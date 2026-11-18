# Attribution

Third-party code, tutorials and libraries used in TinyBoy / Project Arena.
Code adapted from any of these is also credited in a comment at the top of the source file.

## Code adapted from tutorials and examples

- **Raycasting engine** (`firmware/tinyboy/tinyboy.ino`, `experiments/raycaster_v1`, `raycasterv2`, `raycaster_v3`, `raycaster_v4`, `duel_v1`, `duel_v2`, `duel_v3`):
  DDA grid stepping, camera plane and sprite projection adapted from Lode Vandevenne's raycasting tutorials —
  https://lodev.org/cgtutor/raycasting.html and https://lodev.org/cgtutor/raycasting3.html
- **BLE UART example** (`experiments/ble_uart_test`): TinyCircuits ST BLE TinyShield UART example by Ben Rose, TinyCircuits —
  https://learn.tinycircuits.com/Communication/Bluetooth-Low-Energy_TinyShield_Tutorial/
- **BLE watch-to-watch** (`firmware/tinyboy/tinyboy.ino`, `experiments/ble_uart_test` changes, `experiments/ble_central_test`, `duel_v1`, `duel_v2`, `duel_v3`):
  based on the TinyCircuits ST BLE UART example (Ben Rose, TinyCircuits) and ST's BlueNRG-MS client sample app
- **Joystick Wireling input** (`firmware/tinyboy/tinyboy.ino`, `experiments/joystick_test`, `duel_v2`, `duel_v3`): based on the TinyCircuits Joystick Wireling tutorial —
  https://learn.tinycircuits.com/Wirelings/Joystick_Wireling_Tutorial

## Libraries

**Watch (Arduino):** TinyScreen, STBLE, Wireling and SX1505 libraries by TinyCircuits.

**Laptop (Python):** pyserial, bleak, python-telegram-bot, python-dotenv, matplotlib, NumPy, Pillow
(listed in `laptop/requirements.txt`).