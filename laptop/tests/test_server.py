"""Tests for the ARENA game server. Run from the laptop folder:  python -m unittest -v"""
import math
import random
import unittest

from server import protocol
from server.bots import BotBrain
from server.rules import FreeForAll, Game
from server.server import run_demo
from server.world import GameMap


def two_players(game, shooter_xy=(2.5, 2.5), target_xy=(5.5, 2.5)):
    a = game.add_player(0, "A")
    b = game.add_player(1, "B")
    a.x, a.y = shooter_xy
    b.x, b.y = target_xy
    a.angle = math.atan2(b.y - a.y, b.x - a.x)    # A faces B
    return a, b


class MapTests(unittest.TestCase):
    def test_default_map_is_playable(self):
        m = GameMap()
        self.assertTrue(m.is_connected())
        for sx, sy in m.spawns:
            self.assertFalse(m.is_wall(sx, sy))

    def test_walls_and_bounds(self):
        m = GameMap()
        self.assertTrue(m.is_wall(0.5, 0.5))
        self.assertTrue(m.is_wall(-1, 3))
        self.assertFalse(m.is_wall(2.5, 2.5))

    def test_line_of_sight(self):
        m = GameMap()
        self.assertTrue(m.line_of_sight(2.5, 2.5, 5.5, 2.5))
        self.assertFalse(m.line_of_sight(2.5, 2.5, 13.5, 13.5))   # opposite corners are walled off

    def test_pathfinding(self):
        m = GameMap()
        path = m.find_path((1, 1), (14, 14))
        self.assertIsNotNone(path)
        self.assertEqual(path[0], (1, 1))
        self.assertEqual(path[-1], (14, 14))
        for c1, c2 in zip(path, path[1:]):
            self.assertEqual(abs(c1[0] - c2[0]) + abs(c1[1] - c2[1]), 1)
            self.assertFalse(m.is_wall(c2[0] + .5, c2[1] + .5))
        self.assertIsNone(m.find_path((1, 1), (0, 0)))           # wall is unreachable

    def test_disconnected_map_detected(self):
        grid = [[1, 1, 1, 1, 1],
                [1, 0, 1, 0, 1],
                [1, 1, 1, 1, 1]]
        self.assertFalse(GameMap(grid, [(1.5, 1.5)]).is_connected())


class ProtocolTests(unittest.TestCase):
    def test_state_round_trip(self):
        s = protocol.State(2, 7, 3.5, 12.25, math.pi, True, False, 1)
        data = protocol.build_state(s)
        self.assertEqual(len(data), 10)
        r = protocol.parse_state(data)
        self.assertEqual((r.player_id, r.seq, r.fired, r.ready, r.target_id), (2, 7, True, False, 1))
        self.assertAlmostEqual(r.x, 3.5, places=2)
        self.assertAlmostEqual(r.y, 12.25, places=2)
        self.assertAlmostEqual(r.angle, math.pi, places=1)

    def test_bad_packets_rejected(self):
        self.assertIsNone(protocol.parse_state(b"\x00" * 10))                 # wrong magic
        self.assertIsNone(protocol.parse_state(b"\xC1\x00"))                  # too short
        self.assertIsNone(protocol.parse_state(b"\xC1\x09" + b"\x00" * 8))    # player id out of range

    def test_world_packet(self):
        data = protocol.build_world(protocol.GAME_PLAYING, 2, protocol.WORLD_HIT_ME,
                                    [(0, 2.5, 3.0), (10, 13.5, 13.5)] + [(20, 1, 1)] * 6)
        self.assertLessEqual(len(data), 20)
        w = protocol.parse_world(data)
        self.assertEqual(len(w["entities"]), protocol.MAX_ENTITIES)
        self.assertEqual(w["health"], 2)
        self.assertAlmostEqual(w["entities"][0][1], 2.5, places=1)

    def test_world_packet_clamps_out_of_range(self):
        data = protocol.build_world(1, 2, 0, [(0, -1.0, 3.0), (1, 20.0, 99.0)])
        w = protocol.parse_world(data)
        self.assertEqual(w["entities"][0][1], 0.0)
        self.assertLessEqual(w["entities"][1][1], 16.0)


class RulesTests(unittest.TestCase):
    def test_clear_shot_hits_and_kills(self):
        g = Game(rng=random.Random(0))
        a, b = two_players(g)
        self.assertTrue(g.try_hit(0, 1, 1.0))
        self.assertEqual(b.hp, 1)
        self.assertTrue(g.try_hit(0, 1, 1.5))
        self.assertFalse(b.alive)
        self.assertEqual((a.kills, b.deaths), (1, 1))
        self.assertIn("A eliminated B", g.feed)

    def test_fire_rate_limited(self):
        g = Game(rng=random.Random(0))
        _, b = two_players(g)
        self.assertTrue(g.try_hit(0, 1, 1.0))
        self.assertFalse(g.try_hit(0, 1, 1.1))     # 0.1 s later: too fast
        self.assertEqual(b.hp, 1)

    def test_wall_blocks_hit(self):
        g = Game(rng=random.Random(0))
        two_players(g, (2.5, 2.5), (13.5, 13.5))
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_must_be_aiming_at_target(self):
        g = Game(rng=random.Random(0))
        a, _ = two_players(g)
        a.angle += 1.5
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_out_of_range(self):
        g = Game(rng=random.Random(0))
        a, b = two_players(g, (1.5, 8.5), (14.5, 8.5))
        a.angle = 0.0
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_cannot_shoot_self_dead_or_unknown(self):
        g = Game(rng=random.Random(0))
        _, b = two_players(g)
        self.assertFalse(g.try_hit(0, 0, 1.0))
        self.assertFalse(g.try_hit(0, 99, 1.0))
        b.alive = False
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_respawn(self):
        g = Game(rng=random.Random(0))
        _, b = two_players(g)
        g.try_hit(0, 1, 1.0)
        g.try_hit(0, 1, 1.5)
        g.tick(2.0)
        self.assertFalse(b.alive)
        g.tick(1.5 + Game.RESPAWN_S + 0.01)
        self.assertTrue(b.alive)
        self.assertEqual(b.hp, Game.MAX_HP)

    def test_ffa_winner_and_pause(self):
        g = Game(mode=FreeForAll(kills_to_win=1), rng=random.Random(0))
        a, _ = two_players(g)
        g.paused = True
        self.assertFalse(g.try_hit(0, 1, 1.0))      # no hits while paused
        g.paused = False
        g.try_hit(0, 1, 1.0)
        g.try_hit(0, 1, 1.5)
        g.tick(2.0)
        self.assertIs(g.winner, a)
        g.restart()
        self.assertIsNone(g.winner)
        self.assertEqual(a.kills, 0)

    def test_watch_state_applies_and_fires(self):
        g = Game(rng=random.Random(0))
        a, b = two_players(g)
        st = protocol.State(0, 1, 2.5, 2.5, 0.0, True, False, 1)
        self.assertTrue(g.apply_state(st, 1.0))
        self.assertEqual(b.hp, 1)
        wall = protocol.State(0, 2, 0.5, 0.5, 0.0, False, False, 0xFF)
        g.apply_state(wall, 2.0)
        self.assertEqual((a.x, a.y), (2.5, 2.5))     # position inside a wall ignored

    def test_snapshot_shape(self):
        g = Game()
        g.add_player(0, "A")
        g.add_player(10, "Bot", is_bot=True)
        s = g.snapshot()
        self.assertEqual(s["type"], "world")
        self.assertEqual(len(s["players"]), 1)
        self.assertEqual(len(s["bots"]), 1)
        self.assertEqual(set(s["bots"][0]), {"id", "x", "y", "hp"})

    def test_respawn_uses_same_spawn_as_first_spawn(self):
        g = Game(rng=random.Random(0))
        bots = [g.add_player(10 + i, f"Bot{i}", is_bot=True) for i in range(4)]
        starts = [(b.x, b.y) for b in bots]
        self.assertEqual(len(set(starts)), 4)               # four different corners
        for b in bots:
            b.x, b.y = 8.5, 8.5
            g.spawn(b)
        self.assertEqual([(b.x, b.y) for b in bots], starts)


class BotTests(unittest.TestCase):
    def test_bot_hunts_and_shoots_visible_enemy(self):
        g = Game(rng=random.Random(3))
        a, b = two_players(g, (2.5, 2.5), (6.5, 2.5))
        brain = BotBrain(g, a)
        t = 0.0
        while t < 10 and b.hp == Game.MAX_HP:
            t += 0.05
            brain.update(0.05, t)
        self.assertLess(b.hp, Game.MAX_HP)

    def test_bot_stays_out_of_walls(self):
        g = Game(rng=random.Random(5))
        bot = g.add_player(10, "Bot", is_bot=True)
        brain = BotBrain(g, bot)
        for i in range(2000):
            brain.update(0.05, i * 0.05)
            self.assertFalse(g.map.is_wall(bot.x, bot.y))

    def test_demo_match_finishes_deterministically(self):
        lines1, lines2 = [], []
        g1 = run_demo(4, 300, 5, seed=1, out=lines1.append)
        run_demo(4, 300, 5, seed=1, out=lines2.append)
        self.assertIsNotNone(g1.winner)
        self.assertEqual(lines1, lines2)


if __name__ == "__main__":
    unittest.main()
