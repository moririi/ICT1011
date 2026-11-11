// Buzzer Wireling sounds. Non-blocking: notes are stepped through in soundUpdate().
#include "sound.h"
#include <Wire.h>
#include <Wireling.h>

const int BUZZER_PIN = A1;   // Wireling port 1 (duel_v3 uses ports 0 and 3 for joysticks)

struct Note { uint16_t freq; uint16_t ms; };   // freq 0 = silence

static const Note SHOT[]   = { {1800, 12} };
static const Note HIT[]    = { {1200, 40}, {1800, 70} };
static const Note HURT[]   = { {180, 140} };
static const Note WIN[]    = { {523, 90}, {659, 90}, {784, 90}, {1047, 220} };
static const Note LOSE[]   = { {392, 140}, {330, 140}, {262, 280} };
static const Note MOVE[]   = { {1000, 8} };
static const Note SELECT[] = { {800, 40}, {1200, 60} };

static const Note *melody = nullptr;
static uint8_t melodyLen = 0, melodyPos = 0;
static unsigned long noteEnd = 0;

static void playMelody(const Note *m, uint8_t len) {
  melody = m; melodyLen = len; melodyPos = 0; noteEnd = 0;
}
#define PLAY(arr) playMelody(arr, sizeof(arr) / sizeof(arr[0]))

void soundBegin() {
  Wireling.begin();            // powers the Wireling ports
  pinMode(BUZZER_PIN, OUTPUT);
}

void soundUpdate() {
  if (!melody || millis() < noteEnd) return;
  if (melodyPos >= melodyLen) { noTone(BUZZER_PIN); melody = nullptr; return; }
  const Note &n = melody[melodyPos++];
  if (n.freq) tone(BUZZER_PIN, n.freq); else noTone(BUZZER_PIN);
  noteEnd = millis() + n.ms;
}

void playShotSound()            { if (!melody) PLAY(SHOT); }  // never cuts off a more important sound
void playHitSound()             { PLAY(HIT); }
void playHurtSound()            { PLAY(HURT); }
void playRoundOverSound(bool w) { if (w) PLAY(WIN); else PLAY(LOSE); }
void playMenuMoveSound()        { PLAY(MOVE); }
void playMenuSelectSound()      { PLAY(SELECT); }
