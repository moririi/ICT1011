#include <Wire.h>
#include <SPI.h>
#include <TinyScreen.h>

TinyScreen display = TinyScreen(TinyScreenPlus);

uint8_t lineBuf[96];          // one row of pixels (8-bit colour)
uint8_t offset = 0;
unsigned long lastTime = 0;
int frames = 0;

void setup() {
  display.begin();
  display.setFlip(true);
  display.setBrightness(10);
  SerialUSB.begin(9600);      // TinyScreen+ talks to the PC via SerialUSB
}

void loop() {
  display.setX(0, 95);        // write to the whole screen
  display.setY(0, 63);
  display.startData();
  for (int y = 0; y < 64; y++) {
    for (int x = 0; x < 96; x++) {
      lineBuf[x] = (uint8_t)(x + y + offset);   // moving pattern
    }
    display.writeBuffer(lineBuf, 96);
  }
  display.endTransfer();

  offset++;
  frames++;
  if (millis() - lastTime >= 1000) {
    SerialUSB.print("FPS: ");
    SerialUSB.println(frames);
    frames = 0;
    lastTime = millis();
  }
}