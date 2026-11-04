/*
 * raycaster_v3 - TinyBoy engine, layer 5: shooting
 * Builds on raycaster_v2 and adds:
 *   - semi-auto continuous fire (one shot every FIRE_INTERVAL_MS, no button needed)
 *   - a shot only counts if the opponent is under the crosshair at that moment
 *   - 2 hits to take the opponent down
 *   - shot feedback: gun + muzzle flash, opponent flashes white when hit, hit pips (HUD)
 *   - "TARGET DOWN" screen, then the opponent respawns
 *
 * Raycasting and sprite projection adapted from
 * Lode Vandevenne's raycasting tutorials: https://lodev.org/cgtutor/raycasting.html
 * and https://lodev.org/cgtutor/raycasting3.html
 * -> credited in docs/ATTRIBUTION.md
 */
#include <Wire.h>
#include <SPI.h>
#include <TinyScreen.h>
#include <math.h>

TinyScreen display = TinyScreen(TinyScreenPlus);

// ---------------- Screen ----------------
const int SCREEN_W = 96;
const int SCREEN_H = 64;
const int CENTER_X = SCREEN_W / 2;
const int CENTER_Y = SCREEN_H / 2;

// 8-bit TinyScreen colours (if a name doesn't compile, swap it for another TS_8b_ colour)
const uint8_t COL_CEILING    = TS_8b_Black;
const uint8_t COL_FLOOR      = TS_8b_Gray;
const uint8_t COL_BLUE_LIGHT = TS_8b_Blue;
const uint8_t COL_BLUE_DARK  = TS_8b_DarkBlue;
const uint8_t COL_RED_LIGHT  = TS_8b_Red;
const uint8_t COL_RED_DARK   = TS_8b_DarkRed;
const uint8_t COL_OPPONENT   = TS_8b_Green;
const uint8_t COL_OPPONENT_HIT = TS_8b_White;  // flash when hit
const uint8_t COL_CROSSHAIR  = TS_8b_White;
const uint8_t COL_CROSSHAIR_ON_TARGET = TS_8b_Red;
const uint8_t COL_GUN        = TS_8b_Black;
const uint8_t COL_MUZZLE     = TS_8b_Yellow;
const uint8_t COL_PIP_FULL   = TS_8b_Red;     // opponent health remaining
const uint8_t COL_PIP_EMPTY  = TS_8b_DarkRed; // health lost

// ---------------- Shooting settings ----------------
const unsigned long FIRE_INTERVAL_MS  = 400;   // semi-auto: one shot every 0.4 s
const unsigned long MUZZLE_FLASH_MS   = 60;    // how long the muzzle flash shows
const unsigned long HIT_FLASH_MS      = 150;   // how long the opponent flashes white
const unsigned long RESPAWN_DELAY_MS  = 2000;  // "TARGET DOWN" screen duration
const int HITS_TO_KILL = 2;

// ---------------- Map ----------------
// 0 = empty, 1 = blue wall, 2 = red wall. Border must be solid.
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

// ---------------- Player / camera ----------------
float posX = 2.5f, posY = 2.5f;
float dirX = 1.0f, dirY = 0.0f;
float planeX = 0.0f, planeY = 0.66f;

// ---------------- Opponent (dummy for now) ----------------
// Later, oppX/oppY come from the network instead of this patrol.
float oppX = 3.5f, oppY = 8.5f;
float oppSpeed = 1.5f;
const float OPP_MIN_X = 3.5f;
const float OPP_MAX_X = 12.5f;
int oppHits = 0;  // hits taken this life

const int SPR_TEX_W = 8;
const int SPR_TEX_H = 16;
const uint8_t opponentBitmap[SPR_TEX_H] = {
  0b00111100,  // head
  0b01111110,
  0b01111110,
  0b00111100,
  0b00011000,  // neck
  0b11111111,  // shoulders / arms
  0b11111111,
  0b10111101,
  0b10111101,
  0b10111101,
  0b00111100,  // waist
  0b00100100,  // legs
  0b00100100,
  0b00100100,
  0b01100110,
  0b01100110,  // feet
};

bool  sprVisible = false;
int   sprLeft, sprRight, sprTop, sprBottom;
int   sprW, sprH;
float sprDepth;
bool  onTarget = false;

// ---------------- Game state ----------------
enum GameState { STATE_PLAYING, STATE_TARGET_DOWN };
GameState state = STATE_PLAYING;
unsigned long stateStart = 0;
unsigned long lastShotTime = 0;
unsigned long muzzleFlashUntil = 0;
unsigned long hitFlashUntil = 0;
int kills = 0;

// ---------------- Per-column raycast results ----------------
uint8_t wallTop[SCREEN_W];
uint8_t wallBottom[SCREEN_W];
uint8_t wallColour[SCREEN_W];
float   wallDist[SCREEN_W];

uint8_t lineBuf[SCREEN_W];

// ---------------- HUD / gun layout ----------------
const int PIP_Y_TOP = 1, PIP_Y_BOTTOM = 3;     // rows of the hit pips (inclusive)
const int PIP_W = 6, PIP_GAP = 2, PIP_X0 = 2;
const int GUN_TOP = 56;                         // gun occupies rows 56..63
const int GUN_LEFT = CENTER_X - 3, GUN_RIGHT = CENTER_X + 3;      // inclusive
const int FLASH_TOP = 51, FLASH_BOTTOM = 55;    // muzzle flash rows (inclusive)
const int FLASH_LEFT = CENTER_X - 2, FLASH_RIGHT = CENTER_X + 2;

// ---------------- Helpers ----------------
bool isWall(float x, float y) {
  int mx = (int)x;
  int my = (int)y;
  if (mx < 0 || my < 0 || mx >= MAP_W || my >= MAP_H) return true;
  return worldMap[my][mx] != 0;
}

void tryMove(float dx, float dy) {
  const float margin = 0.2f;
  float checkX = posX + dx + (dx > 0 ? margin : -margin);
  float checkY = posY + dy + (dy > 0 ? margin : -margin);
  if (!isWall(checkX, posY)) posX += dx;
  if (!isWall(posX, checkY)) posY += dy;
}

void rotate(float a) {
  float c = cosf(a), s = sinf(a);
  float oldDirX = dirX;
  dirX = dirX * c - dirY * s;
  dirY = oldDirX * s + dirY * c;
  float oldPlaneX = planeX;
  planeX = planeX * c - planeY * s;
  planeY = oldPlaneX * s + planeY * c;
}

// ---------------- Sound (stub) ----------------
// The buzzer teammate fills this in (sound.cpp). Kept here so the hooks are ready.
void playShotSound() {}
void playHitSound() {}
void playKillSound() {}

// ---------------- Input ----------------
void handleInput(float dt) {
  uint8_t buttons = display.getButtons();
  const float moveSpeed = 2.5f * dt;
  const float turnSpeed = 2.5f * dt;

  if (buttons & TSButtonUpperLeft)  tryMove(dirX * moveSpeed, dirY * moveSpeed);
  if (buttons & TSButtonLowerLeft)  tryMove(-dirX * moveSpeed, -dirY * moveSpeed);
  if (buttons & TSButtonUpperRight) rotate(-turnSpeed);
  if (buttons & TSButtonLowerRight) rotate(turnSpeed);
}

// ---------------- Opponent movement (dummy) ----------------
void updateOpponent(float dt) {
  oppX += oppSpeed * dt;
  if (oppX > OPP_MAX_X) { oppX = OPP_MAX_X; oppSpeed = -oppSpeed; }
  if (oppX < OPP_MIN_X) { oppX = OPP_MIN_X; oppSpeed = -oppSpeed; }
}

// ---------------- Raycasting (walls) ----------------
void castRays() {
  for (int x = 0; x < SCREEN_W; x++) {
    float cameraX = 2.0f * x / SCREEN_W - 1.0f;
    float rayDirX = dirX + planeX * cameraX;
    float rayDirY = dirY + planeY * cameraX;

    int mapX = (int)posX;
    int mapY = (int)posY;

    float deltaX = (rayDirX == 0) ? 1e30f : fabsf(1.0f / rayDirX);
    float deltaY = (rayDirY == 0) ? 1e30f : fabsf(1.0f / rayDirY);

    int stepX, stepY;
    float sideX, sideY;
    if (rayDirX < 0) { stepX = -1; sideX = (posX - mapX) * deltaX; }
    else             { stepX =  1; sideX = (mapX + 1.0f - posX) * deltaX; }
    if (rayDirY < 0) { stepY = -1; sideY = (posY - mapY) * deltaY; }
    else             { stepY =  1; sideY = (mapY + 1.0f - posY) * deltaY; }

    int side = 0;
    while (worldMap[mapY][mapX] == 0) {
      if (sideX < sideY) { sideX += deltaX; mapX += stepX; side = 0; }
      else               { sideY += deltaY; mapY += stepY; side = 1; }
    }

    float dist = (side == 0) ? (sideX - deltaX) : (sideY - deltaY);
    if (dist < 0.05f) dist = 0.05f;
    wallDist[x] = dist;

    int lineH = (int)(SCREEN_H / dist);
    int top = CENTER_Y - lineH / 2;
    int bottom = CENTER_Y + lineH / 2;
    if (top < 0) top = 0;
    if (bottom > SCREEN_H) bottom = SCREEN_H;
    wallTop[x] = top;
    wallBottom[x] = bottom;

    uint8_t type = worldMap[mapY][mapX];
    if (type == 2) wallColour[x] = side ? COL_RED_DARK : COL_RED_LIGHT;
    else           wallColour[x] = side ? COL_BLUE_DARK : COL_BLUE_LIGHT;
  }
}

// ---------------- Sprite projection ----------------
void projectOpponent() {
  float relX = oppX - posX;
  float relY = oppY - posY;

  float invDet = 1.0f / (planeX * dirY - dirX * planeY);
  float camX = invDet * (dirY * relX - dirX * relY);
  float camY = invDet * (-planeY * relX + planeX * relY);

  sprVisible = false;
  onTarget = false;
  if (camY <= 0.2f) return;

  sprDepth = camY;
  int screenX = (int)(CENTER_X * (1.0f + camX / camY));

  int fullH = (int)(SCREEN_H / camY);
  sprH = (int)(fullH * 0.8f);
  if (sprH < 2) return;
  sprW = sprH / 2;
  if (sprW < 1) sprW = 1;

  sprBottom = CENTER_Y + fullH / 2;
  sprTop = sprBottom - sprH;
  sprLeft = screenX - sprW / 2;
  sprRight = sprLeft + sprW;

  if (sprRight < 0 || sprLeft >= SCREEN_W) return;
  sprVisible = true;

  if (CENTER_X >= sprLeft && CENTER_X < sprRight &&
      CENTER_Y >= sprTop && CENTER_Y < sprBottom &&
      sprDepth < wallDist[CENTER_X]) {
    int tx = (CENTER_X - sprLeft) * SPR_TEX_W / sprW;
    int ty = (CENTER_Y - sprTop) * SPR_TEX_H / sprH;
    onTarget = (opponentBitmap[ty] >> (7 - tx)) & 1;
  }
}

// ---------------- Shooting ----------------
void drawTargetDownScreen();

// Called every FIRE_INTERVAL_MS. The shot hits only if the opponent is under the crosshair right now.
void shoot(unsigned long now) {
  lastShotTime = now;
  muzzleFlashUntil = now + MUZZLE_FLASH_MS;
  playShotSound();

  if (!onTarget) return;  // missed

  oppHits++;
  hitFlashUntil = now + HIT_FLASH_MS;
  playHitSound();

  if (oppHits >= HITS_TO_KILL) {
    kills++;
    playKillSound();
    state = STATE_TARGET_DOWN;
    stateStart = now;
    drawTargetDownScreen();
  }
}

void respawnOpponent() {
  oppHits = 0;
  oppX = OPP_MIN_X;
  oppSpeed = fabsf(oppSpeed);
  state = STATE_PLAYING;
}

// ---------------- Drawing ----------------
// Crosshair: a small "+" with a gap in the middle
bool isCrosshairPixel(int x, int y) {
  if (y == CENTER_Y && (x == CENTER_X - 3 || x == CENTER_X - 2 || x == CENTER_X + 2 || x == CENTER_X + 3)) return true;
  if (x == CENTER_X && (y == CENTER_Y - 3 || y == CENTER_Y - 2 || y == CENTER_Y + 2 || y == CENTER_Y + 3)) return true;
  return false;
}

void drawFrame(unsigned long now) {
  uint8_t crosshairColour = onTarget ? COL_CROSSHAIR_ON_TARGET : COL_CROSSHAIR;
  uint8_t opponentColour  = (now < hitFlashUntil) ? COL_OPPONENT_HIT : COL_OPPONENT;
  bool muzzleOn = now < muzzleFlashUntil;
  int healthLeft = HITS_TO_KILL - oppHits;

  display.setX(0, SCREEN_W - 1);
  display.setY(0, SCREEN_H - 1);
  display.startData();
  for (int y = 0; y < SCREEN_H; y++) {
    // Work out once per row which overlays can appear here (saves time per pixel)
    bool spriteRow    = sprVisible && y >= sprTop && y < sprBottom;
    int  texY         = spriteRow ? (y - sprTop) * SPR_TEX_H / sprH : 0;
    bool crosshairRow = y >= CENTER_Y - 3 && y <= CENTER_Y + 3;
    bool pipRow       = y >= PIP_Y_TOP && y <= PIP_Y_BOTTOM;
    bool gunRow       = y >= GUN_TOP;
    bool flashRow     = muzzleOn && y >= FLASH_TOP && y <= FLASH_BOTTOM;

    for (int x = 0; x < SCREEN_W; x++) {
      // 1) ceiling / wall / floor
      uint8_t c;
      if (y < wallTop[x])         c = COL_CEILING;
      else if (y < wallBottom[x]) c = wallColour[x];
      else                        c = COL_FLOOR;

      // 2) opponent, only where it's in front of this column's wall
      if (spriteRow && x >= sprLeft && x < sprRight && sprDepth < wallDist[x]) {
        int texX = (x - sprLeft) * SPR_TEX_W / sprW;
        if ((opponentBitmap[texY] >> (7 - texX)) & 1) c = opponentColour;
      }

      // 3) gun and muzzle flash
      if (gunRow && x >= GUN_LEFT && x <= GUN_RIGHT) c = COL_GUN;
      if (flashRow && x >= FLASH_LEFT && x <= FLASH_RIGHT) c = COL_MUZZLE;

      // 4) crosshair
      if (crosshairRow && isCrosshairPixel(x, y)) c = crosshairColour;

      // 5) HUD: opponent health pips, top-left
      if (pipRow) {
        int rel = x - PIP_X0;
        if (rel >= 0) {
          int pip = rel / (PIP_W + PIP_GAP);
          int inPip = rel % (PIP_W + PIP_GAP);
          if (pip < HITS_TO_KILL && inPip < PIP_W) {
            c = (pip < healthLeft) ? COL_PIP_FULL : COL_PIP_EMPTY;
          }
        }
      }

      lineBuf[x] = c;
    }
    display.writeBuffer(lineBuf, SCREEN_W);
  }
  display.endTransfer();
}

// Drawn once when the opponent goes down (not every frame)
void drawTargetDownScreen() {
  display.clearScreen();
  display.setFont(thinPixel7_10ptFontInfo);
  display.fontColor(TS_8b_Green, TS_8b_Black);
  display.setCursor(18, 20);
  display.print("TARGET DOWN");
  display.fontColor(TS_8b_White, TS_8b_Black);
  display.setCursor(30, 36);
  display.print("Kills: ");
  display.print(kills);
}

// ---------------- Main ----------------
unsigned long lastFrame = 0;
unsigned long lastFpsTime = 0;
int frames = 0;

void setup() {
  display.begin();
  display.setFlip(true);
  display.setBrightness(10);
  SerialUSB.begin(9600);
  lastFrame = millis();
  lastShotTime = millis();
}

void loop() {
  unsigned long now = millis();
  float dt = (now - lastFrame) / 1000.0f;
  lastFrame = now;
  if (dt > 0.1f) dt = 0.1f;

  if (state == STATE_TARGET_DOWN) {
    // Show the "TARGET DOWN" screen for a moment, then bring the opponent back
    if (now - stateStart >= RESPAWN_DELAY_MS) respawnOpponent();
    return;
  }

  handleInput(dt);
  updateOpponent(dt);
  castRays();
  projectOpponent();  // uses wallDist[], so runs after castRays()

  if (now - lastShotTime >= FIRE_INTERVAL_MS) shoot(now);
  if (state != STATE_PLAYING) return;  // the shot just took the opponent down

  drawFrame(now);

  frames++;
  if (now - lastFpsTime >= 1000) {
    SerialUSB.print("FPS: ");
    SerialUSB.print(frames);
    SerialUSB.print("  opponent hits: ");
    SerialUSB.print(oppHits);
    SerialUSB.print("/");
    SerialUSB.print(HITS_TO_KILL);
    SerialUSB.print("  kills: ");
    SerialUSB.println(kills);
    frames = 0;
    lastFpsTime = now;
  }
}
