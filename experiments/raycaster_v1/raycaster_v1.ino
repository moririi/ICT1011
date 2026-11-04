/*
 * raycaster_v1 - first TinyBoy engine test
 * A walls-only 3D raycaster on the TinyScreen+, moved with the 4 built-in buttons.
 *
 * Raycasting approach (DDA grid stepping + camera plane) adapted from
 * Lode Vandevenne's raycasting tutorial: https://lodev.org/cgtutor/raycasting.html
 * -> add this to docs/ATTRIBUTION.md
 */
#include <Wire.h>
#include <SPI.h>
#include <TinyScreen.h>
#include <math.h>

TinyScreen display = TinyScreen(TinyScreenPlus);

// ---------------- Screen ----------------
const int SCREEN_W = 96;
const int SCREEN_H = 64;

// 8-bit TinyScreen colours (if a name doesn't compile, swap it for another TS_8b_ colour)
const uint8_t COL_CEILING = TS_8b_Black;
const uint8_t COL_FLOOR   = TS_8b_Gray;
const uint8_t COL_BLUE_LIGHT = TS_8b_Blue;      // wall type 1, east/west faces
const uint8_t COL_BLUE_DARK  = TS_8b_DarkBlue;  // wall type 1, north/south faces (darker = fake lighting)
const uint8_t COL_RED_LIGHT  = TS_8b_Red;       // wall type 2
const uint8_t COL_RED_DARK   = TS_8b_DarkRed;

// ---------------- Map ----------------
// 0 = empty, 1 = blue wall, 2 = red wall.
// The border must be solid so every ray eventually hits a wall.
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
float posX = 2.5f, posY = 2.5f;     // position, in map squares
float dirX = 1.0f, dirY = 0.0f;     // direction the player faces
float planeX = 0.0f, planeY = 0.66f; // camera plane, perpendicular to dir (0.66 = ~66 degree field of view)

// ---------------- Per-column results of the raycast ----------------
uint8_t wallTop[SCREEN_W];     // first screen row of the wall in this column
uint8_t wallBottom[SCREEN_W];  // first screen row below the wall
uint8_t wallColour[SCREEN_W];
float   wallDist[SCREEN_W];    // distance to the wall - needed later to hide the opponent sprite behind walls

uint8_t lineBuf[SCREEN_W];     // one row of pixels, sent to the OLED at a time

// ---------------- Helpers ----------------
bool isWall(float x, float y) {
  int mx = (int)x;
  int my = (int)y;
  if (mx < 0 || my < 0 || mx >= MAP_W || my >= MAP_H) return true;
  return worldMap[my][mx] != 0;
}

// Move one axis at a time so the player slides along walls instead of sticking
void tryMove(float dx, float dy) {
  const float margin = 0.2f;  // keep a little distance from walls
  float checkX = posX + dx + (dx > 0 ? margin : -margin);
  float checkY = posY + dy + (dy > 0 ? margin : -margin);
  if (!isWall(checkX, posY)) posX += dx;
  if (!isWall(posX, checkY)) posY += dy;
}

// Rotate both the direction and the camera plane by angle a (radians)
void rotate(float a) {
  float c = cosf(a), s = sinf(a);
  float oldDirX = dirX;
  dirX = dirX * c - dirY * s;
  dirY = oldDirX * s + dirY * c;
  float oldPlaneX = planeX;
  planeX = planeX * c - planeY * s;
  planeY = oldPlaneX * s + planeY * c;
}

// ---------------- Input ----------------
// Uses the TinyScreen+'s built-in buttons for now; the joystick replaces this later (input.cpp).
// Because the screen is flipped, the buttons may feel mirrored - swap the names below if so.
void handleInput(float dt) {
  uint8_t buttons = display.getButtons();
  const float moveSpeed = 2.5f * dt;  // map squares per second
  const float turnSpeed = 2.5f * dt;  // radians per second

  if (buttons & TSButtonUpperLeft)  tryMove(dirX * moveSpeed, dirY * moveSpeed);    // forward
  if (buttons & TSButtonLowerLeft)  tryMove(-dirX * moveSpeed, -dirY * moveSpeed);  // back
  if (buttons & TSButtonUpperRight) rotate(-turnSpeed);                             // turn one way
  if (buttons & TSButtonLowerRight) rotate(turnSpeed);                              // turn the other way
}

// ---------------- Raycasting ----------------
// For every screen column, cast one ray and find the distance to the first wall it hits.
void castRays() {
  for (int x = 0; x < SCREEN_W; x++) {
    // -1 on the left edge of the screen, +1 on the right edge
    float cameraX = 2.0f * x / SCREEN_W - 1.0f;
    float rayDirX = dirX + planeX * cameraX;
    float rayDirY = dirY + planeY * cameraX;

    // Which map square we're in
    int mapX = (int)posX;
    int mapY = (int)posY;

    // Distance the ray travels to cross one full square in x / in y
    float deltaX = (rayDirX == 0) ? 1e30f : fabsf(1.0f / rayDirX);
    float deltaY = (rayDirY == 0) ? 1e30f : fabsf(1.0f / rayDirY);

    // Step direction and distance to the first square edge in x / in y
    int stepX, stepY;
    float sideX, sideY;
    if (rayDirX < 0) { stepX = -1; sideX = (posX - mapX) * deltaX; }
    else             { stepX =  1; sideX = (mapX + 1.0f - posX) * deltaX; }
    if (rayDirY < 0) { stepY = -1; sideY = (posY - mapY) * deltaY; }
    else             { stepY =  1; sideY = (mapY + 1.0f - posY) * deltaY; }

    // DDA: step square by square until we hit a wall
    int side = 0;  // 0 = hit an east/west face, 1 = hit a north/south face
    while (worldMap[mapY][mapX] == 0) {
      if (sideX < sideY) { sideX += deltaX; mapX += stepX; side = 0; }
      else               { sideY += deltaY; mapY += stepY; side = 1; }
    }

    // Perpendicular distance (avoids the fisheye effect)
    float dist = (side == 0) ? (sideX - deltaX) : (sideY - deltaY);
    if (dist < 0.05f) dist = 0.05f;
    wallDist[x] = dist;

    // Wall height on screen is inversely proportional to distance
    int lineH = (int)(SCREEN_H / dist);
    int top = SCREEN_H / 2 - lineH / 2;
    int bottom = SCREEN_H / 2 + lineH / 2;
    if (top < 0) top = 0;
    if (bottom > SCREEN_H) bottom = SCREEN_H;
    wallTop[x] = top;
    wallBottom[x] = bottom;

    // Colour by wall type, darker on one side for simple shading
    uint8_t type = worldMap[mapY][mapX];
    if (type == 2) wallColour[x] = side ? COL_RED_DARK : COL_RED_LIGHT;
    else           wallColour[x] = side ? COL_BLUE_DARK : COL_BLUE_LIGHT;
  }
}

// ---------------- Drawing ----------------
// Fill the screen row by row using the per-column wall data.
void drawFrame() {
  display.setX(0, SCREEN_W - 1);
  display.setY(0, SCREEN_H - 1);
  display.startData();
  for (int y = 0; y < SCREEN_H; y++) {
    for (int x = 0; x < SCREEN_W; x++) {
      if (y < wallTop[x])         lineBuf[x] = COL_CEILING;
      else if (y < wallBottom[x]) lineBuf[x] = wallColour[x];
      else                        lineBuf[x] = COL_FLOOR;
    }
    display.writeBuffer(lineBuf, SCREEN_W);
  }
  display.endTransfer();
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
}

void loop() {
  unsigned long now = millis();
  float dt = (now - lastFrame) / 1000.0f;  // seconds since last frame, so speed doesn't depend on FPS
  lastFrame = now;
  if (dt > 0.1f) dt = 0.1f;

  handleInput(dt);
  castRays();
  drawFrame();

  frames++;
  if (now - lastFpsTime >= 1000) {
    SerialUSB.print("FPS: ");
    SerialUSB.println(frames);
    frames = 0;
    lastFpsTime = now;
  }
}
