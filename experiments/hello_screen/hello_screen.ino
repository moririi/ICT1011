#include <Wire.h>
#include <SPI.h>
#include <TinyScreen.h>

TinyScreen display = TinyScreen(TinyScreenPlus);

void setup() {
  display.begin();
  display.setFlip(true);      // rotate the display 180°
  display.setBrightness(10);
  display.setFont(thinPixel7_10ptFontInfo);
  display.fontColor(TS_8b_White, TS_8b_Black);
  display.setCursor(10, 25);
  display.print("Hello world");
}

void loop() {}