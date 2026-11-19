# ARENA game server (work in progress)

The referee for the Arena mode: it holds the one true game state and decides hits
(server-authoritative). Pure Python, no extra packages needed yet.

| File | What it does |
|---|---|
| `world.py` | map, line of sight, BFS pathfinding, map playability check |
| `protocol.py` | binary packets watch <-> laptop (`docs/interfaces.md` section 3.2) |
| `rules.py` | `Game`: lobby (everyone ready) -> playing -> over, hit validation, health, respawn at the farthest spawn, speed check against teleporting, time limit with tie-break (kills, fewer deaths, accuracy, else draw), pause/restart, `FreeForAll` mode, `snapshot()` for the web app |
| `bots.py` | `BotBrain`: patrol / hunt / attack / flee with pathfinding |
| `server.py` | All-bot demo match (no watches) |
| `arena.py` | `ArenaServer`: the real-time 20 Hz loop. One `Link` per watch, builds each watch its own WORLD packet (only the enemies it can see: dark maze) and EVENT packets, counts lost/bad packets, aborts the match if a watch goes silent for 3 s, returns to the lobby after a match |
| `fakewatch.py` | Software watches: `AutoWatch` (computer player) and `KeyboardWatch` (you). They move themselves and only learn about the world from WORLD/EVENT packets, like the real firmware |
| `sim.py` | A whole 2-player match through the real packet path, in simulated time (fast), with optional packet loss |
| `play.py` | Play a match yourself on the laptop with the keyboard (Windows) |
| `watch_link.py` | The laptop plays Player B over Bluetooth against one real watch (duel packets) and prints the match EVT lines |
| `duel_bot.py` | Player B's brain for `watch_link.py`: the server bot (BFS, faces you, shoots with line of sight), easy / normal / hard |

## Try it (from the `laptop` folder)
```powershell
python -m server.sim --seed 1                  # two fake watches play a full match (a few seconds)
python -m server.sim --seed 1 --loss 0.1       # same with 10% of packets lost
python -m server.play                          # YOU vs a fake watch. Press R to ready up. W/S walk, Q/E strafe, A/D turn, X quit
```
```powershell
python -m server.server --bots 4 --seconds 120 --seed 1     # fast-forward a bot match
python -m server.server --bots 4 --realtime                 # same, at normal speed
python -m unittest -v                                       # run the tests
```

## EVENT packets (proposal, needs agreement with Shinji; not yet in docs/interfaces.md)
`[0xC3, kind, a, b]`: 1 = match start, 2 = match end (a = winner id, 0xFF = draw), 3 = respawn (a, b = spawn x, y in 1/16 squares: the watch must move there; repeated every tick until the watch arrives),
4 = kill (a = killer, b = victim). WORLD `flags`: bit0 you were hit, bit1 you landed a hit (each stays on for 0.3 s).
The `Link` class is the only thing a Bluetooth layer has to replace: any object with `to_server` / `to_watch` queues works.

## Laptop as Player B against one watch (`watch_link.py`)
The laptop connects to a watch running the normal firmware and plays the 2-player duel as Player B,
using the duel packet (docs/interfaces.md 3.1), not the Arena packets.

1. Flash `firmware/tinyboy` (from main) to the watch. On the watch: MULTIPLAYER -> HOST (A). It shows "Waiting for B".
2. Turn Bluetooth on in Windows. Close nRF Connect and do not pair the watch in Windows settings.
3. From the `laptop` folder:
```powershell
python -m server.watch_link --scan                  # the watch should appear as TinyboyA  0A:80:E1:00:34:12
python -m server.watch_link --still                 # step 1: Player B stands still at (13.5, 13.5) facing west
python -m server.watch_link                         # step 2: the server bot plays (normal)
python -m server.watch_link --difficulty hard       # easy / normal / hard
python -m server.watch_link --events match.log      # also append the EVT lines to a file
python -m server.watch_link --verbose               # also print every packet
```
**Step 1 works if** the watch leaves "Waiting for B" and, after you walk towards the bottom-right corner,
you see the red Player B standing there. You can shoot it: after 2 hits the round ends on both sides.

**Step 2 (`duel_bot.py`)**: Player B is the ARENA server's `BotBrain`: it walks the maze with BFS, turns to face you
when it can see you and shoots when it has line of sight. Shooter decides hits: `Game.try_hit` decides whether each
of its shots lands (range, wall, aim, accuracy roll). Each round both players go back to their spawns.

| Difficulty | Accuracy at point blank | Reaction | Time between shots | Turn speed |
|---|---|---|---|---|
| easy | 45% | 1.0 s | 0.9 s | 2.0 rad/s |
| normal | 75% | 0.6 s | 0.7 s | 3.0 rad/s |
| hard | 90% | 0.3 s | 0.5 s | 4.0 rad/s |

**Match events**: the same `EVT,...` lines Player B's firmware prints (docs/interfaces.md 3.1b), printed the moment
they happen: `HELLO`, `MATCH_START`, `ROUND_START`, `SHOT` (the bot's own shots), `HIT` (both ways), `POS` (5 per second,
A first), `ROUND_END`, `MATCH_END`, and `MATCH_ABORT` if the link drops or you press Ctrl+C mid-match. Every other line
this program prints starts with something else, so `bridge.py` ignores it. A file written with `--events` works
today with `python bridge.py --replay match.log --name-a Watch --name-b "Laptop bot"`.
Use only ONE event source per match: the watch also prints its own EVT lines on USB.

How it behaves (copied from Player B in `firmware/tinyboy/tinyboy.ino`):
- B speaks first; sends again once the watch answered and 100 ms passed, or after 300 ms without an answer.
- Shooter decides hits: B's damage = the watch's hitsLanded, only for the same round number.
- After a round, B keeps sending the final counts for the 2.5 s round-over screen, then starts the next round
  (and catches up if the watch is already on a later round). Match length = the watch's "rounds" setting.
- Angle uses the firmware formula `(atan2(dirY, dirX) + pi) / (2 pi) * 255` (west = 255, east = 127).
- When the watch leaves to the menu it restarts itself, so the link drops; the program then prints a summary and exits.

Tests (`tests/test_watch_link.py`, `tests/test_duel_bot.py`) run it against a Python copy of Player A's firmware logic
(a still and a moving, shooting watch player), with packet loss, against a fake Bluetooth client in real time, and
through `bridge.py`'s own parser and match tracker. Not yet tried with a real watch.

**Known duel limitation (same for two watches):** each side ends a round from its own counters. If both players land
their winning hit within about one packet exchange and a packet is lost, the two sides can record different winners
and drift apart for the rest of the match. In simulation: 0 of 48 matches without loss, 3 of 48 with 20% loss.
Fixing it needs a protocol change (e.g. confirming the round result), i.e. firmware work.

## Known limitations
- Tested only with fake watches in simulated time: no real Bluetooth, no real latency. `play.py` key handling is unit-tested, but
  has not been tried by hand in a real console.
- In a 1v1 bot match the bots often never meet (half of them end on the 180 s time limit). Real
  players are fine; bot hunting needs work for the 2-player case (plan step 6).

## Not done yet
- BLE link to real watches (`bleak`; reuse UUIDs from `ble_bridge_test.py`): implement the `Link` queues
- WebSocket feed for the web app (`Game.snapshot()` already has the right shape)
- Emitting `EVT,` lines so `bridge.py`, the Telegram bot and heatmaps work unchanged
- More modes (team deathmatch, king of the hill), power-ups, map generator
- Watch-side Arena code: needs agreeing with Shinji (STATE/WORLD packet format)

The map in `world.py` is a copy of `worldMap` in `firmware/tinyboy/tinyboy.ino`; keep them in sync.
