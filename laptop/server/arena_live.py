"""arena_live.py - run the ARENA server (Naufal's rules + bots) live on the web dashboard.

A Free-for-All match between server bots, decided by the server-authoritative rules in rules.py,
streamed to web/index.html (laptop + phones) through the same web server as the watch duel
(live_feed.py). The referee panel works fully here: Pause / Resume, Restart, Game Mode, Match Duration.

    cd laptop
    python -m server.arena_live                                  # 4 bots, 2 minutes, first to 10 kills
    python -m server.arena_live --bots 6 --minutes 3 --kills 15 --host-token tb2611
    then open http://localhost:8000/  (phones: http://<laptop IP>:8000/)

This is a simulation: no watch takes part. Real watches would join through arena.py / protocol.py
(each watch one player), the way server.watch_link already joins the laptop to a real watch.
"""
import argparse
import random
import time

from live_feed import LiveFeed, MAP

from .bots import BotBrain
from .rules import LOBBY, OVER, FreeForAll, Game

TICK_HZ = 20
STATE_HZ = 10                 # dashboard updates per second
NEXT_MATCH_S = 12.0           # pause on the final scoreboard before the next match starts
COLOURS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#a855f7", "#14b8a6", "#ec4899", "#eab308"]
NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]


class ArenaFeed(LiveFeed):
    """LiveFeed's web server + referee, fed from a rules.Game instead of watch EVT lines."""

    def __init__(self, n_bots=4, minutes=2.0, kills=10, port=8000, host_token=None, seed=None):
        super().__init__({}, port=port, host_token=host_token)
        self.n_bots, self.kills = n_bots, kills
        self.rng = random.Random(seed)
        self.clock = 0.0
        self.match_no = 0
        self.shown_feed = 0
        self.over_since = None
        self.last_result = None
        self.new_game(minutes * 60)

    # ------------------------------------------------------------ game
    def new_game(self, seconds):
        self.game = Game(mode=FreeForAll(self.kills), rng=self.rng, match_seconds=seconds)
        self.brains = []
        for i in range(self.n_bots):
            p = self.game.add_player(i + 1, NAMES[i % len(NAMES)], is_bot=True)
            self.brains.append(BotBrain(self.game, p))
        self.shown_feed = 0
        self.over_since = None

    def step(self, dt):
        """One server tick (called 20x a second by run())."""
        with self.lock:
            g = self.game
            self.clock += dt
            was = g.state
            for b in self.brains:
                b.update(dt, self.clock)
            g.tick(self.clock)
            if was == LOBBY and g.state != LOBBY:
                self.match_no += 1
            if g.state == OVER:
                if self.over_since is None:
                    self.over_since = self.clock
                    self.last_result = self.result_summary()
                    print(f"=== Arena match {self.match_no} over: "
                          f"{g.winner.name + ' wins' if g.winner else 'draw'} ===")
                elif self.clock - self.over_since >= NEXT_MATCH_S:
                    g.restart()
                    self.match_no += 1
                    self.over_since = None
            events = self.new_events()
        for e in events:
            self.send(e)

    def new_events(self):
        out = []
        g = self.game
        while self.shown_feed < len(g.feed):
            text = g.feed[self.shown_feed]
            self.shown_feed += 1
            if " hit " in text:            # every single hit would flood the feed; kills are enough
                continue
            killer = victim = ""
            if " eliminated " in text:
                killer, victim = text.split(" eliminated ", 1)
            label = text[0].upper() + text[1:]
            print("  " + label)
            out.append(self._event(label, killer, victim))
        return out

    # ------------------------------------------------------------ dashboard
    def elapsed(self):
        g = self.game
        return max(0.0, g.match_seconds - g.time_left) if g.match_seconds else self.clock

    def snapshot(self):
        g = self.game
        status = {"lobby": "lobby", "playing": "paused" if g.paused else "playing",
                  "over": "finished"}[g.state]
        players = [{
            "id": str(p.id), "name": p.name, "color": COLOURS[(p.id - 1) % len(COLOURS)],
            "x": round(p.x, 2), "y": round(p.y, 2), "angle": round(p.angle, 2),
            "health": round(100 * max(0, p.hp) / Game.MAX_HP) if p.alive else 0,
            "kills": p.kills, "deaths": p.deaths, "alive": p.alive,
        } for p in g.players.values()]
        return {
            "type": "state",
            "match": {"id": f"arena-{self.match_no}", "mode": "ffa", "status": status, "round": 1,
                      "duration_seconds": int(g.match_seconds),
                      "remaining_seconds": round(g.time_left, 1)},
            "map": MAP,
            "players": players,
        }

    # ------------------------------------------------------------ referee (all buttons work here)
    def referee(self, raw):
        import json
        try:
            msg = json.loads(raw)
        except ValueError:
            return False, "Bad message."
        if not isinstance(msg, dict) or msg.get("type") != "referee_command":
            return False, "Unknown message."
        if not self.host_token:
            return False, "Referee is off. Start with --host-token <password> to turn it on."
        if str(msg.get("host_token", "")) != self.host_token:
            return False, "Wrong host token."
        action = msg.get("action")
        with self.lock:
            g = self.game
            if action == "pause":
                if g.state != "playing" or g.paused:
                    return False, "Nothing to pause."
                g.paused = True
                g.feed.append("match paused by the referee")
                return True, "Arena paused."
            if action == "resume":
                if not g.paused:
                    return False, "The match isn't paused."
                g.paused = False
                g.feed.append("match resumed")
                return True, "Arena resumed."
            if action == "restart":
                g.restart()
                self.match_no += 1
                self.over_since = None
                return True, "New arena match started."
            if action == "settings":
                s = msg.get("settings") or {}
                if s.get("mode", "ffa") != "ffa":
                    return False, "Only Free For All is implemented in the server so far."
                secs = s.get("duration_seconds")
                if not isinstance(secs, (int, float)) or not 30 <= secs <= 600:
                    return False, "Duration must be 30-600 seconds."
                g.match_seconds = float(secs)
                g.restart()
                self.match_no += 1
                self.over_since = None
                return True, f"Settings applied: Free For All, {int(secs) // 60} min. New match started."
        return False, f"Unknown referee action: {action}"

    # ------------------------------------------------------------ match report (POST /api/commentary)
    def result_summary(self):
        g = self.game
        ranked = sorted(g.players.values(), key=lambda p: (p.kills, -p.deaths, p.accuracy), reverse=True)
        return {"winner": g.winner.name if g.winner else None, "time": g.match_seconds - g.time_left,
                "rows": [(p.name, p.kills, p.deaths, p.shots, p.hits) for p in ranked]}

    async def _commentary(self, request):
        from aiohttp import web
        r = self.last_result
        if r is None:
            return web.json_response({"error": "No finished arena match yet."}, status=404)
        rows = r["rows"]
        mins, secs = divmod(int(r["time"]), 60)
        head = (f"{r['winner']} wins the arena with {rows[0][1]} eliminations!" if r["winner"]
                else "The arena ends in a draw - nobody could be separated.")
        lines = [head, "", "Final standings:"]
        for i, (name, k, d, shots, hits) in enumerate(rows, 1):
            acc = f"{100 * hits // shots}%" if shots else "-"
            lines.append(f"  {i}. {name} - {k} kills, {d} deaths, accuracy {acc}")
        best_kd = max(rows, key=lambda row: row[1] / max(1, row[2]))
        lines += ["", f"Best K/D: {best_kd[0]} ({best_kd[1]}/{best_kd[2]})",
                  f"Match time: {mins}:{secs:02d}",
                  "", "Every hit was checked by the server: range, walls, aim and fire rate."]
        return web.json_response({"commentary": "\n".join(lines), "source": "stats",
                                  "match_id": f"arena-{self.match_no}"})

    # ------------------------------------------------------------ main loop
    def run(self):
        self.start()
        print(f"Arena: {self.n_bots} bots, Free For All, first to {self.kills} kills or "
              f"{int(self.game.match_seconds)} s. Ctrl+C to stop.")
        dt = 1.0 / TICK_HZ
        next_tick = time.perf_counter()
        last_state = 0.0
        try:
            while True:
                self.step(dt)
                now = time.perf_counter()
                if now - last_state >= 1.0 / STATE_HZ:
                    last_state = now
                    with self.lock:
                        snap = self.snapshot()
                    self.send(snap)
                next_tick += dt
                time.sleep(max(0.0, next_tick - time.perf_counter()))
        except KeyboardInterrupt:
            print("\nStopped.")


def main():
    ap = argparse.ArgumentParser(description="ARENA server bots, live on the web dashboard")
    ap.add_argument("--bots", type=int, default=4, help="number of bot players (2-8)")
    ap.add_argument("--minutes", type=float, default=2.0, help="match length")
    ap.add_argument("--kills", type=int, default=10, help="kills needed to win")
    ap.add_argument("--port", type=int, default=8000, help="dashboard port (default 8000)")
    ap.add_argument("--host-token", help="password for the referee buttons")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    ArenaFeed(max(2, min(8, a.bots)), a.minutes, a.kills, a.port, a.host_token, a.seed).run()


if __name__ == "__main__":
    main()
