"""Tests for step 2 (the server bot plays Player B: duel_bot.py) and the EVT match-event output of
watch_link.py, including a check that bridge.py accepts every line (bridge.py is imported, not changed).
Run from the laptop folder:  python -m unittest -v"""
import asyncio
import contextlib
import io
import json
import math
import os
import random
import tempfile
import time
import unittest

from server import watch_link as wl
from server.duel_bot import DIFFICULTY, DuelBot
from server.world import GameMap
from tests.test_watch_link import FakeClient, FirmwareA, play

GRID = GameMap()

# A wall-free walk for the watch player: there and back along open corridors (checked in a test)
ROUTE = [(2.5, 2.5), (5.5, 2.5), (5.5, 8.5), (10.5, 8.5), (10.5, 13.5), (13.5, 13.5)]
ROUTE = ROUTE + ROUTE[-2:0:-1]


def walker(t, speed=2.0):
    legs = [(ROUTE[i], ROUTE[(i + 1) % len(ROUTE)]) for i in range(len(ROUTE))]
    total = sum(math.dist(a, b) for a, b in legs)
    d = (t * speed) % total
    for (x0, y0), (x1, y1) in legs:
        length = math.dist((x0, y0), (x1, y1))
        if d <= length:
            return x0 + (x1 - x0) * d / length, y0 + (y1 - y0) * d / length
        d -= length
    return ROUTE[0]


class HumanA(FirmwareA):
    """FirmwareA that moves and shoots like a person: walks ROUTE, fires every 0.4 s, and hits B
    with probability `aim` when B is within 8 squares with a clear view (shooter decides)."""

    def __init__(self, rounds_to_win=3, seed=0, aim=0.5):
        super().__init__(rounds_to_win)
        self.rng = random.Random(seed)
        self.aim = aim
        self.x, self.y = 2.5, 2.5
        self.t0 = 0.0
        self.last_shot = -99.0
        self.my_shots = 0

    def start_round(self, now):
        super().start_round(now)
        self.x, self.y, self.t0, self.my_shots = 2.5, 2.5, now, 0

    def packet(self):
        p = bytearray(super().packet())
        p[3:5] = int(self.x * 256).to_bytes(2, "little", signed=True)
        p[5:7] = int(self.y * 256).to_bytes(2, "little", signed=True)
        p[10] = self.my_shots & 0xFF
        return bytes(p)

    def step(self, now):
        if self.state == "playing" and not (self.opp_paused or self.paused_by_self):
            self.x, self.y = walker(now - self.t0)
            if now - self.last_shot >= 0.4:
                self.last_shot = now
                self.my_shots += 1
                if self.opp_pos and math.dist((self.x, self.y), self.opp_pos) <= 8 \
                        and GRID.line_of_sight(self.x, self.y, *self.opp_pos) and self.rng.random() < self.aim:
                    self.land_hit()
        return super().step(now)


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(line)

    def kinds(self):
        return [line.split(",")[1] for line in self.lines]


def bot_match(difficulty="normal", seed=1, aim=0.5, rounds=3, seconds=300, loss=0.0):
    rec = Recorder()
    duel = wl.DuelB(DuelBot(difficulty, random.Random(seed)), wl.MatchEvents(rec))
    a = HumanA(rounds, seed=seed, aim=aim)
    play(duel, a, seconds, loss=loss, seed=seed)
    return duel, a, rec


# ---------------------------------------------------------------------------
# Step 2: the bot
# ---------------------------------------------------------------------------
class BotTests(unittest.TestCase):
    def test_test_route_is_wall_free(self):
        for i in range(2000):
            x, y = walker(i * 0.05)
            self.assertFalse(GRID.is_wall(x, y), (x, y))

    def test_bot_plays_a_whole_match_and_both_sides_agree(self):
        for seed in range(4):
            duel, a, _ = bot_match(seed=seed)
            self.assertEqual(duel.state, "match_over", seed)
            self.assertEqual(a.state, "match_over", seed)
            self.assertEqual((duel.rounds_a, duel.rounds_b), (a.rounds_me, a.rounds_opp), seed)

    def test_bot_can_win_rounds(self):
        wins = sum(bot_match(seed=s, aim=0.2)[0].rounds_b for s in range(4))
        self.assertGreater(wins, 0)

    def test_bot_never_walks_into_walls_and_only_shoots_with_line_of_sight(self):
        for seed in range(3):
            bot = DuelBot("hard", random.Random(seed))
            checks = {"ticks": 0, "shots": 0}
            original_update, original_fire = bot.update, wl.DuelB.fire

            def update(duel, dt, now, _bot=bot):
                original_update(duel, dt, now)
                self.assertFalse(GRID.is_wall(duel.x, duel.y), (duel.x, duel.y))
                checks["ticks"] += 1
            bot.update = update

            def fire(duel, now, hit, _bot=bot):
                w = _bot.watch
                self.assertTrue(GRID.line_of_sight(duel.x, duel.y, w.x, w.y))
                checks["shots"] += 1
                original_fire(duel, now, hit)
            duel = wl.DuelB(bot)
            duel.fire = lambda now, hit, _d=duel: fire(_d, now, hit)
            play(duel, HumanA(5, seed=seed, aim=0.3), 200, seed=seed)
            self.assertGreater(checks["ticks"], 1000)
            self.assertGreater(checks["shots"], 0)

    def test_each_round_starts_from_the_spawn_with_a_fresh_path(self):
        bot = DuelBot("normal", random.Random(0))
        duel = wl.DuelB(bot)
        duel.start(0.0)
        duel.on_packet(wl.DuelPacket(0, 1, 8.5, 8.5, 0.0, 0, 0, 0, 3), 0.0)
        t = 0.0
        for _ in range(100):
            t += 0.05
            duel.update(t, 0.05)
        self.assertNotEqual((duel.x, duel.y), (13.5, 13.5))         # it went looking
        duel.hits_landed = 2
        duel.update(t + 0.05, 0.05)                                  # round won -> round over
        duel.update(t + 3.0, 0.05)                                   # next round
        self.assertEqual(duel.round, 2)
        self.assertEqual((duel.x, duel.y), (13.5, 13.5))
        duel.update(t + 3.05, 0.05)                                  # first move of the new round
        path = bot.brain.path
        if path:                                                     # any path starts at the spawn square
            self.assertLessEqual(abs(path[0][0] - 13) + abs(path[0][1] - 13), 1, path)
        self.assertLess(math.dist((duel.x, duel.y), (13.5, 13.5)), 0.2)

    def test_bot_uses_the_watch_player_position_from_packets(self):
        bot = DuelBot("normal", random.Random(0))
        duel = wl.DuelB(bot)
        duel.start(0.0)
        duel.on_packet(wl.DuelPacket(0, 1, 13.5, 8.5, 1.0, 0, 0, 0, 3), 0.0)
        duel.update(0.05, 0.05)
        self.assertEqual((bot.watch.x, bot.watch.y), (13.5, 8.5))

    def test_difficulty_levels_are_ordered(self):
        def hits_per_minute(level):
            hits = 0
            for seed in range(6):
                bot = DuelBot(level, random.Random(seed))

                class Fake:                                       # just what DuelBot reads/writes
                    a, hits_taken, angle, x, y = None, 0, math.pi, 13.5, 13.5
                    landed = 0

                    def fire(self, now, hit):
                        self.landed += hit
                fake = Fake()
                bot.reset_round(fake, 0.0)
                t = 0.0
                while t < 60:
                    t += 0.05
                    x, y = walker(t)
                    fake.a = wl.DuelPacket(0, 1, x, y, 0.0, 0, 0, 0, 3)
                    bot.update(fake, 0.05, t)
                hits += fake.landed
            return hits / 6
        easy, normal, hard = (hits_per_minute(level) for level in ("easy", "normal", "hard"))
        self.assertLess(easy, normal)
        self.assertLess(normal, hard)

    def test_unknown_difficulty_is_refused(self):
        with self.assertRaises(ValueError):
            DuelBot("impossible")
        self.assertEqual(set(DIFFICULTY), {"easy", "normal", "hard"})

    def test_pause_freezes_the_bot(self):
        bot = DuelBot("hard", random.Random(0))
        duel = wl.DuelB(bot)
        duel.start(0.0)
        duel.on_packet(wl.DuelPacket(0, 1, 10.5, 13.5, 0.0, 0, wl.FLAG_PAUSED, 0, 3), 0.0)
        for i in range(100):
            duel.update(i * 0.05, 0.05)
        self.assertEqual((duel.x, duel.y, duel.shots), (13.5, 13.5, 0))

    def test_match_with_packet_loss(self):
        duel, a, _ = bot_match(seed=2, loss=0.3, seconds=400)
        self.assertEqual(duel.state, "match_over")
        self.assertEqual((duel.rounds_a, duel.rounds_b), (a.rounds_me, a.rounds_opp))


# ---------------------------------------------------------------------------
# Match events (docs/interfaces.md 3.1b)
# ---------------------------------------------------------------------------
class EventFormatTests(unittest.TestCase):
    def test_lines_match_the_documented_examples(self):
        rec = Recorder()
        ev = wl.MatchEvents(rec)
        ev.hello()
        ev.match_start()
        ev.round_start(1)
        ev.shot(True)
        ev.shot(False)
        ev.hit("A", "B", 1)
        ev.pos((2.5, 2.5), (13.5, 13.5))
        ev.pos(None, (13.5, 13.5))
        ev.round_end(1, "A", 2, 1, 7, 5, 34210)
        ev.match_end("A", 3, 1)
        ev.match_abort()
        self.assertEqual(rec.lines, [
            "EVT,HELLO,B", "EVT,MATCH_START,B", "EVT,ROUND_START,1", "EVT,SHOT,B,1", "EVT,SHOT,B,0",
            "EVT,HIT,A,B,1", "EVT,POS,A,250,250,B,1350,1350", "EVT,POS,B,1350,1350",
            "EVT,ROUND_END,1,A,2,1,7,5,34210", "EVT,MATCH_END,A,3,1", "EVT,MATCH_ABORT",
        ])

    def test_positions_are_cut_to_hundredths_like_the_firmware(self):
        rec = Recorder()
        wl.MatchEvents(rec).pos((2.499, 13.5099), (13.0, 7.25))
        self.assertEqual(rec.lines, ["EVT,POS,A,249,1350,B,1300,725"])

    def test_emitter_prints_and_appends_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "match.log")
            shown = []
            emit, close = wl.make_emitter(path, echo=lambda line, flush: shown.append(line))
            emit("EVT,HELLO,B")
            emit("EVT,MATCH_START,B")
            close()
            emit2, close2 = wl.make_emitter(path, echo=lambda line, flush: None)   # appends, keeps old lines
            emit2("EVT,MATCH_ABORT")
            close2()
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read().splitlines(), ["EVT,HELLO,B", "EVT,MATCH_START,B", "EVT,MATCH_ABORT"])
            self.assertEqual(shown, ["EVT,HELLO,B", "EVT,MATCH_START,B"])


class EventStreamTests(unittest.TestCase):
    def test_event_order_and_counts_match_the_round_results(self):
        for seed in range(3):
            duel, a, rec = bot_match(seed=seed)
            kinds = rec.kinds()
            self.assertEqual(kinds[:3], ["HELLO", "MATCH_START", "ROUND_START"], seed)
            self.assertEqual(kinds[-1], "MATCH_END", seed)
            self.assertEqual(kinds.count("MATCH_START"), 1)
            self.assertEqual(kinds.count("ROUND_START"), kinds.count("ROUND_END"))
            # per round: SHOT/HIT lines add up to the totals in ROUND_END
            rnd, shots_b, hits_b, hits_a = None, 0, 0, 0
            for line in rec.lines:
                f = line.split(",")[1:]
                if f[0] == "ROUND_START":
                    rnd, shots_b, hits_b, hits_a = int(f[1]), 0, 0, 0
                elif f[0] == "SHOT":
                    shots_b += 1
                elif f[0] == "HIT" and f[1] == "B":
                    hits_b += 1
                elif f[0] == "HIT" and f[1] == "A":
                    hits_a += 1
                elif f[0] == "ROUND_END":
                    self.assertEqual(int(f[1]), rnd)
                    self.assertEqual((int(f[3]), int(f[4]), int(f[6])), (hits_a, hits_b, shots_b), line)
                    self.assertGreater(int(f[5]), 0)                         # the watch's shots, from its packets
                    self.assertGreater(int(f[7]), 0)
            end = rec.lines[-1].split(",")
            self.assertEqual((int(end[3]), int(end[4])), (duel.rounds_a, duel.rounds_b))

    def test_about_five_positions_per_second_while_playing(self):
        rec = Recorder()
        duel = wl.DuelB(wl.StandStill(), wl.MatchEvents(rec))
        play(duel, FirmwareA(), 10.0)
        self.assertTrue(45 <= rec.kinds().count("POS") <= 52, rec.kinds().count("POS"))
        self.assertTrue(all(line.startswith("EVT,POS,A,250,250,B,1350,1350") for line in rec.lines
                            if ",POS," in line and line != rec.lines[3]))

    def test_draw_and_catch_up_lines(self):
        rec = Recorder()
        duel = wl.DuelB(events=wl.MatchEvents(rec))
        duel.start(0.0)
        duel.hits_landed = 2
        duel.on_packet(wl.DuelPacket(0, 1, 2.5, 2.5, 0.0, 2, 0, 3, 3), 0.1)
        duel.update(0.5, 0.01)
        self.assertTrue(rec.lines[-1].startswith("EVT,ROUND_END,1,D,2,2,3,0,"), rec.lines[-1])
        duel.on_packet(wl.DuelPacket(1, 4, 2.5, 2.5, 0.0, 0, 0, 0, 3), 3.2)
        duel.update(3.2, 0.01)                                # round-over pause ends -> round 2 ...
        duel.update(3.25, 0.01)                               # ... and the watch is already on 4
        self.assertIn("EVT,ROUND_START,4", rec.lines)

    def test_a_third_hit_after_the_cap_is_reported_as_a_miss(self):
        rec = Recorder()                                      # same as the firmware: hit = onTarget && hits < 2
        duel = wl.DuelB(events=wl.MatchEvents(rec))
        duel.start(0.0)
        for t in (0.5, 1.0, 1.5):
            duel.fire(t, hit=True)
        shots = [line for line in rec.lines if ",SHOT," in line or ",HIT," in line]
        self.assertEqual(shots, ["EVT,SHOT,B,1", "EVT,HIT,B,A,1", "EVT,SHOT,B,1", "EVT,HIT,B,A,0", "EVT,SHOT,B,0"])
        self.assertEqual((duel.shots, duel.hits_landed), (3, 2))

    def test_abort_only_mid_match(self):
        rec = Recorder()
        duel = wl.DuelB(events=wl.MatchEvents(rec))
        duel.start(0.0)
        duel.abort()
        self.assertEqual(rec.lines[-1], "EVT,MATCH_ABORT")
        duel.abort()
        self.assertEqual(rec.kinds().count("MATCH_ABORT"), 1)
        rec2 = Recorder()
        duel2, _, rec2 = bot_match(seed=0)
        duel2.abort()                                         # after MATCH_END: nothing more
        self.assertEqual(rec2.kinds()[-1], "MATCH_END")

    def test_link_dropping_mid_match_prints_match_abort(self):
        rec = Recorder()
        client = FakeClient(FirmwareA())

        async def go():
            asyncio.get_running_loop().call_later(0.4, setattr, client, "is_connected", False)
            client.watch.connect(time.monotonic())
            return await wl.run_session(client, wl.DuelB(events=wl.MatchEvents(rec)), out=lambda s: None)
        asyncio.run(go())
        self.assertEqual(rec.kinds()[:3], ["HELLO", "MATCH_START", "ROUND_START"])
        self.assertEqual(rec.lines[-1], "EVT,MATCH_ABORT")


class BridgeCompatibilityTests(unittest.TestCase):
    """Feed the lines through bridge.py's own parser and match tracker (bridge.py is not modified)."""

    def _bridge(self, lines):
        import bridge
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            tracker = bridge.MatchTracker(d, "matches.json", {"A": "Watch", "B": "Laptop bot"})
            skipped = bridge.run([line + "\n" for line in lines], tracker, None)
            with open(os.path.join(d, "matches.json"), encoding="utf-8") as f:
                db = json.load(f)
            positions = None
            if db["matches"] and db["matches"][-1]["positions_file"]:
                with open(os.path.join(d, db["matches"][-1]["positions_file"]), encoding="utf-8") as f:
                    positions = json.load(f)
        return skipped, db, positions

    def test_every_line_is_accepted_by_bridge(self):
        import bridge
        for seed in range(3):
            _, _, rec = bot_match(seed=seed)
            for line in rec.lines:
                self.assertIsNotNone(bridge.parse_line(line), line)

    def test_bridge_records_the_same_match(self):
        duel, _, rec = bot_match(seed=1)
        skipped, db, positions = self._bridge(rec.lines)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(db["matches"]), 1)
        m = db["matches"][0]
        self.assertEqual(m["status"], "finished")
        self.assertEqual(m["winner"], "B" if duel.rounds_b > duel.rounds_a else "A")
        self.assertEqual(m["score"], {"A": duel.rounds_a, "B": duel.rounds_b})
        self.assertEqual(len(m["rounds"]), duel.rounds_a + duel.rounds_b + sum("draw" in l for l in duel.log))
        ends = [line.split(",") for line in rec.lines if line.startswith("EVT,ROUND_END")]
        for r, f in zip(m["rounds"], ends):
            self.assertEqual(r["hits"], {"A": int(f[4]), "B": int(f[5])})
            self.assertEqual(r["shots"], {"A": int(f[6]), "B": int(f[7])})
        self.assertGreater(len(positions["A"]), 10)
        self.assertGreater(len(positions["B"]), 10)
        self.assertEqual(db["players"]["B"]["name"], "Laptop bot")

    def test_bridge_saves_an_aborted_match(self):
        rec = Recorder()
        duel = wl.DuelB(events=wl.MatchEvents(rec))
        duel.start(0.0)
        duel.update(0.3, 0.05)
        duel.abort()
        skipped, db, _ = self._bridge(rec.lines)
        self.assertEqual(skipped, 0)
        self.assertEqual(db["matches"][0]["status"], "aborted")


if __name__ == "__main__":
    unittest.main()
