"""Tests for server/arena_live.py (arena bots on the web dashboard) - no web server started."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.arena_live import ArenaFeed  # noqa: E402


def cmd(action, token="pw", **settings):
    return json.dumps({"type": "referee_command", "action": action, "host_token": token, "settings": settings})


class ArenaLiveTest(unittest.TestCase):
    def setUp(self):
        self.f = ArenaFeed(n_bots=4, minutes=1, kills=3, host_token="pw", seed=1)
        self.f.send = lambda msg: None          # no web server in tests
        for _ in range(5):
            self.f.step(0.05)

    def test_snapshot_is_ffa_with_all_bots(self):
        s = self.f.snapshot()
        self.assertEqual(s["match"]["mode"], "ffa")
        self.assertEqual(s["match"]["status"], "playing")
        self.assertEqual(len(s["players"]), 4)
        self.assertEqual(len(s["map"]), 16)

    def test_referee_needs_the_token(self):
        ok, _ = self.f.referee(cmd("pause", token="wrong"))
        self.assertFalse(ok)
        self.assertFalse(self.f.game.paused)

    def test_pause_freezes_the_clock_and_resume(self):
        self.assertTrue(self.f.referee(cmd("pause"))[0])
        left = self.f.game.time_left
        for _ in range(20):
            self.f.step(0.05)
        self.assertEqual(self.f.game.time_left, left)
        self.assertEqual(self.f.snapshot()["match"]["status"], "paused")
        self.assertTrue(self.f.referee(cmd("resume"))[0])
        self.f.step(0.05)
        self.assertLess(self.f.game.time_left, left)

    def test_settings_change_duration_and_restart(self):
        ok, _ = self.f.referee(cmd("settings", mode="ffa", duration_seconds=180))
        self.assertTrue(ok)
        self.assertEqual(self.f.snapshot()["match"]["duration_seconds"], 180)
        self.assertFalse(self.f.referee(cmd("settings", mode="koth", duration_seconds=180))[0])

    def test_match_finishes_and_next_one_starts(self):
        for _ in range(int(80 / 0.05)):           # 1 min match + scoreboard pause, simulated
            self.f.step(0.05)
            if self.f.last_result:
                break
        self.assertIsNotNone(self.f.last_result)
        self.assertEqual(self.f.snapshot()["match"]["status"], "finished")


if __name__ == "__main__":
    unittest.main()
