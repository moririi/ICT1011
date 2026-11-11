/*
 * raycaster_v4 - TinyBoy engine: 1v1 rounds against a bot
 * Builds on raycaster_v3 and adds:
 *   - round system: whoever lands 2 hits wins the round, then both players reset to their spawns
 *   - match: first to roundsToWin rounds wins, then scores reset
 *   - a bot that patrols the maze, turns to face you when it can see you, and shoots back
 *   - red screen border when you get hit, your own health pips (top-right)
 *   - the bot flashes yellow when it fires, so you know you're being shot at
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
const uint8_t COL_OPPONENT_HIT  = TS_8b_White;   // bot flashes white when you hit it
const uint8_t COL_OPPONENT_FIRE = TS_8b_Yellow;  // bot flashes yellow when it fires
const uint8_t COL_CROSSHAIR  = TS_8b_White;
const uint8_t COL_CROSSHAIR_ON_TARGET = TS_8b_Red;
const uint8_t COL_GUN        = TS_8b_Black;
const uint8_t COL_MUZZLE     = TS_8b_Yellow;
const uint8_t COL_DAMAGE     = TS_8b_Red;        // border when you're hit
const uint8_t COL_BOT_PIP_FULL     = TS_8b_Red;
const uint8_t COL_BOT_PIP_EMPTY    = TS_8b_DarkRed;
const uint8_t COL_PLAYER_PIP_FULL  = TS_8b_Green;
const uint8_t COL_PLAYER_PIP_EMPTY = TS_8b_DarkGreen;

// ---------------- Game rules ----------------
const int PLAYER_MAX_HITS = 2;   // hits you can take before losing the round
const int BOT_MAX_HITS    = 2;   // hits the bot can take
int roundsToWin = 3;   // first to N rounds wins the match (changed in SETTINGS: 1 / 3 / 5)
const unsigned long ROUND_OVER_MS = 2500;

// Your gun: semi-auto continuous fire
const unsigned long FIRE_INTERVAL_MS = 400;
const unsigned long MUZZLE_FLASH_MS  = 60;
const unsigned long HIT_FLASH_MS     = 150;
const unsigned long DAMAGE_FLASH_MS  = 250;

// Bot behaviour (tune these to make it easier / harder)
const float BOT_SPEED            = 1.6f;   // squares per second while patrolling
const float BOT_TURN_SPEED       = 3.0f;   // radians per second when turning to aim at you
const float BOT_AIM_TOLERANCE    = 0.15f;  // radians: how precisely it must face you before firing
const unsigned long BOT_REACTION_MS      = 600;  // delay after spotting you before its first shot
const unsigned long BOT_FIRE_INTERVAL_MS = 700;  // slower than your gun
const int   BOT_BASE_ACCURACY    = 75;     // % chance to hit at point blank
const int   BOT_ACCURACY_FALLOFF = 5;      // % lost per map square of distance
const int   BOT_MIN_ACCURACY     = 20;

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

// Spawn points (opposite corners, no line of sight between them)
const float PLAYER_SPAWN_X = 2.5f, PLAYER_SPAWN_Y = 2.5f;
const float BOT_SPAWN_X = 13.5f, BOT_SPAWN_Y = 13.5f;

// Bot patrol loop (centres of open squares; every leg is a straight, wall-free line)
const int WP_COUNT = 9;
const float waypoints[WP_COUNT][2] = {
  {13.5f, 13.5f},  // bot spawn
  {13.5f,  8.5f},
  {10.5f,  8.5f},
  {10.5f,  4.5f},
  { 4.5f,  4.5f},
  { 4.5f,  8.5f},
  { 4.5f, 11.5f},
  {10.5f, 11.5f},
  {10.5f, 13.5f},
};

// ---------------- Player / camera ----------------
float posX, posY;
float dirX, dirY;
float planeX, planeY;
int playerHits = 0;

// ---------------- Bot ----------------
float oppX, oppY;
float botAngle = 0;
int wpIndex = 1;
int oppHits = 0;
bool botHadLos = false;
unsigned long botLosSince = 0;
unsigned long botLastShot = 0;

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
enum GameState { STATE_MENU, STATE_PLAYING, STATE_ROUND_OVER };
GameState state = STATE_MENU;
unsigned long stateStart = 0;
unsigned long lastShotTime = 0;
unsigned long muzzleFlashUntil = 0;
unsigned long hitFlashUntil = 0;
unsigned long botFireFlashUntil = 0;
unsigned long damageFlashUntil = 0;
int roundsPlayer = 0;
int roundsBot = 0;
bool matchOver = false;
bool waitRelease = false;  // ignore movement until the menu "select" button is let go

// ---------------- Per-column raycast results ----------------
uint8_t wallTop[SCREEN_W];
uint8_t wallBottom[SCREEN_W];
uint8_t wallColour[SCREEN_W];
float   wallDist[SCREEN_W];

uint8_t lineBuf[SCREEN_W];

// ---------------- HUD / gun layout ----------------
const int PIP_Y_TOP = 1, PIP_Y_BOTTOM = 3;
const int PIP_W = 6, PIP_GAP = 2;
const int BOT_PIP_X0 = 2;  // top-left: bot health
const int PLAYER_PIP_X0 = SCREEN_W - 2 - (PLAYER_MAX_HITS * PIP_W + (PLAYER_MAX_HITS - 1) * PIP_GAP);  // top-right: yours
const int DAMAGE_BORDER = 4;  // thickness of the red border when hit
const int GUN_TOP = 56;
const int GUN_LEFT = CENTER_X - 3, GUN_RIGHT = CENTER_X + 3;
const int FLASH_TOP = 51, FLASH_BOTTOM = 55;
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

float wrapAngle(float a) {
  while (a > PI)  a -= 2 * PI;
  while (a < -PI) a += 2 * PI;
  return a;
}

// Walk along the line between two points in small steps; blocked if any step is inside a wall
bool hasLineOfSight(float x0, float y0, float x1, float y1) {
  float dx = x1 - x0, dy = y1 - y0;
  float dist = sqrtf(dx * dx + dy * dy);
  int steps = (int)(dist / 0.1f);
  for (int i = 1; i < steps; i++) {
    float t = (float)i / steps;
    if (isWall(x0 + dx * t, y0 + dy * t)) return false;
  }
  return true;
}

// ---------------- Sound (stubs for the buzzer teammate) ----------------
void playShotSound() {}
void playHitSound() {}
void playHurtSound() {}
void playRoundOverSound(bool won) {}

// ---------------- Rounds ----------------
void drawRoundOverScreen(bool playerWon);

void startRound(unsigned long now) {
  if (matchOver) {  // new match
    roundsPlayer = 0;
    roundsBot = 0;
    matchOver = false;
  }
  // Player back to spawn, facing +x
  posX = PLAYER_SPAWN_X; posY = PLAYER_SPAWN_Y;
  dirX = 1.0f; dirY = 0.0f;
  planeX = 0.0f; planeY = 0.66f;
  playerHits = 0;

  // Bot back to spawn, heading for its first waypoint
  oppX = BOT_SPAWN_X; oppY = BOT_SPAWN_Y;
  wpIndex = 1;
  botAngle = atan2f(waypoints[1][1] - oppY, waypoints[1][0] - oppX);
  oppHits = 0;
  botHadLos = false;
  botLastShot = now;

  lastShotTime = now;
  muzzleFlashUntil = hitFlashUntil = botFireFlashUntil = damageFlashUntil = 0;
  state = STATE_PLAYING;
}

void endRound(bool playerWon, unsigned long now) {
  if (playerWon) roundsPlayer++; else roundsBot++;
  matchOver = (roundsPlayer >= roundsToWin || roundsBot >= roundsToWin);
  playRoundOverSound(playerWon);
  state = STATE_ROUND_OVER;
  stateStart = now;
  drawRoundOverScreen(playerWon);
}

// ---------------- Input ----------------
void handleInput(float dt) {
  uint8_t buttons = display.getButtons();
  if (waitRelease) {               // don't turn/move from the button used to pick START
    if (buttons) return;
    waitRelease = false;
  }

  const float moveSpeed = 2.5f * dt;
  const float turnSpeed = 2.5f * dt;

  if (buttons & TSButtonUpperLeft)  tryMove(dirX * moveSpeed, dirY * moveSpeed);
  if (buttons & TSButtonLowerLeft)  tryMove(-dirX * moveSpeed, -dirY * moveSpeed);
  if (buttons & TSButtonUpperRight) rotate(-turnSpeed);
  if (buttons & TSButtonLowerRight) rotate(turnSpeed);
}

// ---------------- Bot ----------------
void botShoot(unsigned long now) {
  botLastShot = now;
  botFireFlashUntil = now + MUZZLE_FLASH_MS;

  // Accuracy drops with distance
  float dx = posX - oppX, dy = posY - oppY;
  float dist = sqrtf(dx * dx + dy * dy);
  int chance = BOT_BASE_ACCURACY - (int)(dist * BOT_ACCURACY_FALLOFF);
  if (chance < BOT_MIN_ACCURACY) chance = BOT_MIN_ACCURACY;

  if (random(100) < chance) {
    playerHits++;
    damageFlashUntil = now + DAMAGE_FLASH_MS;
    playHurtSound();
    if (playerHits >= PLAYER_MAX_HITS) endRound(false, now);
  }
}

void updateBot(float dt, unsigned long now) {
  bool los = hasLineOfSight(oppX, oppY, posX, posY);

  // Movement: follow the patrol loop (half speed while it's shooting at you)
  float speed = BOT_SPEED * (los ? 0.5f : 1.0f) * dt;
  float tx = waypoints[wpIndex][0], ty = waypoints[wpIndex][1];
  float dx = tx - oppX, dy = ty - oppY;
  float d = sqrtf(dx * dx + dy * dy);
  if (d < 0.1f) {
    wpIndex = (wpIndex + 1) % WP_COUNT;
  } else {
    float step = (speed < d) ? speed : d;
    oppX += dx / d * step;
    oppY += dy / d * step;
  }

  if (los) {
    if (!botHadLos) { botHadLos = true; botLosSince = now; }
    // Turn toward the player at a limited speed (so it isn't an instant aimbot)
    float target = atan2f(posY - oppY, posX - oppX);
    float diff = wrapAngle(target - botAngle);
    float maxTurn = BOT_TURN_SPEED * dt;
    if (diff > maxTurn) diff = maxTurn;
    if (diff < -maxTurn) diff = -maxTurn;
    botAngle = wrapAngle(botAngle + diff);

    bool aimed = fabsf(wrapAngle(target - botAngle)) < BOT_AIM_TOLERANCE;
    if (aimed &&
        now - botLosSince >= BOT_REACTION_MS &&
        now - botLastShot >= BOT_FIRE_INTERVAL_MS) {
      botShoot(now);
    }
  } else {
    botHadLos = false;
    if (d >= 0.1f) botAngle = atan2f(dy, dx);  // face where it's walking
  }
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

// ---------------- Your shooting ----------------
void playerShoot(unsigned long now) {
  lastShotTime = now;
  muzzleFlashUntil = now + MUZZLE_FLASH_MS;
  playShotSound();

  if (!onTarget) return;

  oppHits++;
  hitFlashUntil = now + HIT_FLASH_MS;
  playHitSound();
  if (oppHits >= BOT_MAX_HITS) endRound(true, now);
}

// ---------------- Drawing ----------------
bool isCrosshairPixel(int x, int y) {
  if (y == CENTER_Y && (x == CENTER_X - 3 || x == CENTER_X - 2 || x == CENTER_X + 2 || x == CENTER_X + 3)) return true;
  if (x == CENTER_X && (y == CENTER_Y - 3 || y == CENTER_Y - 2 || y == CENTER_Y + 2 || y == CENTER_Y + 3)) return true;
  return false;
}

// Health pips: 'count' small bars starting at x0; the first 'left' are full
bool pipPixel(int x, int x0, int count, int left, uint8_t full, uint8_t empty, uint8_t &c) {
  int rel = x - x0;
  if (rel < 0) return false;
  int pip = rel / (PIP_W + PIP_GAP);
  int inPip = rel % (PIP_W + PIP_GAP);
  if (pip >= count || inPip >= PIP_W) return false;
  c = (pip < left) ? full : empty;
  return true;
}

void drawFrame(unsigned long now) {
  uint8_t crosshairColour = onTarget ? COL_CROSSHAIR_ON_TARGET : COL_CROSSHAIR;
  uint8_t opponentColour = COL_OPPONENT;
  if (now < botFireFlashUntil) opponentColour = COL_OPPONENT_FIRE;
  if (now < hitFlashUntil)     opponentColour = COL_OPPONENT_HIT;
  bool muzzleOn = now < muzzleFlashUntil;
  bool damageOn = now < damageFlashUntil;
  int botHealthLeft = BOT_MAX_HITS - oppHits;
  int playerHealthLeft = PLAYER_MAX_HITS - playerHits;

  display.setX(0, SCREEN_W - 1);
  display.setY(0, SCREEN_H - 1);
  display.startData();
  for (int y = 0; y < SCREEN_H; y++) {
    bool spriteRow    = sprVisible && y >= sprTop && y < sprBottom;
    int  texY         = spriteRow ? (y - sprTop) * SPR_TEX_H / sprH : 0;
    bool crosshairRow = y >= CENTER_Y - 3 && y <= CENTER_Y + 3;
    bool pipRow       = y >= PIP_Y_TOP && y <= PIP_Y_BOTTOM;
    bool gunRow       = y >= GUN_TOP;
    bool flashRow     = muzzleOn && y >= FLASH_TOP && y <= FLASH_BOTTOM;
    bool damageRow    = damageOn && (y < DAMAGE_BORDER || y >= SCREEN_H - DAMAGE_BORDER);

    for (int x = 0; x < SCREEN_W; x++) {
      // 1) ceiling / wall / floor
      uint8_t c;
      if (y < wallTop[x])         c = COL_CEILING;
      else if (y < wallBottom[x]) c = wallColour[x];
      else                        c = COL_FLOOR;

      // 2) bot, only where it's in front of this column's wall
      if (spriteRow && x >= sprLeft && x < sprRight && sprDepth < wallDist[x]) {
        int texX = (x - sprLeft) * SPR_TEX_W / sprW;
        if ((opponentBitmap[texY] >> (7 - texX)) & 1) c = opponentColour;
      }

      // 3) gun and muzzle flash
      if (gunRow && x >= GUN_LEFT && x <= GUN_RIGHT) c = COL_GUN;
      if (flashRow && x >= FLASH_LEFT && x <= FLASH_RIGHT) c = COL_MUZZLE;

      // 4) crosshair
      if (crosshairRow && isCrosshairPixel(x, y)) c = crosshairColour;

      // 5) red border when you've just been hit
      if (damageOn && (damageRow || x < DAMAGE_BORDER || x >= SCREEN_W - DAMAGE_BORDER)) c = COL_DAMAGE;

      // 6) HUD on top: bot health (top-left), your health (top-right)
      if (pipRow) {
        pipPixel(x, BOT_PIP_X0, BOT_MAX_HITS, botHealthLeft, COL_BOT_PIP_FULL, COL_BOT_PIP_EMPTY, c);
        pipPixel(x, PLAYER_PIP_X0, PLAYER_MAX_HITS, playerHealthLeft, COL_PLAYER_PIP_FULL, COL_PLAYER_PIP_EMPTY, c);
      }

      lineBuf[x] = c;
    }
    display.writeBuffer(lineBuf, SCREEN_W);
  }
  display.endTransfer();
}

// Drawn once when a round ends (not every frame)
void drawRoundOverScreen(bool playerWon) {
  display.clearScreen();
  display.setFont(thinPixel7_10ptFontInfo);

  display.fontColor(playerWon ? TS_8b_Green : TS_8b_Red, TS_8b_Black);
  display.setCursor(playerWon ? 24 : 22, 10);
  display.print(playerWon ? "ROUND WON" : "ROUND LOST");

  display.fontColor(TS_8b_White, TS_8b_Black);
  display.setCursor(22, 28);
  display.print("You ");
  display.print(roundsPlayer);
  display.print(" - ");
  display.print(roundsBot);
  display.print(" Bot");

  if (matchOver) {
    display.fontColor(TS_8b_Yellow, TS_8b_Black);
    display.setCursor(24, 44);
    display.print(roundsPlayer >= roundsToWin ? "MATCH WON!" : "MATCH LOST");
  }
}

// ---------------- Main ----------------
unsigned long lastFrame = 0;
unsigned long lastFpsTime = 0;
int frames = 0;
// ---------------- Menus ----------------
// Controls: upper-left = up, lower-left = down, either right button = select
enum MenuScreen { SCR_MAIN, SCR_MULTI, SCR_SETTINGS, SCR_HOWTO, SCR_WAITING };
int screen = SCR_MAIN;
int sel = 0;              // highlighted item on the current screen
uint8_t prevButtons = 0;
uint8_t role = 0;         // 0 = Player A (host), 1 = Player B (join) - used by the BLE code later

const char *MAIN_ITEMS[] = { "SINGLEPLAYER", "MULTIPLAYER", "SETTINGS", "HOW TO PLAY" };
const char *MP_ITEMS[]   = { "HOST (A)", "JOIN (B)", "BACK" };

// Only buttons that were *just* pressed this frame (holding doesn't repeat)
uint8_t pressedButtons() {
  uint8_t b = display.getButtons();
  uint8_t pressed = b & ~prevButtons;
  prevButtons = b;
  return pressed;
}

void printCentered(const char *text, int y, uint8_t colour) {
  display.fontColor(colour, TS_8b_Black);
  int w = display.getPrintWidth((char *)text);
  display.setCursor((SCREEN_W - w) / 2, y);
  display.print(text);
}

void printLeft(const char *text, int y, uint8_t colour) {
  display.fontColor(colour, TS_8b_Black);
  display.setCursor(2, y);
  display.print(text);
}

void drawItem(const char *text, int y, bool selected) {
  char buf[24];
  if (selected) snprintf(buf, sizeof(buf), "> %s <", text);
  else          snprintf(buf, sizeof(buf), "%s", text);
  printCentered(buf, y, selected ? TS_8b_White : TS_8b_Gray);
}

int itemCount() {
  switch (screen) {
    case SCR_MAIN:     return 4;
    case SCR_MULTI:    return 3;
    case SCR_SETTINGS: return 2;
    default:           return 1;  // text screens: only "back"
  }
}

// Drawn once on entry / when the selection changes (not every frame)
void drawScreen() {
  display.clearScreen();
  display.setFont(thinPixel7_10ptFontInfo);
  char buf[20];

  switch (screen) {
    case SCR_MAIN:
      printCentered("TINYBOY", 1, TS_8b_Yellow);
      for (int i = 0; i < 4; i++) drawItem(MAIN_ITEMS[i], 14 + i * 11, sel == i);
      break;

    case SCR_MULTI:
      printCentered("MULTIPLAYER", 1, TS_8b_Yellow);
      for (int i = 0; i < 3; i++) drawItem(MP_ITEMS[i], 16 + i * 12, sel == i);
      break;

    case SCR_SETTINGS:
      printCentered("SETTINGS", 1, TS_8b_Yellow);
      printCentered("Rounds to win:", 15, TS_8b_White);
      snprintf(buf, sizeof(buf), "%d", roundsToWin);
      drawItem(buf, 26, sel == 0);
      drawItem("BACK", 40, sel == 1);
      printCentered("R: change", 54, TS_8b_Blue);
      break;

    case SCR_HOWTO:
      printCentered("HOW TO PLAY", 1, TS_8b_Yellow);
      printLeft("L stick: move",     13, TS_8b_White);
      printLeft("R stick: look",     23, TS_8b_White);
      printLeft("Gun fires itself",  33, TS_8b_White);
      printLeft("2 hits wins round", 43, TS_8b_White);
      printCentered("R: back", 54, TS_8b_Blue);
      break;

    case SCR_WAITING:
      printCentered(role == 0 ? "HOST" : "JOIN", 1, TS_8b_Yellow);
      printCentered(role == 0 ? "You are Player A" : "You are Player B", 18,
                    role == 0 ? TS_8b_Blue : TS_8b_Red);
      printCentered(role == 0 ? "Waiting for B..." : "Looking for A...", 32, TS_8b_White);
      printCentered("R: back", 54, TS_8b_Blue);
      break;
  }
}

void goTo(int scr, int s) {
  screen = scr;
  sel = s;
  drawScreen();
}

void enterMenu() {
  state = STATE_MENU;
  prevButtons = display.getButtons();  // a button already held doesn't count as a press
  goTo(SCR_MAIN, 0);
}

void startGame(unsigned long now) {
  matchOver = true;     // makes startRound() reset the scores for a fresh match
  waitRelease = true;
  startRound(now);
  lastFpsTime = now;
  frames = 0;
}

void updateMenu(unsigned long now) {
  uint8_t p = pressedButtons();

  // Move the highlight
  int old = sel;
  if ((p & TSButtonUpperLeft) && sel > 0)               sel--;
  if ((p & TSButtonLowerLeft) && sel < itemCount() - 1) sel++;
  if (sel != old) drawScreen();

  if (!(p & (TSButtonUpperRight | TSButtonLowerRight))) return;  // no select

  switch (screen) {
    case SCR_MAIN:
      if      (sel == 0) startGame(now);           // SINGLEPLAYER: raycaster_v4 vs bot
      else if (sel == 1) goTo(SCR_MULTI, 0);
      else if (sel == 2) goTo(SCR_SETTINGS, 0);
      else               goTo(SCR_HOWTO, 0);
      break;

    case SCR_MULTI:
      if (sel == 2) { goTo(SCR_MAIN, 1); break; }  // BACK
      role = sel;                                  // 0 = host (A), 1 = join (B)
      goTo(SCR_WAITING, 0);
      // TODO (when duel_v3 BLE code is merged): start BLE here with `role`,
      //      and call startGame() once connected.
      break;

    case SCR_SETTINGS:
      if (sel == 0) {                              // cycle 1 -> 3 -> 5 -> 1
        roundsToWin = (roundsToWin == 1) ? 3 : (roundsToWin == 3) ? 5 : 1;
        drawScreen();
      } else {
        goTo(SCR_MAIN, 2);                         // BACK
      }
      break;

    case SCR_HOWTO:   goTo(SCR_MAIN, 3);    break;
    case SCR_WAITING: goTo(SCR_MULTI, role); break;
  }
}


void setup() {
  display.begin();
  display.setFlip(true);
  display.setBrightness(10);
  SerialUSB.begin(9600);
  randomSeed(micros());
  lastFrame = millis();
  enterMenu();

}

void loop() {
  unsigned long now = millis();
  float dt = (now - lastFrame) / 1000.0f;
  lastFrame = now;
  if (dt > 0.1f) dt = 0.1f;

  if (state == STATE_MENU)  { updateMenu(now);  return; }

  if (state == STATE_ROUND_OVER) {
    if (now - stateStart >= ROUND_OVER_MS) {
      if (matchOver) enterMenu();   // match finished -> back to main menu
      else           startRound(now);
    }
    lastFpsTime = now;  // don't count the pause in the FPS
    frames = 0;
    return;
  }

  handleInput(dt);
  updateBot(dt, now);
  if (state != STATE_PLAYING) return;  // the bot just won the round

  castRays();
  projectOpponent();  // uses wallDist[], so runs after castRays()

  if (now - lastShotTime >= FIRE_INTERVAL_MS) playerShoot(now);
  if (state != STATE_PLAYING) return;  // you just won the round

  drawFrame(now);

  frames++;
  if (now - lastFpsTime >= 1000) {
    SerialUSB.print("FPS: ");
    SerialUSB.print(frames);
    SerialUSB.print("  you: ");
    SerialUSB.print(PLAYER_MAX_HITS - playerHits);
    SerialUSB.print("hp  bot: ");
    SerialUSB.print(BOT_MAX_HITS - oppHits);
    SerialUSB.print("hp  rounds: ");
    SerialUSB.print(roundsPlayer);
    SerialUSB.print("-");
    SerialUSB.println(roundsBot);
    frames = 0;
    lastFpsTime = now;
  }
}
