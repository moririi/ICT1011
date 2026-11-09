"""
ble_bridge_test.py - laptop <-> watch Bluetooth test (first step of ARENA mode)

The laptop does what the nRF Connect app / Watch B did: it connects to a watch
running experiments/ble_uart_test, sends text, and prints what the watch sends back.

Setup (once):
    pip install bleak

Run (watch must be running ble_uart_test, with its Serial Monitor open):
    python ble_bridge_test.py          # connects to Watch A (TinyboyA)
    python ble_bridge_test.py B        # or another known watch
    python ble_bridge_test.py 0A:80:E1:00:34:12   # or any address

Then type a message (max 20 characters) and press Enter -> it appears in the
watch's Serial Monitor. Type in the watch's Serial Monitor -> it appears here.
Ctrl+C to quit.

Note: close nRF Connect / power off any other watch acting as central first -
a peripheral watch accepts only one connection at a time.
"""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

# Nordic UART service (same as the TinyCircuits example)
UART_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # laptop -> watch
UART_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # watch -> laptop

# Known watches. Addresses read "backwards" compared with the bdaddr[] array in the code.
WATCHES = {
    "A": ("0A:80:E1:00:34:12", "TinyboyA"),
    "B": ("0B:80:E1:00:34:12", "TinyboyB"),
}


async def find_watch(target: str):
    """Find the watch by address first, then by advertised name."""
    address, name = WATCHES.get(target.upper(), (target, None))
    print(f"Scanning for {address} ...")
    device = await BleakScanner.find_device_by_address(address, timeout=10.0)
    if device is None and name:
        print(f"Not found by address, trying name '{name}' ...")
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name or ad.local_name) == name, timeout=10.0
        )
    return device


def on_notify(_sender, data: bytearray):
    text = data.decode(errors="replace").rstrip("\r\n")
    print(f"\n[watch]  {text!r}   ({len(data)} bytes: {data.hex(' ')})")


async def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "A"
    device = await find_watch(target)
    if device is None:
        print("Watch not found. Is it running ble_uart_test with its Serial Monitor open?")
        return

    print(f"Found {device.name} ({device.address}), connecting ...")
    async with BleakClient(device) as client:
        await client.start_notify(UART_NOTIFY, on_notify)
        print("Connected! Type a message (max 20 chars) and press Enter. Ctrl+C to quit.\n")

        loop = asyncio.get_running_loop()
        while client.is_connected:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            msg = line.rstrip("\r\n").encode()[:20]  # BLE UART limit: 20 bytes
            if msg:
                await client.write_gatt_char(UART_WRITE, msg, response=False)
                print(f"[laptop] sent {msg!r}")

    print("Disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye")
