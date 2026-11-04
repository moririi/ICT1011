# TinyBoy – Pocket Multiplayer FPS

**ICT1011 Team Exploration Project**

TinyBoy turns the TinyCircuits TinyScreen+ into a pocket-sized retro game console. It runs a first-person 3D maze shooter, in the style of classic raycaster games like Wolfenstein 3D, on a 96×64 OLED screen. Two TinyBoys can connect wirelessly over Bluetooth Low Energy for 1v1 multiplayer: each player sees the other in the maze and tries to tag them first.

The challenge is fitting a real-time 3D game, plus wireless multiplayer, into a microcontroller with a 48 MHz processor, about 32 KB of RAM and no floating-point hardware.

## Planned features

- **3D raycasting engine:** real-time first-person view of a maze, rendered column by column
- **1v1 multiplayer over BLE:** two consoles exchange player position, direction and actions in real time
- **Physical controls:** joystick for movement, buttons for actions, and tilt-to-aim using the accelerometer
- **Sound effects:** via a buzzer
- **Custom levels:** maps stored on a microSD card, designed with a desktop level editor
- **3D-printed handheld case**

> Features are in development; this list will be updated as they're completed.

## Hardware

Per console:

| Component | Purpose |
|---|---|
| TinyScreen+ | Processor + OLED display + built-in buttons |
| Accelerometer TinyShield | Tilt controls |
| ST BLE TinyShield | Multiplayer link |
| MicroSD TinyShield + microSD card | Level storage |
| Wireling Adapter TinyShield | Connects the wirelings below |
| Joystick Wireling | Movement |
| Buzzer Wireling | Sound |
| 150 mAh Li-Po battery | Portable power |

## Repository structure

```
firmware/tinyboy/    Main game sketch (open tinyboy.ino in the Arduino IDE)
experiments/         Standalone test sketches for individual hardware/features
prototype/           PC version of the raycaster for testing ideas quickly
tools/level_editor/  Desktop level editor (Python)
case/                3D-printable case files
docs/                Interface definitions and attributions
```

## Getting started

1. Install the [Arduino IDE](https://www.arduino.cc/en/software).
2. Set up the TinyScreen+ board support by following the TinyCircuits setup guide on [tinycircuits.com](https://tinycircuits.com/).
3. Clone this repo and open `firmware/tinyboy/tinyboy.ino` in the Arduino IDE.
4. Select the TinyScreen+ board and its port, then upload.


## Benchmarks

| Test | Result | Notes |
| --- | --- | --- |
| `experiments/fps_test` | **194 FPS** | Full 96×64 screen redraw, 8-bit colour, line-by-line `writeBuffer` (~5 ms per frame) |
| `experiments/raycaster_v1` | **~50 FPS** | Walls-only raycaster, `float` maths, 96 rays per frame |

## Contributing

- Don't push directly to `main`. Create a branch for your work and open a Pull Request.
- Pull from `main` often to stay up to date.
- Any code adapted from online sources **must** be credited in [`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md) and in a comment in the source file.

## Team

Morikage Shinji 2604696
Lee Chee Hsien 2603040
Muhammad Naufal 2603036
Jeremy Ang 2603019
Tee Wei Wen 2605749
Lee Yu Xuan 2603945

## Attribution

See [`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md) for all third-party code, libraries and references used.