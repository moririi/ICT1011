# Laptop side – bridge, Telegram bot, heatmaps, replays, database

```
Watch (USB) → bridge.py → matches.json → bot.py → Telegram (@ict1011_arena_bot)
                               │
                               └→ heatmap.py · replay.py · database.py (arena.db)
```
- `bridge.py` – reads match events from the watch, saves `matches.json` (the ONLY writer)
- `bot.py` – reads `matches.json`, answers commands, posts auto match reports
- `heatmap.py` – PNG of where each player spent their time in a match
- `replay.py` – animated GIF replay of a match (top-down view of the maze)
- `database.py` – copies `matches.json` + positions into SQLite (`arena.db`) for stats like `/top`
- Formats: `docs/interfaces.md` (3.1b events, 4b matches.json)

## 1. Install (once)
```powershell
cd laptop
pip install pyserial python-dotenv "python-telegram-bot[job-queue]"
pip install matplotlib numpy pillow      # heatmaps + replays
```
SQLite needs nothing extra – it is built into Python.

## 2. Create `laptop/.env` (never commit it – it's in .gitignore)
```env
TELEGRAM_TOKEN=<get it privately from Chee Hsien>
TELEGRAM_CHAT_ID=<where auto reports go>
TELEGRAM_THREAD_ID=<topic id, or leave empty>
```
**Finding the IDs**
- **Group topic (easiest):** right-click the topic → *Copy link* → `https://t.me/c/1234567890/47`
  → `TELEGRAM_CHAT_ID=-1001234567890` (put `-100` in front), `TELEGRAM_THREAD_ID=47`
- **Your private chat:** message @userinfobot → your ID is the chat ID; leave THREAD_ID empty
- **Or via getUpdates:** STOP bot.py first (otherwise the result is empty), send the bot a message,
  then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `"chat": {"id": ...}`
  and `"message_thread_id"`. Never share this link – it contains the token.

The bot must be a **member of the group** (make it admin if members can't post).

## 3. Run (two terminals, both in `laptop/`)
```powershell
python bot.py                                          # terminal 1
python bridge.py --port COM5 --name-a Alice --name-b Bob   # terminal 2
```
- Close the Arduino Serial Monitor first – only one program can use the COM port.
- Only ONE copy of bot.py may run at a time (otherwise: "Conflict: terminated by other getUpdates").
- The bot only answers while bot.py is running on someone's laptop.

## Test without watches
```powershell
python bridge.py --replay sample/events.log --data my_test
```
Then point the bot at `my_test/` – everything works like a real match.

## Live web dashboard (projector / phones)

`bridge.py --live` also serves `web/index.html` and streams the live watch duel to it:
```powershell
pip install aiohttp
python bridge.py --port COM5 --name-a Alice --name-b Bob --live
```
- Laptop: open **http://localhost:8000/** and press **Connect** (the address is filled in already).
- Phones / projector on the same WiFi: **http://&lt;laptop IP&gt;:8000/** (find the IP with `ipconfig` → IPv4 Address).
  Windows may ask to allow Python through the firewall: allow it on private networks.
- Shows the maze, both players moving, health, rounds won (K) / lost (D) and a hit / round feed.
  The timer shows match time. Referee buttons only work with the arena server, not the watch duel.
- Test without watches: `python bridge.py --replay sample/events.log --data my_test --live` (plays at real speed).

**Referee (pause / resume both watches from the dashboard)**: start the bridge with a password,
```powershell
python bridge.py --port COM5 --name-a Alice --name-b Bob --live --host-token 1234
```
then type `1234` into **Host Token** on the dashboard. **Pause Match** freezes both watches ("PAUSED by the referee");
the players can quit but only the referee can resume. Pause works during a round. Restart / settings don't apply to the duel.

**Match report**: after a match, **Generate Match Report** writes a report from the match statistics
(rounds, fastest round, flawless rounds, comebacks). Optional: put `ANTHROPIC_API_KEY=...` in `laptop/.env`
and an AI model writes it instead (falls back to the statistics report if anything goes wrong).

**One watch vs the laptop bot** (watch on MULTIPLAYER → HOST (A), nothing plugged in by USB needed):
```powershell
python -u -m server.watch_link | python bridge.py --stdin --name-a You --name-b "Laptop bot" --live
```
watch_link's status lines still show in the window; its EVT lines go into matches.json, Telegram and the dashboard.
Use only one event source per match (don't also run `bridge.py --port` for the same watch).

## Bot commands
| Command | What it does |
|---|---|
| `/leaderboard` | Player rankings by wins |
| `/stats <name>` | Career stats for one player |
| `/lastmatch` | Details of the most recent finished match |
| `/heatmap [match_id]` | Picture of where each player spent their time (default: latest match) |
| `/replay [match_id]` | Animated GIF replay of a match (default: latest match) |
| `/top [wins\|winrate\|hits\|rounds]` | Top players from the match database (round win rate, hits per round), plus the fastest round |
| `/start` · `/help` | Show the command list |

Plus an automatic report when a match ends.

## Heatmaps, replays and the database without Telegram
Run in `laptop/`. `--data` is the folder that has `matches.json` (default: the current folder).
```powershell
python heatmap.py --data sample --match 1          # saves heatmap_match_1.png
python replay.py --data sample --match 1           # saves replay_match_1.gif (6x speed, change with --speed)
python database.py --data sample                   # builds sample/arena.db, prints the top players
python database.py --data sample --top winrate    # rank by wins | winrate | hits | rounds
```
- `WORLD_MAP` in `heatmap.py` is a copy of `worldMap` in `firmware/tinyboy/tinyboy.ino` – update it if the map changes.
- `arena.db` is rebuilt from `matches.json` every time `/top` or `database.py` runs, so never edit it by hand.
  `*.db` is in `.gitignore`.
- Don't commit the generated `.png` / `.gif` files.