# TinyBoy / ARENA – Interfaces

The shared contract between the watch code, the laptop server and the web app.
**Change this file only by team agreement** – everyone builds against it.

---

## 1. System overview

| Mode | Link | Status |
|---|---|---|
| **Local duel** | Watch A ⇄ Watch B over BLE (no laptop) | ✅ working (`experiments/duel_v1`) |
| **Arena** | Watches ⇄ laptop server over BLE, phones ⇄ laptop over WiFi (web page) | 🚧 in progress |

```
Watch (C++) --BLE--> Laptop server (Python) --WebSocket--> Web app on phones / PC
```

---

## 2. BLE link (watch side)

- Every watch uses the **Nordic UART service** from the TinyCircuits example.
  - Service UUID `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
  - **Write** characteristic (others → watch): `6E400002-…`
  - **Notify** characteristic (watch → others): `6E400003-…`
- Handles on a peripheral watch: **TX char 13 (value 14)**, **RX char 15 (value 16, CCCD 17)**.
- **Max 20 bytes per message.** All game packets are binary, little-endian.
- Fixed addresses: Player A `12:34:00:E1:80:0A`, Player B `12:34:00:E1:80:0B`.
  (Bytes are stored reversed in code: `{0x12,0x34,0x00,0xE1,0x80,0x0A}`.)
- In **Arena** mode **both watches are peripherals** (like Player A) and the laptop is the central.

---

## 3. Packets

### 3.1 Duel packet (watch ⇄ watch) – 11 bytes – `magic = 0xB0`
| Byte | Field | Type | Meaning |
|---|---|---|---|
| 0 | magic | u8 | `0xB0` |
| 1 | seq | u8 | increments every packet |
| 2 | round | u8 | round this data belongs to |
| 3–4 | x | i16 | position × 256 (map squares) |
| 5–6 | y | i16 | position × 256 |
| 7 | angle | u8 | facing, 0–255 = full circle |
| 8 | hitsLanded | u8 | hits I landed on you this round |
| 9 | flags | u8 | bit0 = fired in the last 100 ms |
| 10 | shots | u8 | shots I fired this round (since duel_v3) |

Rule: **the shooter decides hits**. Your damage = the other watch's `hitsLanded` for the current round.
Link: **turn-taking** – B sends, A answers, B sends again (~10/s). A never sends on its own.

### 3.1b Match events (watch → laptop over USB) – since `duel_v3`

The watch plugged into the laptop prints one line per event on its USB serial port (9600 baud).
Lines start with `EVT,`; every other line (FPS debug etc.) must be ignored.
Players are always `A` and `B`, whichever watch is plugged in. Positions are hundredths of a map square (`250` = 2.50).

| Line | Meaning |
|---|---|
| `EVT,HELLO,A` | watches connected; this line came from watch A |
| `EVT,MATCH_START,A` | a new match begins |
| `EVT,ROUND_START,1` | round number starts |
| `EVT,SHOT,A,1` | A fired; `1` = hit, `0` = miss (only the plugged-in watch's own shots) |
| `EVT,HIT,A,B,1` | A hit B; B has 1 hp left |
| `EVT,POS,A,250,250,B,1350,1350` | positions, 5× per second (B part missing until B's first packet) |
| `EVT,ROUND_END,1,A,2,1,7,5,34210` | round 1, winner A (`D` = draw), hitsA, hitsB, shotsA, shotsB, duration ms |
| `EVT,MATCH_END,A,3,1` | match winner, rounds A, rounds B |
| `EVT,MATCH_ABORT` | connection lost mid-match |

The laptop bridge (`laptop/bridge.py`) turns these into `matches.json` (see section 4b).

### 3.2 Arena packets (proposal – to confirm)

**Watch → laptop: `STATE` – 10 bytes – `magic = 0xC1`**
| Byte | Field | Type | Meaning |
|---|---|---|---|
| 0 | magic | u8 | `0xC1` |
| 1 | playerId | u8 | 0–3 |
| 2 | seq | u8 | |
| 3–4 | x | i16 | × 256 |
| 5–6 | y | i16 | × 256 |
| 7 | angle | u8 | 0–255 |
| 8 | flags | u8 | bit0 fired, bit1 ready (lobby) |
| 9 | targetId | u8 | id of who is in my crosshair when I fired (`0xFF` = nobody) |

**Laptop → watch: `WORLD` – up to 20 bytes – `magic = 0xC2`**
| Byte | Field | Meaning |
|---|---|---|
| 0 | magic `0xC2` | |
| 1 | gameState | 0 lobby, 1 playing, 2 round over, 3 paused |
| 2 | myHealth | your health |
| 3 | flags | bit0 you were hit, bit1 you scored a hit, bit2 flare active |
| 4 | entityCount | number of entities that follow (max 5) |
| 5.. | entities | 3 bytes each: `id`, `x` (u8, ×16), `y` (u8, ×16) |

Entity ids: 0–3 players, 10+ bots, 20+ power-ups. The watch picks sprite colour from the id.

**Laptop → watch: `EVENT` – `magic = 0xC3`**: `[magic, eventType, a, b]` – round over, match over, power-up picked up, etc.

> In Arena mode the **laptop decides hits** (server-authoritative): the watch only reports *who* was in its crosshair when it fired.

---

## 4. Laptop server (Python)

- `bleak` connects to each watch, subscribes to notify `6E400003-…`, writes to `6E400002-…`.
- Game loop at 20 Hz: read `STATE` from watches → update world (movement, hits, bots, power-ups) → send each watch its `WORLD`.
- Pushes the full world as JSON over a **WebSocket** to the web app.

### 4b. `matches.json` (written by `laptop/bridge.py`, read by everything else)

**Only `bridge.py` writes this file.** The bot, heatmaps, etc. only read it.
A ready-made example is in `laptop/sample/` (built from `laptop/sample/events.log`) – build against that, no watch needed.

```json
{
  "players": {
    "A": { "name": "Player A", "matches": 1, "wins": 1, "losses": 0,
           "rounds_won": 3, "shots": 33, "hits": 7 },
    "B": { ... }
  },
  "matches": [
    {
      "id": 1, "started": "2026-11-09T18:37:32", "ended": "2026-11-09T18:40:10",
      "status": "finished",              // or "aborted" / "in_progress"
      "winner": "A",                     // null if aborted
      "players": { "A": "Player A", "B": "Player B" },
      "score": { "A": 3, "B": 1 },
      "rounds": [
        { "round": 1, "winner": "A",     // "draw" possible
          "hits":  { "A": 2, "B": 1 },
          "shots": { "A": 9, "B": 6 },
          "duration_s": 24.6 }
      ],
      "positions_file": "positions/match_1.json"
    }
  ]
}
```

`positions/match_<id>.json` (path relative to `matches.json`) – movement trail for heatmaps,
5 samples per second: `{ "A": [[x, y, t], ...], "B": [[x, y, t], ...] }` – x, y in map squares (0–16), t = seconds since match start.

Accuracy = `hits / shots`. A finished match only counts towards wins/losses/matches.

### WebSocket messages (server → web)
```json
{ "type": "world", "t": 12345,
  "players": [{"id":0,"name":"A","x":2.5,"y":2.5,"angle":0.0,"hp":2,"score":1}],
  "bots":    [{"id":10,"x":8.5,"y":8.5,"hp":2}],
  "powerups":[{"id":20,"kind":"flare","x":4.5,"y":11.5}],
  "feed":    ["A hit B"] }
```

### WebSocket messages (web → server)
```json
{ "type": "referee", "action": "start" | "pause" | "restart", "mode": "ffa" }
{ "type": "drop", "kind": "flare" | "airstrike" | "health", "x": 8, "y": 8 }
```

---

## 5. Watch game "shape" (for the menu)

Every game on the watch exposes the same three functions so the menu can launch it:

```cpp
void pongSetup();      // called once when chosen from the menu
void pongLoop();       // called every frame while playing
bool pongFinished();   // return true to go back to the menu
```

Same pattern for `invadersSetup/Loop/Finished`, `pacmanSetup/…`, `arenaSetup/…`.

---

## 6. Buttons and screen

- Screen is flipped with `display.setFlip(true)`.
- Current mapping: **upper-left = forward, lower-left = back, upper-right = turn left, lower-right = turn right**. Fire is automatic.
- With a joystick (Wireling Adapter + Joystick Wireling): joystick = move/strafe, buttons = turn.

## 7. Sound hooks
`playShotSound()`, `playHitSound()`, `playHurtSound()`, `playRoundOverSound(result)` – empty stubs in the game code, filled in by the buzzer owner.
