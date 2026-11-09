/*
 * joystick_test - check the Joystick Wireling works on the TinyScreen+ stack
 *
 * Hardware: Wireling Adapter TinyShield in the stack, Joystick Wireling plugged into PORT 0.
 * Libraries (install once): "Wireling" (Library Manager) and the TinyCircuits SX1505 library (zip from GitHub).
 *
 * Shows which direction is pushed on the screen and in the Serial Monitor.
 * Based on the TinyCircuits Joystick Wireling tutorial example.
 *   https://learn.tinycircuits.com/Wirelings/Joystick_Wireling_Tutorial  -> credit in docs/ATTRIBUTION.md
 */
#include <Wire.h>
#include <SPI.h>
#include <TinyScreen.h>
#include <Wireling.h>
#include <SX1505.h>

#define JS_PORT 0   // the Wireling Adapter port the joystick is plugged into (0-3)

TinyScreen display = TinyScreen(TinyScreenPlus);
TinyJoystick joystick = TinyJoystick();

void setup() {
  display.begin();
  display.setFlip(true);
  display.setBrightness(10);
  display.setFont(thinPixel7_10ptFontInfo);
  SerialUSB.begin(9600);

  Wire.begin();
  Wireling.begin();
  Wireling.selectPort(JS_PORT);
  joystick.begin();

  display.clearScreen();
  display.fontColor(TS_8b_Yellow, TS_8b_Black);
  display.setCursor(10, 4);
  display.print("JOYSTICK TEST");
}

void loop() {
  joystick.getPosition();

  // Line on screen showing the current direction
  const char *dir = "centre";
  if (joystick.up)         dir = "UP";
  else if (joystick.down)  dir = "DOWN";
  else if (joystick.left)  dir = "LEFT";
  else if (joystick.right) dir = "RIGHT";

  display.fontColor(TS_8b_White, TS_8b_Black);
  display.setCursor(10, 30);
  display.print("                ");  // clear the old text
  display.setCursor(10, 30);
  display.print(dir);

  SerialUSB.print("up "); SerialUSB.print(joystick.up);
  SerialUSB.print("  down "); SerialUSB.print(joystick.down);
  SerialUSB.print("  left "); SerialUSB.print(joystick.left);
  SerialUSB.print("  right "); SerialUSB.println(joystick.right);

  delay(100);
}
