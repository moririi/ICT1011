#pragma once
#include <Arduino.h>

void soundBegin();      // call once in setup()
void soundUpdate();     // call every loop()

void playShotSound();
void playHitSound();
void playHurtSound();
void playRoundOverSound(bool won);
void playMenuMoveSound();
void playMenuSelectSound();
