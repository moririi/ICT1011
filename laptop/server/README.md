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
