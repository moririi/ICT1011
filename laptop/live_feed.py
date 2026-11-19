"""
live_feed.py - serves the web dashboard (web/index.html) and streams the LIVE match to it.

Started by bridge.py with --live. Runs a small web server in a background thread:
    http://<laptop-ip>:8000/                 the dashboard (open it on the laptop, a projector or any phone
                                             on the same WiFi)
    ws://<laptop-ip>:8000/ws/spectator       WebSocket the dashboard connects to by default

It turns the watch's EVT lines (docs/interfaces.md 3.1b) into the dashboard's messages:
    {"type": "state", "match": {...}, "map": [[...]], "players": [...]}   ~5x per second
    {"type": "event", "event": {"id", "killer", "victim", "text", "time"}} on hits / rounds / match end

Referee buttons (bridge.py --live --host-token <password>): the dashboard's Pause / Resume button sends
    {"type": "referee_command", "action": "pause" | "resume", "host_token": "..."}
and, if the password matches, the bridge sends CMD,PAUSE / CMD,RESUME to the watch on USB
(docs/interfaces.md 3.1c). Both watches freeze; the watch reports back with EVT,PAUSE / EVT,RESUME.

Match report: POST /api/commentary returns a report of the newest finished match, written from its
statistics. If ANTHROPIC_API_KEY is set (environment or laptop/.env), an AI model writes it instead,
from the same statistics; any problem falls back to the statistics report.

Setup (once):  pip install aiohttp
"""
import asyncio
import json
import math
import os
import random
import threading
import time
from pathlib import Path

from aiohttp import WSMsgType, web

try:  # ANTHROPIC_API_KEY can live in laptop/.env next to the Telegram token
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Same maze as worldMap in firmware/tinyboy/tinyboy.ino (1 = wall). Keep them in sync.
MAP = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1],
    [1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1],
    [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1],
    [1, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
]

MAX_HITS = 2          # hits to lose a round (MAX_HITS in the firmware)
STATE_INTERVAL = 0.2  # seconds between state messages
COLOURS = {"A": "#3b82f6", "B": "#ef4444"}  # blue host, red join - same as on the watches


class LiveFeed:
    def __init__(self, names, port=8000, host_token=None, send_command=None, last_match=None):
        self.port = port
        self.host_token = host_token        # password for the referee buttons (None = buttons off)
        self.send_command = send_command    # function(str) -> bool that writes to the watch's USB port
        self.last_match = last_match        # function() -> newest finished match dict (matches.json)
        self.paused_at = None
        self.between_rounds = False
        self.names = dict(names)
        self.clients = set()
        self.loop = None
        self.lock = threading.Lock()
        self.event_no = 0
        self.last_sent = 0.0
        self.match_id = "-"
        self.status = "lobby"
        self.round = 0
        self.t0 = time.time()
        self.players = {}
        self.reset_players()

    # ------------------------------------------------------------ state
    def reset_players(self):
        start = {"A": (2.5, 2.5, 0.0), "B": (13.5, 13.5, math.pi)}
        for p, (x, y, a) in start.items():
            self.players[p] = {"x": x, "y": y, "angle": a, "hits_taken": 0,
                               "rounds_won": 0, "rounds_lost": 0}

    def elapsed(self):
        end = self.paused_at if self.paused_at is not None else time.time()   # the timer stops while paused
        return max(0.0, end - self.t0)

    def snapshot(self):
        players = []
        for p in ("A", "B"):
            st = self.players[p]
            players.append({
                "id": p,
                "name": self.names.get(p) or f"Player {p}",
                "color": COLOURS[p],
                "x": st["x"], "y": st["y"], "angle": st["angle"],
                "health": round(100 * max(0, MAX_HITS - st["hits_taken"]) / MAX_HITS),
                "kills": st["rounds_won"],     # shown as rounds won
                "deaths": st["rounds_lost"],   # shown as rounds lost
                "alive": True,
            })
        return {
            "type": "state",
            "match": {
                "id": self.match_id,
                "mode": "duel",
                "status": self.status,
                "round": max(1, self.round),
                "duration_seconds": 0,
                "remaining_seconds": round(self.elapsed(), 1),  # the dashboard timer shows match time
            },
            "map": MAP,
            "players": players,
        }

    def _event(self, text, killer="", victim=""):
        self.event_no += 1
        secs = int(self.elapsed())
        return {"type": "event", "event": {
            "id": f"{self.match_id}-{self.event_no}",
            "killer": killer, "victim": victim, "text": text,
            "time": f"{secs // 60:02d}:{secs % 60:02d}",
        }}

    def name(self, p):
        return self.names.get(p) or f"Player {p}"

    # ------------------------------------------------------------ events from bridge.py
    def on_event(self, f, match_id=None):
        """f = the fields after 'EVT' (already validated by bridge.py)."""
        out = []
        with self.lock:
            kind = f[0]
            if kind == "MATCH_START":
                self.match_id = str(match_id or self.match_id)
                self.status = "playing"
                self.round = 0
                self.t0 = time.time()
                self.paused_at = None
                self.reset_players()
                out.append(self._event(f"Match started: {self.name('A')} vs {self.name('B')}"))
            elif kind == "ROUND_START":
                self.round += 1
                self.between_rounds = False
                for p in ("A", "B"):
                    self.players[p]["hits_taken"] = 0
            elif kind == "POS":
                for i in range(1, len(f), 3):
                    p, x, y = f[i], int(f[i + 1]) / 100, int(f[i + 2]) / 100
                    st = self.players[p]
                    dx, dy = x - st["x"], y - st["y"]
                    if dx * dx + dy * dy > 0.0004:   # moved: face the way they are walking
                        st["angle"] = math.atan2(dy, dx)
                    st["x"], st["y"] = x, y
            elif kind == "HIT":
                shooter, target, hp_left = f[1], f[2], int(f[3])
                self.players[target]["hits_taken"] = MAX_HITS - hp_left
                out.append(self._event(f"{self.name(shooter)} hit {self.name(target)}",
                                       self.name(shooter), self.name(target)))
            elif kind == "ROUND_END":
                self.between_rounds = True
                winner = f[2]
                if winner in ("A", "B"):
                    loser = "B" if winner == "A" else "A"
                    self.players[winner]["rounds_won"] += 1
                    self.players[loser]["rounds_lost"] += 1
                    out.append(self._event(f"Round {self.round} won by {self.name(winner)}",
                                           self.name(winner), self.name(loser)))
                else:
                    out.append(self._event(f"Round {self.round}: draw"))
            elif kind == "MATCH_END":
                self.status = "finished"
                w = f[1]
                out.append(self._event(f"{self.name(w)} wins the match {f[2]}-{f[3]}!"))
            elif kind == "PAUSE":
                if self.status == "playing":
                    self.status = "paused"
                    self.paused_at = time.time()
                who = "the referee" if f[1] == "R" else self.name(f[1])
                out.append(self._event(f"Match paused by {who}"))
            elif kind == "RESUME":
                if self.status == "paused":
                    self.status = "playing"
                    if self.paused_at is not None:
                        self.t0 += time.time() - self.paused_at
                    self.paused_at = None
                out.append(self._event("Match resumed"))
            elif kind == "MATCH_ABORT":
                self.status = "lobby"
                self.paused_at = None
                out.append(self._event("Match aborted (connection lost)"))

            now = time.time()
            if kind != "POS" or now - self.last_sent >= STATE_INTERVAL:
                self.last_sent = now
                out.append(self.snapshot())
        for msg in out:
            self.send(msg)

    # ------------------------------------------------------------ web server (background thread)
    def send(self, msg):
        if self.loop is None or not self.clients:
            return
        data = json.dumps(msg)
        asyncio.run_coroutine_threadsafe(self._broadcast(data), self.loop)

    async def _broadcast(self, data):
        for ws in list(self.clients):
            try:
                await ws.send_str(data)
            except Exception:
                self.clients.discard(ws)

    async def _index(self, request):
        return web.FileResponse(WEB_DIR / "index.html")

    async def _ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        with self.lock:
            snap = self.snapshot()
        await ws.send_str(json.dumps(snap))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    ok, text = self.referee(msg.data)
                    await ws.send_str(json.dumps({"type": "referee_response", "ok": ok, "message": text}))
        finally:
            self.clients.discard(ws)
        return ws

    # ------------------------------------------------------------ referee buttons
    def referee(self, raw):
        """Handle one message from a dashboard. Returns (ok, message for the dashboard)."""
        try:
            msg = json.loads(raw)
        except ValueError:
            return False, "Bad message."
        if not isinstance(msg, dict) or msg.get("type") != "referee_command":
            return False, "Unknown message."
        if not self.host_token:
            return False, "Referee is off. Start the bridge with --host-token <password> to turn it on."
        if str(msg.get("host_token", "")) != self.host_token:
            return False, "Wrong host token."
        action = msg.get("action")
        if action not in ("pause", "resume"):
            return False, "The watch duel only supports Pause / Resume (no restart or settings)."
        if self.send_command is None:
            return False, "No watch on USB - start the bridge with --port COMx to use the referee."
        if action == "pause" and (self.status != "playing" or self.between_rounds):
            return False, "Nothing to pause - pause works during a round."
        if action == "resume" and self.status != "paused":
            return False, "The match isn't paused."
        if not self.send_command("CMD,PAUSE" if action == "pause" else "CMD,RESUME"):
            return False, "Couldn't reach the watch (is it plugged in?)."
        return True, f"{action.capitalize()} sent to the watches."

    # ------------------------------------------------------------ match report (POST /api/commentary)
    async def _commentary(self, request):
        match = self.last_match() if self.last_match else None
        if match is None:
            return web.json_response({"error": "No finished match yet."}, status=404)
        stats_text = match_report(match)
        text, source = stats_text, "stats"
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            try:
                text, source = await ai_report(match, stats_text, key), "ai"
            except Exception as e:     # no internet, bad key, ... -> the statistics report still works
                print(f"[report] AI report failed ({e}) - using the statistics report")
        return web.json_response({"commentary": text, "source": source, "match_id": match.get("id")})

    def start(self):
        ready = threading.Event()

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            app = web.Application()
            app.router.add_get("/", self._index)
            app.router.add_get("/ws/spectator", self._ws)
            app.router.add_post("/api/commentary", self._commentary)
            runner = web.AppRunner(app)
            self.loop.run_until_complete(runner.setup())
            self.loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", self.port).start())
            ready.set()
            self.loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)
        print(f"Live dashboard: http://localhost:{self.port}/  "
              f"(phones on the same WiFi: http://<this laptop's IP>:{self.port}/)")


# ---------------------------------------------------------------------------
# Match report from the statistics in matches.json (no AI needed)
# ---------------------------------------------------------------------------
def match_report(m, rng=random):
    names = m.get("players", {})
    a, b = names.get("A", "Player A"), names.get("B", "Player B")
    score = m.get("score", {"A": 0, "B": 0})
    w = m.get("winner") if m.get("winner") in ("A", "B") else ("A" if score["A"] >= score["B"] else "B")
    l = "B" if w == "A" else "A"
    winner, loser = names.get(w, w), names.get(l, l)
    ws, ls = score.get(w, 0), score.get(l, 0)
    rounds = m.get("rounds", [])

    if ls == 0 and ws > 1:
        opener = rng.choice([f"A clean sweep! {winner} takes it {ws}-0 without dropping a round.",
                             f"Total domination: {winner} beats {loser} {ws}-0."])
    elif ws - ls == 1 and ls > 0:
        opener = rng.choice([f"Down to the wire! {winner} edges out {loser} {ws}-{ls}.",
                             f"What a fight - {winner} beats {loser} {ws}-{ls} in a match that could have gone either way."])
    else:
        opener = f"{winner} beats {loser} {ws}-{ls}."

    lines = [opener, "", "Round by round:"]
    flawless = {"A": 0, "B": 0}
    total_hits = {"A": 0, "B": 0}
    fastest = None
    streak, best_streak, last_w = 0, (0, None), None
    for r in rounds:
        rw = r.get("winner")
        hits = r.get("hits", {})
        for p in ("A", "B"):
            total_hits[p] += hits.get(p, 0)
        t = r.get("duration_s", 0)
        if rw in ("A", "B"):
            other = "B" if rw == "A" else "A"
            perfect = hits.get(other, 0) == 0
            if perfect:
                flawless[rw] += 1
            note = " - flawless, not a scratch" if perfect else ""
            lines.append(f"  R{r.get('round')}: {names.get(rw, rw)} in {t:.1f} s{note}")
            if fastest is None or t < fastest[0]:
                fastest = (t, rw, r.get("round"))
            streak = streak + 1 if rw == last_w else 1
            last_w = rw
            if streak > best_streak[0]:
                best_streak = (streak, rw)
        else:
            lines.append(f"  R{r.get('round')}: draw - both landed the final hit together ({t:.1f} s)")

    # comeback: the winner lost the first round(s)
    if rounds and rounds[0].get("winner") == l and ws > 1:
        lines += ["", f"Comeback! {loser} drew first blood, but {winner} answered back."]

    lines += ["", "Key stats:"]
    if fastest:
        lines.append(f"  Fastest round: R{fastest[2]}, {fastest[0]:.1f} s by {names.get(fastest[1], fastest[1])}")
    lines.append(f"  Hits landed: {a} {total_hits['A']}, {b} {total_hits['B']}")
    for p in ("A", "B"):
        if flawless[p]:
            lines.append(f"  Flawless rounds: {names.get(p, p)} x{flawless[p]}")
    if best_streak[0] >= 2:
        lines.append(f"  Best streak: {best_streak[0]} rounds in a row by {names.get(best_streak[1], best_streak[1])}")
    total = sum(r.get("duration_s", 0) for r in rounds)
    lines.append(f"  Match time: {int(total) // 60}:{int(total) % 60:02d} of fighting over {len(rounds)} round(s)")
    lines += ["", rng.choice([f"GG {loser} - rematch?", f"{winner} is the one to beat now.",
                             f"{loser} will want revenge after that one."])]
    return "\n".join(lines)


async def ai_report(m, stats_text, key):
    """Optional: an AI model writes the commentary from the same statistics (needs ANTHROPIC_API_KEY)."""
    import aiohttp
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
    prompt = ("You are an energetic esports caster. Write a short, fun post-match report (under 120 words, "
              "plain text, no markdown) for this 1v1 duel on TinyBoy watches. Use only these facts:\n\n"
              + stats_text + "\n\nRaw match data: " + json.dumps(m))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post("https://api.anthropic.com/v1/messages",
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": model, "max_tokens": 400,
                                "messages": [{"role": "user", "content": prompt}]}) as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}: {data.get('error', {}).get('message', data)}")
    text = "".join(part.get("text", "") for part in data.get("content", []))
    if not text.strip():
        raise RuntimeError("empty answer")
    return text.strip()
