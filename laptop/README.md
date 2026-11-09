# Laptop side – bridge + Telegram bot

```
Watch (USB) → bridge.py → matches.json → bot.py → Telegram (@ict1011_arena_bot)
```
- `bridge.py` – reads match events from the watch, saves `matches.json` (the ONLY writer)
- `bot.py` – reads `matches.json`, answers commands, posts auto match reports
- Formats: `docs/interfaces.md` (3.1b events, 4b matches.json)

## 1. Install (once)
```powershell
cd laptop
pip install pyserial python-dotenv "python-telegram-bot[job-queue]"
```

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

## Bot commands
`/start` · `/help` · `/leaderboard` · `/stats <name>` · `/lastmatch` – plus an automatic report when a match ends.