"""
bridge.py - TinyBoy laptop bridge: watch match events  ->  matches.json

Reads the "EVT,..." lines a watch running duel_v3 prints over USB (format: docs/interfaces.md,
section 3.1b), builds each match, and saves everything to matches.json for the Telegram bot,
heatmaps, etc. This is the ONLY program that writes matches.json - everything else just reads it.

Setup (once):
    pip install pyserial

Live, from a watch (close the Arduino Serial Monitor first - only one program can open the port):
    python bridge.py --port COM5
    python bridge.py --port COM5 --name-a Alice --name-b Bob

Replay a saved log (no watch needed - for testing the bot):
    python bridge.py --replay sample/events.log --data my_test   (writes my_test/matches.json)

Every raw line is also appended to events_<date>.log, so any session can be replayed later.

Output files (in the --data folder, default: this folder):
    matches.json                    players' totals + one entry per match (small, for the bot)
    positions/match_<id>.json       movement trail of each match (for heatmaps)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Safe JSON saving: write a temp file, then swap it in, so a crash mid-save
# never leaves a half-written file. Retries because on Windows the swap fails
# for a moment if another program (the bot) is reading the file right then.
# ---------------------------------------------------------------------------
def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    print(f"[warn] could not save {path} (file busy)")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Match tracker: turns events into matches
# ---------------------------------------------------------------------------
class MatchTracker:
    def __init__(self, data_dir, out_name, names):
        self.data_dir = data_dir
        self.out_path = os.path.join(data_dir, out_name)
        self.pos_dir = os.path.join(data_dir, "positions")
        os.makedirs(self.pos_dir, exist_ok=True)

        self.db = load_json(self.out_path, {"players": {}, "matches": []})
        for p in ("A", "B"):
            self.db["players"].setdefault(p, {
                "name": f"Player {p}", "matches": 0, "wins": 0, "losses": 0,
                "rounds_won": 0, "shots": 0, "hits": 0,
            })
            if names.get(p):
                self.db["players"][p]["name"] = names[p]

        self.match = None        # match in progress
        self.positions = None    # {"A": [[x, y, t], ...], "B": [...]}
        self.match_t0 = 0.0
        self.reporter = None     # which watch is plugged in

    # --- helpers ---
    def next_id(self):
        return max((m["id"] for m in self.db["matches"]), default=0) + 1

    def save(self):
        save_json(self.out_path, self.db)

    def save_positions(self):
        if self.match is not None:
            save_json(os.path.join(self.pos_dir, f"match_{self.match['id']}.json"), self.positions)

    def name(self, p):
        return self.db["players"][p]["name"]

    # --- events ---
    def handle(self, f):
        """f = the fields after 'EVT', e.g. ['ROUND_END', '1', 'A', '2', '1', ...]"""
        kind = f[0]
        if kind == "HELLO":
            self.reporter = f[1]
            print(f"Watches connected (reporting watch: {f[1]})")

        elif kind == "MATCH_START":
            if self.match is not None:          # previous one never finished
                self.finish("aborted")
            self.reporter = f[1]
            self.match = {
                "id": self.next_id(), "started": now_iso(), "ended": None,
                "status": "in_progress", "winner": None,
                "players": {p: self.name(p) for p in ("A", "B")},
                "score": {"A": 0, "B": 0}, "rounds": [],
                "positions_file": None,
            }
            self.positions = {"A": [], "B": []}
            self.match_t0 = time.time()
            self.db["matches"].append(self.match)
            self.save()
            print(f"\n=== Match {self.match['id']} started: {self.name('A')} vs {self.name('B')} ===")

        elif kind == "ROUND_START":
            if self.match:  # the watch counts rounds across matches - number them per match here
                print(f"  Round {len(self.match['rounds']) + 1} ...")

        elif kind == "POS":
            if not self.match:
                return
            t = round(time.time() - self.match_t0, 2)
            for i in range(1, len(f), 3):  # groups of  P,x,y
                p, x, y = f[i], f[i + 1], f[i + 2]
                self.positions[p].append([int(x) / 100, int(y) / 100, t])

        elif kind == "HIT":
            if self.match:
                print(f"    {self.name(f[1])} hit {self.name(f[2])} ({f[3]} hp left)")

        elif kind == "ROUND_END":
            if not self.match:
                return
            rnd, winner = len(self.match["rounds"]) + 1, f[2]
            hits = {"A": int(f[3]), "B": int(f[4])}
            shots = {"A": int(f[5]), "B": int(f[6])}
            self.match["rounds"].append({
                "round": rnd, "winner": winner if winner in ("A", "B") else "draw",
                "hits": hits, "shots": shots, "duration_s": round(int(f[7]) / 1000, 1),
            })
            if winner in ("A", "B"):
                self.match["score"][winner] += 1
            for p in ("A", "B"):
                self.db["players"][p]["shots"] += shots[p]
                self.db["players"][p]["hits"] += hits[p]
                if winner == p:
                    self.db["players"][p]["rounds_won"] += 1
            self.save()
            self.save_positions()
            who = self.name(winner) if winner in ("A", "B") else "nobody (draw)"
            print(f"  Round {rnd} won by {who} | score {self.match['score']['A']}-{self.match['score']['B']}")

        elif kind == "MATCH_END":
            if self.match:
                self.match["winner"] = f[1]
                self.match["score"] = {"A": int(f[2]), "B": int(f[3])}
                self.finish("finished")

        elif kind == "MATCH_ABORT":
            if self.match:
                self.finish("aborted")

        elif kind == "SHOT":
            pass  # per-round shot totals come in ROUND_END (covers both players)

    def finish(self, status):
        m = self.match
        m["status"] = status
        m["ended"] = now_iso()
        m["positions_file"] = f"positions/match_{m['id']}.json"
        if status == "finished" and m["winner"] in ("A", "B"):
            w = m["winner"]
            l = "B" if w == "A" else "A"
            self.db["players"][w]["wins"] += 1
            self.db["players"][l]["losses"] += 1
            for p in ("A", "B"):
                self.db["players"][p]["matches"] += 1
        self.save_positions()
        self.save()
        if status == "finished":
            print(f"=== Match {m['id']} WON by {self.name(m['winner'])} "
                  f"({m['score']['A']}-{m['score']['B']}) - saved to {os.path.basename(self.out_path)} ===\n")
        else:
            print(f"=== Match {m['id']} aborted (connection lost) - partial match saved ===\n")
        self.match = None
        self.positions = None


# ---------------------------------------------------------------------------
# Line handling: only "EVT," lines, and skip anything damaged in transfer
# ---------------------------------------------------------------------------
EXPECTED_FIELDS = {  # minimum number of fields (after 'EVT') per event type
    "HELLO": 2, "MATCH_START": 2, "ROUND_START": 2, "SHOT": 3, "HIT": 4,
    "POS": 4, "ROUND_END": 8, "MATCH_END": 4, "MATCH_ABORT": 1,
}
NUMERIC_FIELDS = {  # positions of fields that must be whole numbers
    "ROUND_START": [1], "SHOT": [2], "HIT": [3],
    "ROUND_END": [1, 3, 4, 5, 6, 7], "MATCH_END": [2, 3],
}


def parse_line(line):
    """Return the event fields, or None if this isn't a valid event line."""
    line = line.strip()
    if not line.startswith("EVT,"):
        return None
    f = line.split(",")[1:]
    kind = f[0] if f else ""
    if kind not in EXPECTED_FIELDS or len(f) < EXPECTED_FIELDS[kind]:
        return None
    try:
        for i in NUMERIC_FIELDS.get(kind, []):
            int(f[i])
        if kind == "POS":  # groups of  P,x,y  - every x and y must be a number
            if (len(f) - 1) % 3 != 0:
                return None
            for i in range(1, len(f), 3):
                if f[i] not in ("A", "B"):
                    return None
                int(f[i + 1]); int(f[i + 2])
    except (ValueError, IndexError):
        return None
    return f


def run(lines, tracker, raw_log):
    skipped = 0
    for raw in lines:
        if raw_log:
            raw_log.write(raw if raw.endswith("\n") else raw + "\n")
            raw_log.flush()
        if not raw.strip().startswith("EVT,"):
            continue  # FPS/debug lines
        f = parse_line(raw)
        if f is None:
            skipped += 1
            print(f"[skip] damaged line: {raw.strip()!r}")
            continue
        tracker.handle(f)
    return skipped


def serial_lines(port):
    import serial  # pip install pyserial
    while True:
        try:
            with serial.Serial(port, 9600, timeout=1) as ser:
                print(f"Listening on {port} ... (Ctrl+C to stop)")
                while True:
                    b = ser.readline()
                    if b:
                        yield b.decode(errors="replace")
        except serial.SerialException as e:
            # the watch restarted (watchdog / new upload) or was unplugged - keep trying
            print(f"[port] {e} - retrying in 2 s (close the Arduino Serial Monitor if it's open)")
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description="TinyBoy watch -> matches.json bridge")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="watch serial port, e.g. COM5")
    src.add_argument("--replay", help="replay a saved log file instead of a live watch")
    ap.add_argument("--data", default=HERE, help="folder for matches.json and positions/")
    ap.add_argument("--out", default="matches.json", help="output file name")
    ap.add_argument("--name-a", help="display name for Player A")
    ap.add_argument("--name-b", help="display name for Player B")
    args = ap.parse_args()

    tracker = MatchTracker(args.data, args.out, {"A": args.name_a, "B": args.name_b})

    if args.replay:
        with open(args.replay, encoding="utf-8") as f:
            skipped = run(f, tracker, None)
        if tracker.match is not None:
            tracker.finish("aborted")
        print(f"Replay done. Skipped {skipped} damaged line(s).")
        return

    log_path = os.path.join(args.data, f"events_{datetime.now():%Y%m%d}.log")
    with open(log_path, "a", encoding="utf-8") as raw_log:
        try:
            run(serial_lines(args.port), tracker, raw_log)
        except KeyboardInterrupt:
            if tracker.match is not None:
                tracker.finish("aborted")
            print("\nStopped.")


if __name__ == "__main__":
    main()