/*
 * duel_v3 - TinyBoy 2-player duel over Bluetooth (watch <-> watch) + Joystick Wirelings
 *           + match events over USB for the laptop (see docs/interfaces.md, section 'Match events')
 *
 * Controls (twin-stick): LEFT stick (port 3) = walk + strafe, RIGHT stick (port 0) = turn.
 *           Buttons still work: left buttons = walk, right buttons = turn.
 * Joysticks: Wireling Adapter TinyShield + Joystick Wirelings in ports 0 and 3. Optional -
 *           missing sticks are detected and the watch falls back to buttons.
 * Joystick code based on the TinyCircuits Joystick Wireling tutorial.
 *
 * BOTH watches run this same sketch. At start-up choose a role on the watch:
 *   left buttons  = Player A (blue, hosts / waits for a connection)
 *   right buttons = Player B (red, connects to Player A)
 *
 * Each watch:
 *   - runs the 3D raycaster locally
 *   - sends its own position + hits landed ~20 times a second
 *   - draws the other player as a sprite where the latest packet says
 *   - decides its OWN hits on the opponent (shooter decides), shares the count
 * First to 2 hits wins the round, first to 3 rounds wins the match.
 *
 * Raycasting / sprite projection adapted from Lode Vandevenne's tutorials
 *   https://lodev.org/cgtutor/raycasting.html , https://lodev.org/cgtutor/raycasting3.html
 * BLE adapted from the TinyCircuits ST BLE TinyShield UART example (Ben Rose, TinyCircuits)
 *   and ST's BlueNRG-MS client sample app.  -> credited in docs/ATTRIBUTION.md
 */
#include <Wire.h>
#include <SPI.h>
#include <TinyScreen.h>
#include <STBLE.h>
#include <Wireling.h>
#include <SX1505.h>

// Defined at the very top: the Arduino IDE auto-adds function declarations near the start of
// the file, and readStick() uses this type, so it must exist before them.
struct StickState { bool up, down, left, right; };
#include <math.h>
#include <stdio.h>

#if defined(ARDUINO_ARCH_AVR)
#define SerialMonitorInterface Serial
#elif defined(ARDUINO_ARCH_SAMD)
#define SerialMonitorInterface SerialUSB
#endif

char sprintbuff[100];
#define PRINTF(...) do { sprintf(sprintbuff, __VA_ARGS__); SerialMonitorInterface.print(sprintbuff); } while (0)

TinyScreen display = TinyScreen(TinyScreenPlus);

// =====================================================================
//  Roles and the network packet
// =====================================================================
#define ROLE_A 0   // peripheral: waits for a connection (like ble_uart_test)
#define ROLE_B 1   // central: connects to Player A (like ble_central_test)
uint8_t role = ROLE_A;

// Fixed Bluetooth addresses so Player B knows exactly where Player A is
uint8_t addrA[] = {0x12, 0x34, 0x00, 0xE1, 0x80, 0x0A};
uint8_t addrB[] = {0x12, 0x34, 0x00, 0xE1, 0x80, 0x0B};

// Player A's UART characteristic handles (printed by ble_uart_test: TX 13, RX 15)
#define A_TX_CHAR_HANDLE  13
#define A_RX_CHAR_HANDLE  15
#define A_WRITE_HANDLE    (A_TX_CHAR_HANDLE + 1)  // B writes here  -> A receives
#define A_NOTIFY_HANDLE   (A_RX_CHAR_HANDLE + 1)  // A notifies here -> B receives
#define A_CCCD_HANDLE     (A_RX_CHAR_HANDLE + 2)  // B turns notifications on here

// One game packet = 10 bytes (BLE message limit is 20)
#define PACKET_MAGIC 0xB0
struct __attribute__((packed)) DuelPacket {
  uint8_t magic;       // PACKET_MAGIC, to ignore anything else
  uint8_t seq;         // counts up every packet
  uint8_t round;       // which round this data belongs to
  int16_t x, y;        // position x 256 (fixed point)
  uint8_t angle;       // facing, 0-255 = full circle
  uint8_t hitsLanded;  // how many times I've hit you this round
  uint8_t flags;       // bit 0 = I just fired
  uint8_t shots;       // shots I've fired this round (for accuracy stats)
};
#define FLAG_FIRED 0x01
const unsigned long SEND_INTERVAL_MS = 100;  // 10 packets per second (was 20 - less traffic, fewer clashes)

// =====================================================================
//  Screen, colours, rules
// =====================================================================
const int SCREEN_W = 96;
const int SCREEN_H = 64;
const int CENTER_X = SCREEN_W / 2;
const int CENTER_Y = SCREEN_H / 2;

// 8-bit colour helper (TinyScreen 8-bit format: bbbgggrr)
#define COL8(r, g, b) ((uint8_t)((((b) & 7) << 5) | (((g) & 7) << 2) | ((r) & 3)))

const uint8_t COL_CEILING     = TS_8b_Black;
const uint8_t COL_FLOOR       = TS_8b_Gray;
// Neutral walls, so the blue and red players stand out
const uint8_t COL_STONE_LIGHT = COL8(2, 5, 5);
const uint8_t COL_STONE_DARK  = COL8(1, 3, 3);
const uint8_t COL_WOOD_LIGHT  = COL8(3, 4, 1);
const uint8_t COL_WOOD_DARK   = COL8(2, 2, 0);
const uint8_t COL_HIT_FLASH   = TS_8b_White;
const uint8_t COL_FIRE_FLASH  = TS_8b_Yellow;
const uint8_t COL_CROSSHAIR   = TS_8b_White;
const uint8_t COL_CROSSHAIR_ON_TARGET = TS_8b_Yellow;
const uint8_t COL_GUN         = TS_8b_Black;
const uint8_t COL_MUZZLE      = TS_8b_Yellow;
const uint8_t COL_DAMAGE      = TS_8b_Red;
const uint8_t COL_LINK_OK     = TS_8b_Green;
const uint8_t COL_LINK_BAD    = TS_8b_Red;

// Player colours: A is blue, B is red
uint8_t myColour, myColourDark, oppColour, oppColourDark;

const int MAX_HITS = 2;          // hits to lose a round
const int ROUNDS_TO_WIN = 3;
const unsigned long ROUND_OVER_MS    = 2500;
const unsigned long FIRE_INTERVAL_MS = 400;   // semi-auto continuous fire
const unsigned long MUZZLE_FLASH_MS  = 60;
const unsigned long HIT_FLASH_MS     = 150;
const unsigned long DAMAGE_FLASH_MS  = 250;
const unsigned long OPP_TIMEOUT_MS   = 2000;  // no packets for this long = hide opponent

// =====================================================================
//  Map
// =====================================================================
const int MAP_W = 16;
const int MAP_H = 16;
const uint8_t worldMap[MAP_H][MAP_W] = {
  {1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1},
  {1,0,0,0,0,0,0,1,0,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,0,1,0,0,0,0,0,0,0,1},
  {1,0,0,2,2,0,0,0,0,0,0,2,2,0,0,1},
  {1,0,0,2,0,0,0,0,0,0,0,0,2,0,0,1},
  {1,0,0,0,0,0,1,1,1,1,0,0,0,0,0,1},
  {1,0,0,0,0,0,1,0,0,1,0,0,0,0,0,1},
  {1,1,1,0,0,0,0,0,0,0,0,0,0,1,1,1},
  {1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,1,0,0,1,0,0,0,0,0,1},
  {1,0,0,0,0,0,1,1,1,1,0,0,0,0,0,1},
  {1,0,0,2,0,0,0,0,0,0,0,0,2,0,0,1},
  {1,0,0,2,2,0,0,0,0,0,0,2,2,0,0,1},
  {1,0,0,0,0,0,0,0,1,0,0,0,0,0,0,1},
  {1,0,0,0,0,0,0,0,1,0,0,0,0,0,0,1},
  {1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1},
};

// =====================================================================
//  Game state
// =====================================================================
// Me
float posX, posY, dirX, dirY, planeX, planeY;
int myHitsLanded = 0;   // hits I've landed on the opponent this round
int hitsTaken = 0;      // hits I've taken (comes from the opponent's packet)
int lastHitsTaken = 0;

// Opponent (from the network)
float oppTargetX = -10, oppTargetY = -10;  // latest received position
float oppX = -10, oppY = -10;              // smoothed position we draw
int   oppHitsLanded = 0;
uint8_t oppRound = 0;
unsigned long oppLastPacket = 0;
unsigned long oppFireFlashUntil = 0;
bool  oppHasPosition = false;

// Rounds
enum GameState { STATE_WAITING, STATE_PLAYING, STATE_ROUND_OVER };
GameState state = STATE_WAITING;
unsigned long stateStart = 0;
uint8_t myRound = 0;
int roundsMe = 0, roundsOpp = 0;
bool matchOver = false;

// Effects / timers
unsigned long lastShotTime = 0;
unsigned long muzzleFlashUntil = 0;
unsigned long hitFlashUntil = 0;
unsigned long damageFlashUntil = 0;
unsigned long lastSend = 0;
// Match stats (for the laptop)
int myShots = 0, oppShots = 0;          // shots fired this round
unsigned long roundStartMs = 0;
unsigned long lastPosLog = 0;
bool matchActive = false;
const unsigned long POS_LOG_INTERVAL_MS = 200;  // 5 position samples per second
// Turn-taking: B sends, A replies, B sends again... so only one packet is ever in flight each way
volatile bool replyDue = false;   // A: B sent us a packet, answer it
volatile bool gotReply = true;    // B: A answered our last packet
const unsigned long REPLY_TIMEOUT_MS = 300;  // B: if A's answer got lost, send anyway after this
uint8_t sendSeq = 0;
bool waitingScreenDrawn = false;

// Sprite projection results
const int SPR_TEX_W = 8;
const int SPR_TEX_H = 16;
const uint8_t playerBitmap[SPR_TEX_H] = {
  0b00111100, 0b01111110, 0b01111110, 0b00111100,  // head
  0b00011000,                                      // neck
  0b11111111, 0b11111111, 0b10111101, 0b10111101, 0b10111101,  // body / arms
  0b00111100,                                      // waist
  0b00100100, 0b00100100, 0b00100100, 0b01100110, 0b01100110   // legs / feet
};
bool  sprVisible = false;
int   sprLeft, sprRight, sprTop, sprBottom, sprW, sprH;
float sprDepth;
bool  onTarget = false;

// Raycast results per column
uint8_t wallTop[SCREEN_W], wallBottom[SCREEN_W], wallColour[SCREEN_W];
float   wallDist[SCREEN_W];
// Whole frame is drawn into RAM first (6 KB), then sent to the screen in one burst.
// (Earlier versions paused ALL interrupts 64 times per frame; now nothing is paused while drawing.)
uint8_t frameBuf[SCREEN_W * SCREEN_H];

// Pause / resume ONLY the Bluetooth chip's interrupt (external interrupt controller),
// instead of all interrupts - USB and the millisecond timer keep running.
inline void bleIrqOff() { NVIC_DisableIRQ(EIC_IRQn); __DSB(); __ISB(); }
inline void bleIrqOn()  { NVIC_EnableIRQ(EIC_IRQn); }

// Freeze detector: the code records which step it's on; if the watch freezes,
// the watchdog's early warning prints this step to the Serial Monitor before restarting.
volatile uint8_t stage = 0;
const char *stageName(uint8_t s) {
  switch (s) {
    case 1: return "netLoop (Bluetooth events)";
    case 2: return "sending packet";
    case 3: return "raycasting";
    case 4: return "drawing into RAM";
    case 5: return "sending frame to screen";
    case 6: return "text screen";
    case 7: return "serial print";
    case 8: return "Bluetooth command (netSend)";
    case 9: return "reading joystick";
    default: return "other";
  }
}

// HUD layout
const int PIP_Y_TOP = 1, PIP_Y_BOTTOM = 3, PIP_W = 6, PIP_GAP = 2;
const int OPP_PIP_X0 = 2;
const int MY_PIP_X0 = SCREEN_W - 2 - (MAX_HITS * PIP_W + (MAX_HITS - 1) * PIP_GAP);
const int LINK_X0 = CENTER_X - 1, LINK_X1 = CENTER_X + 1;  // link indicator, top centre
const int DAMAGE_BORDER = 4;
const int GUN_TOP = 56, GUN_LEFT = CENTER_X - 3, GUN_RIGHT = CENTER_X + 3;
const int FLASH_TOP = 51, FLASH_BOTTOM = 55, FLASH_LEFT = CENTER_X - 2, FLASH_RIGHT = CENTER_X + 2;

// =====================================================================
//  BLE layer (both roles)
// =====================================================================
volatile bool netConnected = false;
uint16_t netConnHandle = 0;
bool needAdvertise = false;           // A: start advertising
bool needReconnect = false;           // B: try connecting again
bool notifyOn = false;                // B: notifications from A enabled
unsigned long netConnectedAt = 0, netDisconnectedAt = 0, lastNotifyTry = 0;
uint16_t UARTServHandle, UARTTXCharHandle, UARTRXCharHandle;  // A's own service

#define COPY_UUID_128(uuid_struct, uuid_15, uuid_14, uuid_13, uuid_12, uuid_11, uuid_10, uuid_9, uuid_8, uuid_7, uuid_6, uuid_5, uuid_4, uuid_3, uuid_2, uuid_1, uuid_0) \
  do { uuid_struct[0] = uuid_0; uuid_struct[1] = uuid_1; uuid_struct[2] = uuid_2; uuid_struct[3] = uuid_3; \
       uuid_struct[4] = uuid_4; uuid_struct[5] = uuid_5; uuid_struct[6] = uuid_6; uuid_struct[7] = uuid_7; \
       uuid_struct[8] = uuid_8; uuid_struct[9] = uuid_9; uuid_struct[10] = uuid_10; uuid_struct[11] = uuid_11; \
       uuid_struct[12] = uuid_12; uuid_struct[13] = uuid_13; uuid_struct[14] = uuid_14; uuid_struct[15] = uuid_15; } while (0)
#define COPY_UART_SERVICE_UUID(u) COPY_UUID_128(u, 0x6E,0x40,0x00,0x01,0xB5,0xA3,0xF3,0x93,0xE0,0xA9,0xE5,0x0E,0x24,0xDC,0xCA,0x9E)
#define COPY_UART_TX_CHAR_UUID(u) COPY_UUID_128(u, 0x6E,0x40,0x00,0x02,0xB5,0xA3,0xF3,0x93,0xE0,0xA9,0xE5,0x0E,0x24,0xDC,0xCA,0x9E)
#define COPY_UART_RX_CHAR_UUID(u) COPY_UUID_128(u, 0x6E,0x40,0x00,0x03,0xB5,0xA3,0xF3,0x93,0xE0,0xA9,0xE5,0x0E,0x24,0xDC,0xCA,0x9E)

uint8_t addUartService() {
  uint8_t uuid[16];
  COPY_UART_SERVICE_UUID(uuid);
  if (aci_gatt_add_serv(UUID_TYPE_128, uuid, PRIMARY_SERVICE, 7, &UARTServHandle) != BLE_STATUS_SUCCESS) return BLE_STATUS_ERROR;
  COPY_UART_TX_CHAR_UUID(uuid);
  if (aci_gatt_add_char(UARTServHandle, UUID_TYPE_128, uuid, 20, CHAR_PROP_WRITE_WITHOUT_RESP, ATTR_PERMISSION_NONE,
                        GATT_NOTIFY_ATTRIBUTE_WRITE, 16, 1, &UARTTXCharHandle) != BLE_STATUS_SUCCESS) return BLE_STATUS_ERROR;
  COPY_UART_RX_CHAR_UUID(uuid);
  if (aci_gatt_add_char(UARTServHandle, UUID_TYPE_128, uuid, 20, CHAR_PROP_NOTIFY, ATTR_PERMISSION_NONE,
                        0, 16, 1, &UARTRXCharHandle) != BLE_STATUS_SUCCESS) return BLE_STATUS_ERROR;
  return BLE_STATUS_SUCCESS;
}

void startAdvertising() {  // Player A
  const char local_name[] = {AD_TYPE_COMPLETE_LOCAL_NAME, 'T', 'i', 'n', 'y', 'b', 'o', 'y', 'A'};
  hci_le_set_scan_resp_data(0, NULL);
  tBleStatus ret = aci_gap_set_discoverable(ADV_IND, (50 * 1000) / 625, (100 * 1000) / 625,
                                            PUBLIC_ADDR, NO_WHITE_LIST_USE,
                                            sizeof(local_name), local_name, 0, NULL, 0, 0);
  PRINTF("Advertising as Player A (status %d)\n", ret);
}

void startConnection() {  // Player B
  tBleStatus ret = aci_gap_create_connection(0x0010, 0x0010, PUBLIC_ADDR, addrA, PUBLIC_ADDR,
                                             8, 16, 0, 200, 0, 0);
  PRINTF("Connecting to Player A (status %d)\n", ret);
}

void netBegin() {
  HCI_Init();
  BNRG_SPI_Init();
  BlueNRG_RST();

  aci_hal_write_config_data(CONFIG_DATA_PUBADDR_OFFSET, CONFIG_DATA_PUBADDR_LEN,
                            role == ROLE_A ? addrA : addrB);
  aci_gatt_init();

  uint16_t service_handle, dev_name_char_handle, appearance_char_handle;
  aci_gap_init_IDB05A1(role == ROLE_A ? GAP_PERIPHERAL_ROLE_IDB05A1 : GAP_CENTRAL_ROLE_IDB05A1,
                       0, 0x08, &service_handle, &dev_name_char_handle, &appearance_char_handle);

  if (role == ROLE_A) {
    const char *name = "TinyboyA";
    aci_gatt_update_char_value(service_handle, dev_name_char_handle, 0, strlen(name), (uint8_t *)name);
    if (addUartService() == BLE_STATUS_SUCCESS)
      PRINTF("UART service OK. TX %d RX %d (B expects 13 / 15)\n", UARTTXCharHandle, UARTRXCharHandle);
    else
      PRINTF("UART service FAILED\n");
    needAdvertise = true;
  }
  aci_hal_set_tx_power_level(1, 3);

  if (role == ROLE_B) startConnection();
}

void netLoop() {
  HCI_Process();
  unsigned long now = millis();

  if (role == ROLE_A && needAdvertise && !netConnected) {
    needAdvertise = false;
    startAdvertising();
  }
  if (role == ROLE_B) {
    if (netConnected && !notifyOn && now - netConnectedAt > 300 && now - lastNotifyTry > 200) {
      lastNotifyTry = now;
      uint8_t on[2] = {0x01, 0x00};
      if (aci_gatt_write_charac_descriptor(netConnHandle, A_CCCD_HANDLE, 2, on) == BLE_STATUS_SUCCESS) {
        notifyOn = true;
        PRINTF("Notifications on\n");
      }
    }
    if (needReconnect && now - netDisconnectedAt > 1000) {
      needReconnect = false;
      startConnection();
    }
  }
}

bool netSend(const uint8_t *data, uint8_t len) {
  if (!netConnected) return false;
  tBleStatus ret;
  uint8_t prevStage = stage;
  stage = 8;
  if (role == ROLE_A)
    ret = aci_gatt_update_char_value(UARTServHandle, UARTRXCharHandle, 0, len, (uint8_t *)data);  // notify B
  else
    ret = aci_gatt_write_without_response(netConnHandle, A_WRITE_HANDLE, len, data);            // write to A
  stage = prevStage;
  return ret == BLE_STATUS_SUCCESS;  // if the radio is busy we just skip; the next packet replaces it
}

// Called by the BLE library for every event (runs inside HCI_Process)
void HCI_Event_CB(void *pckt) {
  hci_uart_pckt *hci_pckt = (hci_uart_pckt *)pckt;
  hci_event_pckt *event_pckt = (hci_event_pckt *)hci_pckt->data;
  if (hci_pckt->type != HCI_EVENT_PKT) return;

  switch (event_pckt->evt) {
    case EVT_DISCONN_COMPLETE:
      netConnected = false;
      notifyOn = false;
      netDisconnectedAt = millis();
      if (role == ROLE_A) needAdvertise = true;
      else needReconnect = true;
      PRINTF("Disconnected\n");
      break;

    case EVT_LE_META_EVENT: {
      evt_le_meta_event *evt = (evt_le_meta_event *)event_pckt->data;
      if (evt->subevent == EVT_LE_CONN_COMPLETE) {
        evt_le_connection_complete *cc = (evt_le_connection_complete *)evt->data;
        if (cc->status == 0) {
          netConnected = true;
          netConnHandle = cc->handle;
          netConnectedAt = millis();
          PRINTF("Connected!\n");
        } else if (role == ROLE_B) {
          needReconnect = true;
          netDisconnectedAt = millis();
        }
      }
      break;
    }

    case EVT_VENDOR: {
      evt_blue_aci *blue_evt = (evt_blue_aci *)event_pckt->data;
      switch (blue_evt->ecode) {
        case EVT_BLUE_GATT_READ_PERMIT_REQ:  // A
          if (netConnHandle != 0) aci_gatt_allow_read(netConnHandle);
          break;
        case EVT_BLUE_GATT_ATTRIBUTE_MODIFIED: {  // A: B wrote a packet to us
          evt_gatt_attr_modified_IDB05A1 *m = (evt_gatt_attr_modified_IDB05A1 *)blue_evt->data;
          if (m->attr_handle == UARTTXCharHandle + 1) onNetReceive(m->att_data, m->data_length);
          break;
        }
        case EVT_BLUE_GATT_NOTIFICATION: {  // B: A notified us with a packet
          evt_gatt_attr_notification *n = (evt_gatt_attr_notification *)blue_evt->data;
          if (n->attr_handle == A_NOTIFY_HANDLE) onNetReceive(n->attr_value, n->event_data_length - 2);
          break;
        }
      }
      break;
    }
  }
}

// =====================================================================
//  Packets
// =====================================================================
void onNetReceive(uint8_t *data, uint8_t len) {
  if (len != sizeof(DuelPacket) || data[0] != PACKET_MAGIC) return;
  DuelPacket p;
  memcpy(&p, data, sizeof(p));

  oppTargetX = p.x / 256.0f;
  oppTargetY = p.y / 256.0f;
  if (!oppHasPosition) { oppX = oppTargetX; oppY = oppTargetY; oppHasPosition = true; }
  oppRound = p.round;
  if (p.round == myRound) { oppHitsLanded = p.hitsLanded; oppShots = p.shots; }  // only this round's counts
  if (p.flags & FLAG_FIRED) oppFireFlashUntil = millis() + MUZZLE_FLASH_MS;
  oppLastPacket = millis();
  if (role == ROLE_A) replyDue = true;  // A: answer this packet
  else                gotReply = true;  // B: our turn to send again
}

void sendMyState(unsigned long now) {
  DuelPacket p;
  p.magic = PACKET_MAGIC;
  p.seq = sendSeq++;
  p.round = myRound;
  p.x = (int16_t)(posX * 256);
  p.y = (int16_t)(posY * 256);
  p.angle = (uint8_t)((atan2f(dirY, dirX) + PI) / (2 * PI) * 255);
  p.hitsLanded = myHitsLanded;
  p.flags = (now - lastShotTime < 100) ? FLAG_FIRED : 0;
  p.shots = myShots;
  netSend((uint8_t *)&p, sizeof(p));
}

// =====================================================================
//  Helpers
// =====================================================================
bool isWall(float x, float y) {
  int mx = (int)x, my = (int)y;
  if (mx < 0 || my < 0 || mx >= MAP_W || my >= MAP_H) return true;
  return worldMap[my][mx] != 0;
}

void tryMove(float dx, float dy) {
  const float m = 0.2f;
  if (!isWall(posX + dx + (dx > 0 ? m : -m), posY)) posX += dx;
  if (!isWall(posX, posY + dy + (dy > 0 ? m : -m))) posY += dy;
}

void rotate(float a) {
  float c = cosf(a), s = sinf(a);
  float ox = dirX;
  dirX = dirX * c - dirY * s;  dirY = ox * s + dirY * c;
  float opx = planeX;
  planeX = planeX * c - planeY * s;  planeY = opx * s + planeY * c;
}

// Sound hooks for the buzzer teammate
void playShotSound() {}
void playHitSound() {}
void playHurtSound() {}
void playRoundOverSound(int result) {}

// Text screens (wrapped so a Bluetooth interrupt can't scramble the SPI bus mid-draw)
void drawTextScreen(const char *l1, uint8_t c1, const char *l2, const char *l3) {
  uint8_t prevStage = stage;
  stage = 6;
  bleIrqOff();
  display.clearScreen();
  display.setFont(thinPixel7_10ptFontInfo);
  display.fontColor(c1, TS_8b_Black);
  display.setCursor(4, 8);  display.print(l1);
  display.fontColor(TS_8b_White, TS_8b_Black);
  display.setCursor(4, 26); display.print(l2);
  display.setCursor(4, 42); display.print(l3);
  bleIrqOn();
  stage = prevStage;
}

// =====================================================================
//  Joystick Wirelings (optional) - twin-stick, like a game controller
//    LEFT  stick (MOVE_PORT, port 3): up/down = walk, left/right = strafe
//    RIGHT stick (TURN_PORT, port 0): left/right = turn
//  Both joysticks share one I2C address; the Wireling Adapter switches ports between reads.
// =====================================================================
#define MOVE_PORT 3        // Wireling Adapter port of the movement stick (left side)
#define TURN_PORT 0        // Wireling Adapter port of the turning stick (right side)
#define JOY_SWAP_UD false  // true if pushing the move stick "up" walks you backwards
#define JOY_SWAP_LR false  // true if the move stick strafes the wrong way
#define TURN_SWAP   false  // true if the turn stick turns the wrong way
TinyJoystick joystick = TinyJoystick();
bool moveStickOk = false, turnStickOk = false;

bool stickLooksValid() {  // with nothing plugged in, reads come back as nonsense (e.g. up AND down)
  return !((joystick.up && joystick.down) || (joystick.left && joystick.right));
}

bool stickBegin(uint8_t port) {
  Wireling.selectPort(port);
  joystick.begin();
  joystick.getPosition();
  return stickLooksValid();
}

bool readStick(uint8_t port, StickState &st) {
  Wireling.selectPort(port);
  joystick.getPosition();
  if (!stickLooksValid()) return false;  // ignore glitches
  st.up = joystick.up; st.down = joystick.down; st.left = joystick.left; st.right = joystick.right;
  return true;
}

void joystickBegin() {
  Wire.begin();
  Wireling.begin();
  moveStickOk = stickBegin(MOVE_PORT);
  turnStickOk = stickBegin(TURN_PORT);
  PRINTF("Move stick (port %d): %s | Turn stick (port %d): %s\n",
         MOVE_PORT, moveStickOk ? "found" : "none", TURN_PORT, turnStickOk ? "found" : "none");
}

// Adds joystick input on top of the buttons
void readJoysticks(bool &fwd, bool &back, bool &strafeL, bool &strafeR, bool &turnL, bool &turnR) {
  uint8_t prevStage = stage;
  stage = 9;
  StickState st;
  if (moveStickOk && readStick(MOVE_PORT, st)) {
    fwd     |= JOY_SWAP_UD ? st.down : st.up;
    back    |= JOY_SWAP_UD ? st.up : st.down;
    strafeL |= JOY_SWAP_LR ? st.right : st.left;
    strafeR |= JOY_SWAP_LR ? st.left : st.right;
  }
  if (turnStickOk && readStick(TURN_PORT, st)) {
    turnL |= TURN_SWAP ? st.right : st.left;
    turnR |= TURN_SWAP ? st.left : st.right;
  }
  stage = prevStage;
}

// =====================================================================
//  Match events over USB (read by laptop/bridge.py)
//  One line per event, starting with "EVT,". Every other line (FPS etc.) is ignored by the laptop.
//  Players are always named A and B, so either watch can be the one plugged in.
//  Positions are in hundredths of a map square (250 = 2.50).
// =====================================================================
char meLetter()  { return role == ROLE_A ? 'A' : 'B'; }
char oppLetter() { return role == ROLE_A ? 'B' : 'A'; }

bool evtReady() { return (bool)SerialUSB; }  // only send when the laptop has the port open

void evtStart(const char *type) { SerialUSB.print("EVT,"); SerialUSB.print(type); }
void evtNum(long v)             { SerialUSB.print(','); SerialUSB.print(v); }
void evtChar(char c)            { SerialUSB.print(','); SerialUSB.print(c); }
void evtEnd()                   { SerialUSB.print('\n'); }

void evtHello() {
  if (!evtReady()) return;
  evtStart("HELLO"); evtChar(meLetter()); evtEnd();
}

void evtMatchStart() {
  if (!evtReady()) return;
  evtStart("MATCH_START"); evtChar(meLetter()); evtEnd();
}

void evtRoundStart() {
  if (!evtReady()) return;
  evtStart("ROUND_START"); evtNum(myRound); evtEnd();
}

void evtShot(bool hit) {
  if (!evtReady()) return;
  evtStart("SHOT"); evtChar(meLetter()); evtNum(hit ? 1 : 0); evtEnd();
}

void evtHit(char shooter, char target, int hpLeft) {
  if (!evtReady()) return;
  evtStart("HIT"); evtChar(shooter); evtChar(target); evtNum(hpLeft); evtEnd();
}

void evtPos() {
  if (!evtReady()) return;
  // Always A first, then B, whichever watch is talking
  long mx = (long)(posX * 100), my = (long)(posY * 100);
  long ox = (long)(oppX * 100), oy = (long)(oppY * 100);
  bool oppKnown = oppHasPosition;
  evtStart("POS");
  if (role == ROLE_A) {
    evtChar('A'); evtNum(mx); evtNum(my);
    if (oppKnown) { evtChar('B'); evtNum(ox); evtNum(oy); }
  } else {
    if (oppKnown) { evtChar('A'); evtNum(ox); evtNum(oy); }
    evtChar('B'); evtNum(mx); evtNum(my);
  }
  evtEnd();
}

void evtRoundEnd(int result, unsigned long now) {
  if (!evtReady()) return;
  char winner = result > 0 ? meLetter() : (result < 0 ? oppLetter() : 'D');  // D = draw
  int hitsA  = role == ROLE_A ? myHitsLanded : oppHitsLanded;
  int hitsB  = role == ROLE_A ? oppHitsLanded : myHitsLanded;
  int shotsA = role == ROLE_A ? myShots : oppShots;
  int shotsB = role == ROLE_A ? oppShots : myShots;
  evtStart("ROUND_END"); evtNum(myRound); evtChar(winner);
  evtNum(hitsA); evtNum(hitsB); evtNum(shotsA); evtNum(shotsB); evtNum(now - roundStartMs);
  evtEnd();
}

void evtMatchEnd() {
  if (!evtReady()) return;
  char winner = roundsMe >= ROUNDS_TO_WIN ? meLetter() : oppLetter();
  int roundsA = role == ROLE_A ? roundsMe : roundsOpp;
  int roundsB = role == ROLE_A ? roundsOpp : roundsMe;
  evtStart("MATCH_END"); evtChar(winner); evtNum(roundsA); evtNum(roundsB); evtEnd();
}

void evtMatchAbort() {
  if (!evtReady()) return;
  evtStart("MATCH_ABORT"); evtEnd();
}

// =====================================================================
//  Rounds
// =====================================================================
void startRound(unsigned long now) {
  if (matchOver) { roundsMe = 0; roundsOpp = 0; matchOver = false; }
  myRound++;  // both watches count rounds the same way
  if (!matchActive) { matchActive = true; evtMatchStart(); }
  evtRoundStart();
  myShots = oppShots = 0;
  roundStartMs = now;

  if (role == ROLE_A) {  // top-left corner, facing east
    posX = 2.5f; posY = 2.5f; dirX = 1; dirY = 0; planeX = 0; planeY = 0.66f;
  } else {               // bottom-right corner, facing west
    posX = 13.5f; posY = 13.5f; dirX = -1; dirY = 0; planeX = 0; planeY = -0.66f;
  }
  myHitsLanded = 0;
  hitsTaken = lastHitsTaken = 0;
  oppHitsLanded = 0;
  lastShotTime = now;
  muzzleFlashUntil = hitFlashUntil = damageFlashUntil = 0;
  state = STATE_PLAYING;
}

void endRound(int result, unsigned long now) {  // 1 = I won, -1 = I lost, 0 = draw
  if (result > 0) roundsMe++;
  if (result < 0) roundsOpp++;
  matchOver = (roundsMe >= ROUNDS_TO_WIN || roundsOpp >= ROUNDS_TO_WIN);
  evtRoundEnd(result, now);
  if (matchOver) { evtMatchEnd(); matchActive = false; }
  // myRound stays the same during the round-over screen, so we keep re-sending the
  // final hit count for this round - the other watch gets it even if a packet is lost
  playRoundOverSound(result);
  state = STATE_ROUND_OVER;
  stateStart = now;

  char score[24];
  sprintf(score, "You %d - %d Them", roundsMe, roundsOpp);
  const char *title = result > 0 ? "ROUND WON" : (result < 0 ? "ROUND LOST" : "DRAW");
  uint8_t colour = result > 0 ? TS_8b_Green : (result < 0 ? TS_8b_Red : TS_8b_Yellow);
  const char *bottom = matchOver ? (roundsMe >= ROUNDS_TO_WIN ? "MATCH WON!" : "MATCH LOST") : "Next round...";
  drawTextScreen(title, colour, score, bottom);
}

void enterWaiting() {
  if (matchActive) { evtMatchAbort(); matchActive = false; }
  state = STATE_WAITING;
  waitingScreenDrawn = false;
  oppHasPosition = false;
}

// =====================================================================
//  Raycasting + sprite
// =====================================================================
void castRays() {
  for (int x = 0; x < SCREEN_W; x++) {
    float cameraX = 2.0f * x / SCREEN_W - 1.0f;
    float rdx = dirX + planeX * cameraX, rdy = dirY + planeY * cameraX;
    int mapX = (int)posX, mapY = (int)posY;
    float ddx = (rdx == 0) ? 1e30f : fabsf(1.0f / rdx);
    float ddy = (rdy == 0) ? 1e30f : fabsf(1.0f / rdy);
    int stepX, stepY; float sideX, sideY;
    if (rdx < 0) { stepX = -1; sideX = (posX - mapX) * ddx; } else { stepX = 1; sideX = (mapX + 1.0f - posX) * ddx; }
    if (rdy < 0) { stepY = -1; sideY = (posY - mapY) * ddy; } else { stepY = 1; sideY = (mapY + 1.0f - posY) * ddy; }
    int side = 0;
    while (worldMap[mapY][mapX] == 0) {
      if (sideX < sideY) { sideX += ddx; mapX += stepX; side = 0; }
      else               { sideY += ddy; mapY += stepY; side = 1; }
    }
    float dist = (side == 0) ? (sideX - ddx) : (sideY - ddy);
    if (dist < 0.05f) dist = 0.05f;
    wallDist[x] = dist;
    int h = (int)(SCREEN_H / dist);
    int top = CENTER_Y - h / 2, bottom = CENTER_Y + h / 2;
    wallTop[x] = top < 0 ? 0 : top;
    wallBottom[x] = bottom > SCREEN_H ? SCREEN_H : bottom;
    if (worldMap[mapY][mapX] == 2) wallColour[x] = side ? COL_WOOD_DARK : COL_WOOD_LIGHT;
    else                           wallColour[x] = side ? COL_STONE_DARK : COL_STONE_LIGHT;
  }
}

void projectOpponent(unsigned long now) {
  sprVisible = false;
  onTarget = false;
  if (!oppHasPosition || now - oppLastPacket > OPP_TIMEOUT_MS) return;

  float relX = oppX - posX, relY = oppY - posY;
  float invDet = 1.0f / (planeX * dirY - dirX * planeY);
  float camX = invDet * (dirY * relX - dirX * relY);
  float camY = invDet * (-planeY * relX + planeX * relY);
  if (camY <= 0.2f) return;

  sprDepth = camY;
  int screenX = (int)(CENTER_X * (1.0f + camX / camY));
  int fullH = (int)(SCREEN_H / camY);
  sprH = (int)(fullH * 0.8f);
  if (sprH < 2) return;
  sprW = sprH / 2; if (sprW < 1) sprW = 1;
  sprBottom = CENTER_Y + fullH / 2;
  sprTop = sprBottom - sprH;
  sprLeft = screenX - sprW / 2;
  sprRight = sprLeft + sprW;
  if (sprRight < 0 || sprLeft >= SCREEN_W) return;
  sprVisible = true;

  if (CENTER_X >= sprLeft && CENTER_X < sprRight && CENTER_Y >= sprTop && CENTER_Y < sprBottom &&
      sprDepth < wallDist[CENTER_X]) {
    int tx = (CENTER_X - sprLeft) * SPR_TEX_W / sprW;
    int ty = (CENTER_Y - sprTop) * SPR_TEX_H / sprH;
    onTarget = (playerBitmap[ty] >> (7 - tx)) & 1;
  }
}

// =====================================================================
//  Drawing
// =====================================================================
bool isCrosshairPixel(int x, int y) {
  if (y == CENTER_Y && (x == CENTER_X - 3 || x == CENTER_X - 2 || x == CENTER_X + 2 || x == CENTER_X + 3)) return true;
  if (x == CENTER_X && (y == CENTER_Y - 3 || y == CENTER_Y - 2 || y == CENTER_Y + 2 || y == CENTER_Y + 3)) return true;
  return false;
}

bool pipPixel(int x, int x0, int left, uint8_t full, uint8_t empty, uint8_t &c) {
  int rel = x - x0;
  if (rel < 0) return false;
  int pip = rel / (PIP_W + PIP_GAP), in = rel % (PIP_W + PIP_GAP);
  if (pip >= MAX_HITS || in >= PIP_W) return false;
  c = (pip < left) ? full : empty;
  return true;
}

void drawFrame(unsigned long now) {
  uint8_t crossCol = onTarget ? COL_CROSSHAIR_ON_TARGET : COL_CROSSHAIR;
  uint8_t sprCol = oppColour;
  if (now < oppFireFlashUntil) sprCol = COL_FIRE_FLASH;
  if (now < hitFlashUntil)     sprCol = COL_HIT_FLASH;
  bool muzzleOn = now < muzzleFlashUntil;
  bool damageOn = now < damageFlashUntil;
  bool linkOk = netConnected && (now - oppLastPacket < 500);
  int oppHealth = MAX_HITS - myHitsLanded;
  int myHealth = MAX_HITS - hitsTaken;

  // 1) Draw the whole frame into RAM - no SPI involved, Bluetooth can run freely
  stage = 4;
  for (int y = 0; y < SCREEN_H; y++) {
    uint8_t *lineBuf = frameBuf + y * SCREEN_W;
    bool spriteRow = sprVisible && y >= sprTop && y < sprBottom;
    int texY = spriteRow ? (y - sprTop) * SPR_TEX_H / sprH : 0;
    bool crossRow = y >= CENTER_Y - 3 && y <= CENTER_Y + 3;
    bool hudRow = y >= PIP_Y_TOP && y <= PIP_Y_BOTTOM;
    bool gunRow = y >= GUN_TOP;
    bool flashRow = muzzleOn && y >= FLASH_TOP && y <= FLASH_BOTTOM;
    bool dmgRow = damageOn && (y < DAMAGE_BORDER || y >= SCREEN_H - DAMAGE_BORDER);

    for (int x = 0; x < SCREEN_W; x++) {
      uint8_t c;
      if (y < wallTop[x])         c = COL_CEILING;
      else if (y < wallBottom[x]) c = wallColour[x];
      else                        c = COL_FLOOR;

      if (spriteRow && x >= sprLeft && x < sprRight && sprDepth < wallDist[x]) {
        int texX = (x - sprLeft) * SPR_TEX_W / sprW;
        if ((playerBitmap[texY] >> (7 - texX)) & 1) c = sprCol;
      }
      if (gunRow && x >= GUN_LEFT && x <= GUN_RIGHT) c = COL_GUN;
      if (flashRow && x >= FLASH_LEFT && x <= FLASH_RIGHT) c = COL_MUZZLE;
      if (crossRow && isCrosshairPixel(x, y)) c = crossCol;
      if (damageOn && (dmgRow || x < DAMAGE_BORDER || x >= SCREEN_W - DAMAGE_BORDER)) c = COL_DAMAGE;
      if (hudRow) {
        pipPixel(x, OPP_PIP_X0, oppHealth, oppColour, oppColourDark, c);
        pipPixel(x, MY_PIP_X0, myHealth, myColour, myColourDark, c);
        if (x >= LINK_X0 && x <= LINK_X1) c = linkOk ? COL_LINK_OK : COL_LINK_BAD;
      }
      lineBuf[x] = c;
    }
  }

  // 2) Send it to the screen in one burst, with only the Bluetooth interrupt paused (~5 ms)
  stage = 5;
  bleIrqOff();
  display.setX(0, SCREEN_W - 1);
  display.setY(0, SCREEN_H - 1);
  display.startData();
  display.writeBuffer(frameBuf, SCREEN_W * SCREEN_H);
  display.endTransfer();
  bleIrqOn();
}

// =====================================================================
//  Watchdog: if loop() stops running for ~2 s, the chip restarts itself
//  (safety net so a frozen watch recovers on its own)
// =====================================================================
bool wokeFromFreeze = false;

void watchdogStart() {
  // Clock the watchdog from the slow 32 kHz oscillator, divided down to 1024 Hz (generic clock 2)
  GCLK->GENDIV.reg = GCLK_GENDIV_ID(2) | GCLK_GENDIV_DIV(4);
  GCLK->GENCTRL.reg = GCLK_GENCTRL_ID(2) | GCLK_GENCTRL_GENEN |
                      GCLK_GENCTRL_SRC_OSCULP32K | GCLK_GENCTRL_DIVSEL;
  while (GCLK->STATUS.bit.SYNCBUSY);
  GCLK->CLKCTRL.reg = GCLK_CLKCTRL_ID_WDT | GCLK_CLKCTRL_CLKEN | GCLK_CLKCTRL_GEN_GCLK2;

  WDT->CTRL.reg = 0;
  while (WDT->STATUS.bit.SYNCBUSY);
  WDT->CONFIG.reg = WDT_CONFIG_PER_2K;        // 2048 ticks at 1024 Hz = restart after 2 s
  WDT->EWCTRL.reg = WDT_EWCTRL_EWOFFSET_1K;   // early warning after 1 s (freeze detector)
  WDT->INTENSET.reg = WDT_INTENSET_EW;
  NVIC_SetPriority(WDT_IRQn, 0);
  NVIC_EnableIRQ(WDT_IRQn);
  WDT->CTRL.reg = WDT_CTRL_ENABLE;
  while (WDT->STATUS.bit.SYNCBUSY);
}

void watchdogKick() {
  if (!WDT->STATUS.bit.SYNCBUSY) WDT->CLEAR.reg = WDT_CLEAR_CLEAR_KEY;
}

// Early warning: the loop has been stuck for 1 s. Report where, then let the watchdog restart us.
// (If nothing gets printed at all, the freeze happened with interrupts switched off.)
extern "C" void WDT_Handler(void) {
  WDT->INTFLAG.reg = WDT_INTFLAG_EW;
  SerialUSB.print("\n!!! FROZE during step ");
  SerialUSB.print(stage);
  SerialUSB.print(": ");
  SerialUSB.println(stageName(stage));
}

// =====================================================================
//  Setup: choose role
// =====================================================================
void chooseRole() {
  drawTextScreen(wokeFromFreeze ? "RECOVERED - pick:" : "TINYBOY DUEL", TS_8b_Yellow,
                 "Left btn: Player A", "Right btn: Player B");
  while (true) {
    uint8_t b = display.getButtons();
    if (b & (TSButtonUpperLeft | TSButtonLowerLeft))   { role = ROLE_A; break; }
    if (b & (TSButtonUpperRight | TSButtonLowerRight)) { role = ROLE_B; break; }
    delay(10);
  }
  while (display.getButtons()) delay(10);  // wait for release

  if (role == ROLE_A) {
    myColour = TS_8b_Blue; myColourDark = TS_8b_DarkBlue; oppColour = TS_8b_Red;  oppColourDark = TS_8b_DarkRed;
  } else {
    myColour = TS_8b_Red;  myColourDark = TS_8b_DarkRed;  oppColour = TS_8b_Blue; oppColourDark = TS_8b_DarkBlue;
  }
}

void setup() {
  display.begin();
  display.setFlip(true);
  display.setBrightness(10);
  SerialMonitorInterface.begin(9600);  // optional: no waiting, so it runs on battery
  randomSeed(micros());

  wokeFromFreeze = (PM->RCAUSE.reg & PM_RCAUSE_WDT) != 0;  // did the watchdog restart us?
  chooseRole();
  PRINTF("Role: Player %c%s\n", role == ROLE_A ? 'A' : 'B',
         wokeFromFreeze ? " (restarted after a freeze)" : "");
  joystickBegin();
  netBegin();
  enterWaiting();
  watchdogStart();  // from here on, a freeze restarts the watch within ~2 s
}

// =====================================================================
//  Main loop
// =====================================================================
unsigned long lastFrame = 0, lastFpsTime = 0;
int frames = 0;

void loop() {
  unsigned long now = millis();
  float dt = (now - lastFrame) / 1000.0f;
  lastFrame = now;
  if (dt > 0.1f) dt = 0.1f;

  watchdogKick();  // "still alive" - if this stops happening, the watchdog restarts us
  stage = 1;
  netLoop();
  stage = 0;

  // ---------- Waiting for the other player ----------
  if (state == STATE_WAITING) {
    if (!waitingScreenDrawn) {
      if (role == ROLE_A) drawTextScreen("PLAYER A (blue)", TS_8b_Blue, "Waiting for B...", "Hold btn: back");
      else                drawTextScreen("PLAYER B (red)", TS_8b_Red, "Connecting to A", "Hold btn: back");
      waitingScreenDrawn = true;
    }
    // BACK: hold any button for 1 second -> restart the watch and pick the role again
    static unsigned long holdStart = 0;
    if (display.getButtons()) {
      if (holdStart == 0) holdStart = now;
      if (now - holdStart >= 1000) {
        drawTextScreen("Going back...", TS_8b_Yellow, "", "");
        while (display.getButtons()) delay(10);  // wait for release
        NVIC_SystemReset();                       // full restart, back to the role screen
      }
    } else {
      holdStart = 0;
    }
    if (netConnected) {
      gotReply = true;    // B speaks first
      replyDue = false;
      myRound = 0; roundsMe = 0; roundsOpp = 0; matchOver = false;
      matchActive = false;
      evtHello();
      startRound(now);
    }
    return;
  }

  // Lost the connection mid-game
  if (!netConnected) { enterWaiting(); return; }

  // Keep sending our state in every in-game state, so the other side stays in sync
  // Take turns: A only answers B's packets; B sends when A has answered (or the answer got lost)
  bool sendNow;
  if (role == ROLE_A) sendNow = replyDue;
  else sendNow = (gotReply && now - lastSend >= SEND_INTERVAL_MS) || (now - lastSend >= REPLY_TIMEOUT_MS);
  if (sendNow) {
    lastSend = now;
    replyDue = false;
    gotReply = false;
    stage = 1;
    HCI_Process();       // empty the receive queue first, so the chip's reply always has room
    stage = 2;
    sendMyState(now);
    stage = 0;
  }

  // ---------- Round over screen ----------
  if (state == STATE_ROUND_OVER) {
    if (now - stateStart >= ROUND_OVER_MS) startRound(now);
    return;
  }

  // ---------- Playing ----------
  // If the other side already moved on to a later round, catch up
  if (oppRound > myRound) { myRound = oppRound - 1; startRound(now); }

  // Controls
  uint8_t b = display.getButtons();
  float move = 2.5f * dt, turn = 2.5f * dt;
  bool fwd = b & TSButtonUpperLeft, back = b & TSButtonLowerLeft;
  bool turnL = b & TSButtonUpperRight, turnR = b & TSButtonLowerRight;
  bool strafeL = false, strafeR = false;
  readJoysticks(fwd, back, strafeL, strafeR, turnL, turnR);
  if (fwd)  tryMove(dirX * move, dirY * move);
  if (back) tryMove(-dirX * move, -dirY * move);
  // Strafe = move sideways, along the camera plane (perpendicular to where you face)
  float sx = planeX / 0.66f, sy = planeY / 0.66f;
  float strafe = move * 0.8f;
  if (strafeR) tryMove(sx * strafe, sy * strafe);
  if (strafeL) tryMove(-sx * strafe, -sy * strafe);
  if (turnL) rotate(-turn);
  if (turnR) rotate(turn);

  // Smoothly slide the opponent toward their latest received position
  float k = dt * 12.0f; if (k > 1) k = 1;
  oppX += (oppTargetX - oppX) * k;
  oppY += (oppTargetY - oppY) * k;

  stage = 3;
  castRays();
  projectOpponent(now);
  stage = 0;

  // Semi-auto fire; the shooter decides whether it hit
  if (now - lastShotTime >= FIRE_INTERVAL_MS) {
    lastShotTime = now;
    muzzleFlashUntil = now + MUZZLE_FLASH_MS;
    playShotSound();
    myShots++;
    bool hit = onTarget && myHitsLanded < MAX_HITS;
    evtShot(hit);
    if (hit) {
      myHitsLanded++;
      hitFlashUntil = now + HIT_FLASH_MS;
      playHitSound();
      evtHit(meLetter(), oppLetter(), MAX_HITS - myHitsLanded);
    }
  }

  // Damage I've taken comes from the opponent's count
  hitsTaken = oppHitsLanded;
  if (hitsTaken > lastHitsTaken) {
    damageFlashUntil = now + DAMAGE_FLASH_MS;
    playHurtSound();
    for (int h = lastHitsTaken + 1; h <= hitsTaken; h++) evtHit(oppLetter(), meLetter(), MAX_HITS - h);
    lastHitsTaken = hitsTaken;
  }

  // Round result (both sides reach the same answer from the same counters)
  bool iWon = myHitsLanded >= MAX_HITS;
  bool iLost = hitsTaken >= MAX_HITS;
  if (iWon || iLost) {
    // (no extra send here - the normal turn-taking keeps sending this round's final hit count
    //  during the round-over screen, so the other watch always gets it)
    endRound(iWon && iLost ? 0 : (iWon ? 1 : -1), now);
    return;
  }

  if (now - lastPosLog >= POS_LOG_INTERVAL_MS) { lastPosLog = now; stage = 7; evtPos(); stage = 0; }

  drawFrame(now);
  stage = 0;

  frames++;
  if (now - lastFpsTime >= 1000) {
    stage = 7;
    PRINTF("FPS %d | me %dhp opp %dhp | rounds %d-%d | link %s\n", frames,
           MAX_HITS - hitsTaken, MAX_HITS - myHitsLanded, roundsMe, roundsOpp,
           (now - oppLastPacket < 500) ? "ok" : "stale");
    frames = 0;
    lastFpsTime = now;
    stage = 0;
  }
}
