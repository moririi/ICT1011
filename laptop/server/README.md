# ARENA game server (work in progress)

The referee for the Arena mode: it holds the one true game state and decides hits
(server-authoritative). Pure Python, no extra packages needed yet.

| File | What it does |
|---|---|
| `world.py` | map, line of sight, BFS pathfinding, map playability check |
| `protocol.py` | binary packets watch <-> laptop (`docs/interfaces.md` section 3.2) |
| `rules.py` | `Game`: hit validation, health, respawn, scoring, pause/restart, game modes (`FreeForAll` so far), `snapshot()` for the web app |
| `bots.py` | `BotBrain`: patrol / hunt / attack / flee with pathfinding |
| `server.py` | 20 Hz loop. Currently an all-bot demo match |

## Try it (from the `laptop` folder)
```powershell
python -m server.server --bots 4 --seconds 120 --seed 1     # fast-forward a bot match
python -m server.server --bots 4 --realtime                 # same, at normal speed
python -m unittest -v                                       # run the tests
```

## Not done yet
- BLE link to real watches (`bleak`; reuse UUIDs from `ble_bridge_test.py`)
- WebSocket feed for the web app (`Game.snapshot()` already has the right shape)
- Emitting `EVT,` lines so `bridge.py`, the Telegram bot and heatmaps work unchanged
- More modes (team deathmatch, king of the hill), power-ups, lobby, map generator
- Watch-side Arena code: needs agreeing with Shinji (STATE/WORLD packet format)

The map in `world.py` is a copy of `worldMap` in `firmware/tinyboy/tinyboy.ino`; keep them in sync.
