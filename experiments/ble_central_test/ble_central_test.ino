//-------------------------------------------------------------------------------
//  ble_central_test - Watch B (CENTRAL) for TinyBoy watch-to-watch BLE
//
//  Watch A runs experiments/ble_uart_test (the TinyCircuits UART example) as the
//  PERIPHERAL. This sketch makes Watch B do what the nRF Connect app did:
//    1. connect directly to Watch A's Bluetooth address (no scanning)
//    2. send text typed in this Serial Monitor -> appears in Watch A's Serial Monitor
//    3. turn on notifications -> text typed in Watch A's Serial Monitor appears here
//  BLE setup adapted from the TinyCircuits ST BLE TinyShield UART example (Ben Rose,
//  TinyCircuits) and ST's BlueNRG-MS client sample app. -> credit in docs/ATTRIBUTION.md
//-------------------------------------------------------------------------------

#include <SPI.h>
#include <STBLE.h>

#if defined (ARDUINO_ARCH_AVR)
#define SerialMonitorInterface Serial
#elif defined(ARDUINO_ARCH_SAMD)
#define SerialMonitorInterface SerialUSB
#endif

#include <stdio.h>
char sprintbuff[100];
// do { } while (0) so PRINTF works safely inside if/else
#define PRINTF(...) do { sprintf(sprintbuff, __VA_ARGS__); SerialMonitorInterface.print(sprintbuff); } while (0)

// ======================= FILL THESE IN =======================
// Watch A's address: must match bdaddr[] in Watch A's UART.ino exactly
uint8_t watchA_addr[] = {0x12, 0x34, 0x00, 0xE1, 0x80, 0x0A};

// Watch B's own address: must be different from Watch A's
uint8_t my_addr[]     = {0x12, 0x34, 0x00, 0xE1, 0x80, 0x0B};

// Watch A prints these two numbers when it starts up:
//   "TX char handle: X   RX char handle: Y"
// Put X and Y here. (0 = not filled in yet)
#define WATCH_A_TX_CHAR_HANDLE  13   // printed by Watch A
#define WATCH_A_RX_CHAR_HANDLE  15   // printed by Watch A
// =============================================================

// Derived handles (BLE layout: declaration handle, then value, then config descriptor)
#define A_WRITE_HANDLE   (WATCH_A_TX_CHAR_HANDLE + 1)  // we write here -> Watch A receives it
#define A_NOTIFY_HANDLE  (WATCH_A_RX_CHAR_HANDLE + 1)  // Watch A sends to us from here
#define A_CCCD_HANDLE    (WATCH_A_RX_CHAR_HANDLE + 2)  // write 0x0001 here to switch notifications on

// Connection settings
#define SCAN_INTERVAL     0x0010   // x 0.625 ms = 10 ms
#define SCAN_WINDOW       0x0010
#define CONN_INTERVAL_MIN 8        // x 1.25 ms = 10 ms (short = fast packets, good for games)
#define CONN_INTERVAL_MAX 16       // x 1.25 ms = 20 ms
#define SUPERVISION_TO    200      // x 10 ms = 2 s without contact = disconnected

// State
volatile bool connected = false;
volatile bool notifyOn = false;
uint16_t connHandle = 0;
unsigned long connectedAt = 0;
unsigned long lastNotifyTry = 0;
bool needReconnect = false;
unsigned long disconnectedAt = 0;

char inputBuf[21];   // BLE UART messages are max 20 bytes
uint8_t inputLen = 0;

// ---------------------------------------------------------------
void setup() {
  SerialMonitorInterface.begin(9600);
  while (!SerialMonitorInterface);   // wait for the Serial Monitor to open

  if (WATCH_A_TX_CHAR_HANDLE == 0 || WATCH_A_RX_CHAR_HANDLE == 0) {
    PRINTF("Fill in WATCH_A_TX_CHAR_HANDLE and WATCH_A_RX_CHAR_HANDLE first!\n");
    PRINTF("(Watch A prints them when it starts up.)\n");
    while (1);
  }

  BLEsetupCentral();
  startConnection();
}

void loop() {
  HCI_Process();

  // After connecting, switch on notifications from Watch A (retry until it's accepted)
  if (connected && !notifyOn &&
      millis() - connectedAt > 300 && millis() - lastNotifyTry > 200) {
    lastNotifyTry = millis();
    enableNotifications();
  }

  // Reconnect a second after a disconnect
  if (needReconnect && millis() - disconnectedAt > 1000) {
    needReconnect = false;
    startConnection();
  }

  // Send each line typed into this Serial Monitor to Watch A
  while (SerialMonitorInterface.available()) {
    char c = SerialMonitorInterface.read();
    if (c == '\n' || c == '\r') {
      if (inputLen > 0) {
        sendToWatchA((uint8_t *)inputBuf, inputLen);
        inputLen = 0;
      }
    } else if (inputLen < 20) {
      inputBuf[inputLen++] = c;
    }
  }
}

// ---------------------------------------------------------------
void BLEsetupCentral() {
  int ret;

  HCI_Init();
  BNRG_SPI_Init();
  BlueNRG_RST();

  ret = aci_hal_write_config_data(CONFIG_DATA_PUBADDR_OFFSET, CONFIG_DATA_PUBADDR_LEN, my_addr);
  if (ret) PRINTF("Setting BD_ADDR failed.\n");

  ret = aci_gatt_init();
  if (ret) PRINTF("GATT_Init failed.\n");

  uint16_t service_handle, dev_name_char_handle, appearance_char_handle;
  // CENTRAL role: this watch starts connections (Watch A uses PERIPHERAL)
  ret = aci_gap_init_IDB05A1(GAP_CENTRAL_ROLE_IDB05A1, 0, 0x07,
                             &service_handle, &dev_name_char_handle, &appearance_char_handle);
  if (ret) PRINTF("GAP_Init failed.\n");
  else     PRINTF("BLE central initialized.\n");

  aci_hal_set_tx_power_level(1, 3);
}

void startConnection() {
  PRINTF("Connecting to Watch A %02X:%02X:%02X:%02X:%02X:%02X ...\n",
         watchA_addr[5], watchA_addr[4], watchA_addr[3],
         watchA_addr[2], watchA_addr[1], watchA_addr[0]);

  tBleStatus ret = aci_gap_create_connection(
      SCAN_INTERVAL, SCAN_WINDOW,
      PUBLIC_ADDR, watchA_addr,     // who to connect to
      PUBLIC_ADDR,                  // our own address type
      CONN_INTERVAL_MIN, CONN_INTERVAL_MAX,
      0,                            // latency
      SUPERVISION_TO,
      0, 0);                        // connection event length (let the chip decide)

  if (ret != BLE_STATUS_SUCCESS) PRINTF("create_connection failed: 0x%02X\n", ret);
}

void enableNotifications() {
  uint8_t on[2] = {0x01, 0x00};
  tBleStatus ret = aci_gatt_write_charac_descriptor(connHandle, A_CCCD_HANDLE, 2, on);
  if (ret == BLE_STATUS_SUCCESS) {
    notifyOn = true;
    PRINTF("Notifications ON - Watch A can now send to us.\n");
  }
}

void sendToWatchA(uint8_t *data, uint8_t len) {
  if (!connected) {
    PRINTF("Not connected yet.\n");
    return;
  }
  tBleStatus ret = aci_gatt_write_without_response(connHandle, A_WRITE_HANDLE, len, data);
  if (ret == BLE_STATUS_SUCCESS) {
    PRINTF("Sent to A: ");
    SerialMonitorInterface.write(data, len);
    SerialMonitorInterface.println();
  } else {
    PRINTF("Send failed: 0x%02X\n", ret);
  }
}

// ---------------------------------------------------------------
// Events from the BLE chip arrive here (called from HCI_Process)
void HCI_Event_CB(void *pckt) {
  hci_uart_pckt *hci_pckt = (hci_uart_pckt *)pckt;
  hci_event_pckt *event_pckt = (hci_event_pckt *)hci_pckt->data;

  if (hci_pckt->type != HCI_EVENT_PKT) return;

  switch (event_pckt->evt) {

    case EVT_DISCONN_COMPLETE:
      connected = false;
      notifyOn = false;
      PRINTF("Disconnected.\n");
      needReconnect = true;
      disconnectedAt = millis();
      break;

    case EVT_LE_META_EVENT: {
      evt_le_meta_event *evt = (evt_le_meta_event *)event_pckt->data;
      if (evt->subevent == EVT_LE_CONN_COMPLETE) {
        evt_le_connection_complete *cc = (evt_le_connection_complete *)evt->data;
        if (cc->status == 0) {
          connected = true;
          connHandle = cc->handle;
          connectedAt = millis();
          PRINTF("Connected to Watch A! (handle %d)\n", connHandle);
          PRINTF("Type a message (max 20 chars) and press Enter to send.\n");
        } else {
          PRINTF("Connection failed, status 0x%02X\n", cc->status);
          needReconnect = true;
          disconnectedAt = millis();
        }
      }
      break;
    }

    case EVT_VENDOR: {
      evt_blue_aci *blue_evt = (evt_blue_aci *)event_pckt->data;
      if (blue_evt->ecode == EVT_BLUE_GATT_NOTIFICATION) {
        evt_gatt_attr_notification *n = (evt_gatt_attr_notification *)blue_evt->data;
        if (n->attr_handle == A_NOTIFY_HANDLE) {
          uint8_t len = n->event_data_length - 2;  // length includes the 2-byte handle
          PRINTF("From A: ");
          SerialMonitorInterface.write(n->attr_value, len);
          SerialMonitorInterface.println();
        }
      }
      break;
    }
  }
}
