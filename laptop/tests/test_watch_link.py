"""Tests for watch_link.py (laptop = Player B in a duel against one watch).
Run from the laptop folder:  python -m unittest -v

FirmwareA below is a Python copy of what Player A's firmware (firmware/tinyboy/tinyboy.ino) does in a
multiplayer duel, so the whole link can be tested without a watch."""
import asyncio
import math
import random
import struct
import time
import unittest

from server import watch_link as wl

FMT = "<BBBhhBBBBB"


class FirmwareA:
    """Player A as the firmware behaves: only answers B's packets, counts rounds itself, takes B's
    hitsLanded as damage only for the same round, 2.5 s round-over screen, catches up to B's round."""

    def __init__(self, rounds_to_win=3):
        self.rounds_to_win = rounds_to_win
        self.state = "waiting"
        self.my_round = 0
        self.rounds_me = self.rounds_opp = 0
        self.my_hits = self.hits_taken = self.opp_hits = 0
        self.opp_round = 0
        self.opp_pos = None
        self.opp_paused = False
        self.paused_by_self = False
        self.reply_due = False
        self.seq = 0
        self.received = 0
        self.state_since = 0.0
        self.results = []

    def connect(self, now):                       # "if (netConnected)" in the waiting state
        self.my_round = 0
        self.rounds_me = self.rounds_opp = 0
        self.reply_due = False
        self.start_round(now)

    def start_round(self, now):
        self.my_round += 1
        self.my_hits = self.hits_taken = self.opp_hits = 0
        self.state, self.state_since = "playing", now

    def end_round(self, result, now):
        if result > 0:
            self.rounds_me += 1
        if result < 0:
            self.rounds_opp += 1
        self.results.append(result)
        over = self.rounds_me >= self.rounds_to_win or self.rounds_opp >= self.rounds_to_win
        self.state, self.state_since = ("match_over" if over else "round_over"), now

    def receive(self, data, now):                 # onNetReceive
        if len(data) != 12 or data[0] != 0xB0:
            return
        _, _, rnd, x, y, _, hits, flags, _, _ = struct.unpack(FMT, data)
        self.opp_pos = (x / 256.0, y / 256.0)
        self.opp_round = rnd
        if rnd == self.my_round:
            self.opp_hits = hits
        self.opp_paused = bool(flags & 0x02)
        self.reply_due = True
        self.received += 1

    def land_hit(self):                            # A shoots B and decides it hit
        if self.state == "playing" and self.my_hits < 2:
            self.my_hits += 1

    def packet(self):
        flags = 0x02 if self.paused_by_self else 0
        p = struct.pack(FMT, 0xB0, self.seq, self.my_round, int(2.5 * 256), int(2.5 * 256), 127,
                        self.my_hits, flags, 0, self.rounds_to_win)
        self.seq = (self.seq + 1) & 0xFF
        return p

    def step(self, now):
        """One pass of loop(). Returns the packet A sends this pass, or None."""
        out = None
        if self.reply_due:                         # A only answers
            self.reply_due = False
            out = self.packet()
        if self.state == "match_over":
            return out
        if self.state == "round_over":
            if now - self.state_since >= 2.5:
                self.start_round(now)
            return out
        if self.state != "playing":
            return out
        if self.opp_round > self.my_round:         # catch up
            self.my_round = self.opp_round - 1
            self.start_round(now)
        if self.opp_paused or self.paused_by_self:
            return out
        self.hits_taken = self.opp_hits
        won, lost = self.my_hits >= 2, self.hits_taken >= 2
        if won or lost:
            self.end_round(0 if (won and lost) else (1 if won else -1), now)
        return out


class AlwaysHit:
    """Test brain: fires every 0.4 s and every shot hits."""

    def reset_round(self, duel, now):
        pass

    def update(self, duel, dt, now):
        if now - duel.last_fire >= 0.4:
            duel.fire(now, hit=True)


class CountingBrain(wl.StandStill):
    def __init__(self):
        self.updates = 0

    def update(self, duel, dt, now):
        self.updates += 1


def play(duel, a, seconds, dt=0.01, latency=0.02, loss=0.0, seed=0, events=None):
    """Run B (duel + TurnTaker) against FirmwareA in simulated time. events: {time: fn(t)}."""
    rng = random.Random(seed)
    turns = wl.TurnTaker()
    to_a, to_b = [], []                            # (arrival time, bytes)
    a.connect(0.0)
    duel.start(0.0)
    events = dict(events or {})
    t = 0.0
    while t < seconds:
        t = round(t + dt, 6)
        for when in sorted(k for k in events if k <= t):
            events.pop(when)(t)
        for item in [m for m in to_a if m[0] <= t]:
            to_a.remove(item)
            a.receive(item[1], t)
        out = a.step(t)
        if out is not None and rng.random() >= loss:
            to_b.append((t + latency, out))
        for item in [m for m in to_b if m[0] <= t]:
            to_b.remove(item)
            pkt = wl.unpack(item[1])
            turns.on_reply()
            duel.on_packet(pkt, t)
        duel.update(t, dt)
        if turns.should_send(t):
            data = wl.pack(duel.packet(t))
            turns.on_send(t)
            if rng.random() >= loss:
                to_a.append((t + latency, data))
    return turns


class PacketTests(unittest.TestCase):
    def test_packet_is_12_bytes_in_firmware_order(self):
        p = wl.DuelPacket(seq=5, round=2, x=13.5, y=13.5, angle=math.pi, hits_landed=1,
                          flags=wl.FLAG_FIRED, shots=7, rounds=3)
        data = wl.pack(p)
        self.assertEqual(len(data), 12)
        expected = bytes([0xB0, 5, 2]) + (3456).to_bytes(2, "little") + (3456).to_bytes(2, "little") \
            + bytes([255, 1, 1, 7, 3])
        self.assertEqual(data, expected)

    def test_unpack_round_trip_and_rejects(self):
        p = wl.DuelPacket(200, 4, 2.5, 7.75, 0.0, 2, 0, 9, 5)
        q = wl.unpack(wl.pack(p))
        self.assertEqual((q.seq, q.round, q.hits_landed, q.shots, q.rounds), (200, 4, 2, 9, 5))
        self.assertAlmostEqual(q.x, 2.5)
        self.assertAlmostEqual(q.y, 7.75)
        self.assertIsNone(wl.unpack(wl.pack(p)[:11]))                 # wrong size
        self.assertIsNone(wl.unpack(b"\xC1" + wl.pack(p)[1:]))       # wrong magic

    def test_angle_matches_the_firmware_formula(self):
        def firmware(dir_x, dir_y):                                   # (uint8_t)((atan2f + PI) / (2PI) * 255)
            return int((math.atan2(dir_y, dir_x) + math.pi) / (2 * math.pi) * 255)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.6, -0.8)):
            self.assertEqual(wl.angle_to_byte(math.atan2(dy, dx)), firmware(dx, dy), (dx, dy))
        self.assertEqual(wl.angle_to_byte(math.pi), 255)                # west, B's spawn facing
        for b in (wl.angle_to_byte(-math.pi), wl.angle_to_byte(math.pi)):    # -pi gives 0, +pi 255:
            self.assertAlmostEqual(math.cos(wl.byte_to_angle(b)), -1.0, places=3)  # both mean west
        self.assertEqual(wl.angle_to_byte(0.0), 127)                    # east
        self.assertAlmostEqual(wl.byte_to_angle(127), 0.0, delta=0.02)


class TurnTakingTests(unittest.TestCase):
    def test_b_speaks_first_then_waits_for_reply_or_100ms(self):
        tt = wl.TurnTaker()
        self.assertTrue(tt.should_send(0.0))
        tt.on_send(0.0)
        self.assertFalse(tt.should_send(0.05))
        tt.on_reply()
        self.assertFalse(tt.should_send(0.05))                          # answered, but only 50 ms
        self.assertTrue(tt.should_send(0.10))
        self.assertEqual(tt.timeouts, 0)

    def test_sends_anyway_after_300ms_without_reply(self):
        tt = wl.TurnTaker()
        tt.on_send(0.0)
        self.assertFalse(tt.should_send(0.29))
        self.assertTrue(tt.should_send(0.30))
        tt.on_send(0.30)
        self.assertEqual(tt.timeouts, 1)
        self.assertAlmostEqual(tt.wait_time(0.35), 0.25)


class DuelTests(unittest.TestCase):
    def test_step1_watch_sees_player_b_in_the_corner(self):
        duel, a = wl.DuelB(), FirmwareA()
        turns = play(duel, a, 2.0)
        self.assertEqual(a.opp_pos, (13.5, 13.5))                       # "the red player in the corner"
        self.assertEqual(a.opp_round, 1)
        self.assertGreaterEqual(turns.sent, 15)                         # about 10 packets per second
        self.assertLessEqual(turns.sent, 22)
        self.assertEqual(turns.timeouts, 0)
        self.assertEqual(duel.state, "playing")

    def test_watch_player_wins_rounds_and_both_sides_stay_in_step(self):
        duel, a = wl.DuelB(), FirmwareA(rounds_to_win=3)
        events = {}
        for k in range(3):                                              # A shoots B twice each round
            base = 1.0 + k * 4.0
            events[base] = lambda t: a.land_hit()
            events[base + 0.5] = lambda t: a.land_hit()
        play(duel, a, 16.0, events=events)
        self.assertEqual(a.results, [1, 1, 1])
        self.assertEqual((duel.rounds_a, duel.rounds_b), (3, 0))
        self.assertEqual(duel.state, "match_over")
        self.assertEqual(a.state, "match_over")
        self.assertEqual(duel.round, a.my_round)
        self.assertEqual(duel.rounds_to_win, 3)                         # taken from the watch

    def test_laptop_wins_when_its_hits_reach_two(self):
        duel, a = wl.DuelB(AlwaysHit()), FirmwareA(rounds_to_win=1)
        play(duel, a, 4.0)
        self.assertEqual(a.results, [-1])                               # the watch lost
        self.assertEqual((duel.rounds_a, duel.rounds_b), (0, 1))
        self.assertEqual(duel.state, "match_over")

    def test_round_over_pause_then_next_round_back_at_spawn(self):
        duel, a = wl.DuelB(AlwaysHit()), FirmwareA(rounds_to_win=3)
        play(duel, a, 2.0)
        self.assertEqual(duel.state, "round_over")
        self.assertEqual(duel.round, 1)                                 # keeps sending round 1 ...
        self.assertEqual(duel.packet(2.0).hits_landed, 2)               # ... with the final count
        duel.x, duel.y = 5.0, 5.0
        duel.update(2.0 + wl.ROUND_OVER_S + 1.0, 0.01)
        self.assertEqual((duel.round, duel.state), (2, "playing"))
        self.assertEqual((duel.x, duel.y, duel.hits_landed), (13.5, 13.5, 0))

    def test_hits_from_another_round_are_not_damage(self):
        duel = wl.DuelB()
        duel.start(0.0)
        old = wl.DuelPacket(0, 0, 2.5, 2.5, 0.0, 2, 0, 0, 3)            # watch still on round 0
        duel.on_packet(old, 0.1)
        duel.update(0.1, 0.01)
        self.assertEqual(duel.hits_taken, 0)
        self.assertEqual(duel.state, "playing")
        now = wl.DuelPacket(1, 1, 2.5, 2.5, 0.0, 1, 0, 0, 3)
        duel.on_packet(now, 0.2)
        self.assertEqual(duel.hits_taken, 1)

    def test_catches_up_when_the_watch_is_on_a_later_round(self):
        duel = wl.DuelB()
        duel.start(0.0)
        duel.on_packet(wl.DuelPacket(0, 3, 2.5, 2.5, 0.0, 0, 0, 0, 5), 0.1)
        duel.update(0.1, 0.01)
        self.assertEqual((duel.round, duel.state), (3, "playing"))
        self.assertEqual(duel.rounds_to_win, 5)

    def test_bad_rounds_setting_is_ignored(self):
        duel = wl.DuelB()
        duel.start(0.0)
        duel.on_packet(wl.DuelPacket(0, 1, 2.5, 2.5, 0.0, 0, 0, 0, 7), 0.1)
        self.assertEqual(duel.rounds_to_win, 3)

    def test_watch_pause_freezes_the_laptop_player(self):
        brain = CountingBrain()
        duel, a = wl.DuelB(brain), FirmwareA()
        play(duel, a, 1.0, events={0.3: lambda t: setattr(a, "paused_by_self", True)})
        frozen_at = brain.updates
        self.assertTrue(duel.a_paused)
        self.assertLess(frozen_at, 40)                                  # stopped soon after 0.3 s
        duel.update(5.0, 0.01)
        self.assertEqual(brain.updates, frozen_at)

    def test_draw_when_both_reach_two_together(self):
        duel = wl.DuelB()
        duel.start(0.0)
        duel.hits_landed = 2
        duel.on_packet(wl.DuelPacket(0, 1, 2.5, 2.5, 0.0, 2, 0, 0, 3), 0.1)
        duel.update(0.1, 0.01)
        self.assertEqual(duel.state, "round_over")
        self.assertEqual((duel.rounds_a, duel.rounds_b), (0, 0))
        self.assertIn("draw", duel.log[-1])

    def test_rounds_stay_in_step_with_30_percent_packet_loss(self):
        for seed in range(5):
            duel, a = wl.DuelB(AlwaysHit()), FirmwareA(rounds_to_win=5)
            events = {2.0 + 6 * k: (lambda t: a.land_hit()) for k in range(4)}
            events.update({2.4 + 6 * k: (lambda t: a.land_hit()) for k in range(4)})
            turns = play(duel, a, 40.0, loss=0.3, seed=seed, events=events)
            self.assertEqual(duel.state, "match_over", seed)
            self.assertEqual(a.state, "match_over", seed)
            self.assertEqual(sorted(a.results), sorted(-r for r in self._b_results(duel)), seed)
            self.assertGreater(turns.timeouts, 0)                       # the 300 ms rule was needed

    @staticmethod
    def _b_results(duel):
        out = []
        for line in duel.log:
            if line.startswith("round"):
                out.append(1 if "B wins" in line else (-1 if "A wins" in line else 0))
        return out


class FakeClient:
    """Stands in for bleak's BleakClient: a FirmwareA watch behind a 15 ms radio delay."""

    def __init__(self, watch, reply=True):
        self.watch = watch
        self.reply = reply
        self.is_connected = True
        self.callback = None
        self.writes = []

    async def start_notify(self, uuid, callback):
        assert uuid == wl.UART_NOTIFY
        self.callback = callback

    async def write_gatt_char(self, uuid, data, response=True):
        assert uuid == wl.UART_WRITE and response is False and len(data) == 12
        now = time.monotonic()
        self.writes.append(now)
        self.watch.receive(data, now)
        out = self.watch.step(now)
        if out is not None and self.reply:
            asyncio.get_running_loop().call_later(0.015, self.callback, None, bytearray(out))


class SessionTests(unittest.TestCase):
    def _run(self, client, seconds):
        async def go():
            stop = asyncio.Event()
            asyncio.get_running_loop().call_later(seconds, stop.set)
            duel = wl.DuelB()
            client.watch.connect(time.monotonic())
            stats = await wl.run_session(client, duel, out=lambda s: None, stop=stop)
            return duel, stats
        return asyncio.run(go())

    def test_real_time_loop_talks_about_ten_times_a_second(self):
        client = FakeClient(FirmwareA())
        duel, stats = self._run(client, 1.5)
        self.assertGreaterEqual(stats["sent"], 11)
        self.assertLessEqual(stats["sent"], 17)
        self.assertGreaterEqual(stats["replies"], stats["sent"] - 2)
        self.assertEqual((stats["timeouts"], stats["bad"]), (0, 0))
        self.assertEqual(client.watch.opp_pos, (13.5, 13.5))
        gaps = [b - a for a, b in zip(client.writes, client.writes[1:])]
        self.assertGreaterEqual(min(gaps), 0.095)                       # never faster than 10/s

    def test_keeps_sending_every_300ms_if_the_watch_never_answers(self):
        client = FakeClient(FirmwareA(), reply=False)
        _, stats = self._run(client, 1.0)
        self.assertGreaterEqual(stats["sent"], 3)
        self.assertLessEqual(stats["sent"], 5)
        self.assertGreaterEqual(stats["timeouts"], 2)

    def test_stops_when_the_watch_disconnects(self):
        client = FakeClient(FirmwareA())

        async def go():
            asyncio.get_running_loop().call_later(0.3, setattr, client, "is_connected", False)
            return await wl.run_session(client, wl.DuelB(), out=lambda s: None)
        start = time.monotonic()
        stats = asyncio.run(go())
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertGreater(stats["sent"], 0)


if __name__ == "__main__":
    unittest.main()
