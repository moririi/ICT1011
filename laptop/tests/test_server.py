"""Tests for the ARENA game server. Run from the laptop folder:  python -m unittest -v"""
import math
import random
import unittest

from server import protocol
from server.bots import BotBrain
from server.rules import FreeForAll, Game
from server.server import run_demo
from server.world import GameMap


def new_game(**kw):
    """A Game already in the PLAYING state (skips the lobby)."""
    g = Game(**kw)
    g.state = "playing"
    return g


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
        g = new_game(rng=random.Random(0))
        a, b = two_players(g)
        self.assertTrue(g.try_hit(0, 1, 1.0))
        self.assertEqual(b.hp, 1)
        self.assertTrue(g.try_hit(0, 1, 1.5))
        self.assertFalse(b.alive)
        self.assertEqual((a.kills, b.deaths), (1, 1))
        self.assertIn("A eliminated B", g.feed)

    def test_fire_rate_limited(self):
        g = new_game(rng=random.Random(0))
        _, b = two_players(g)
        self.assertTrue(g.try_hit(0, 1, 1.0))
        self.assertFalse(g.try_hit(0, 1, 1.1))     # 0.1 s later: too fast
        self.assertEqual(b.hp, 1)

    def test_wall_blocks_hit(self):
        g = new_game(rng=random.Random(0))
        two_players(g, (2.5, 2.5), (13.5, 13.5))
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_must_be_aiming_at_target(self):
        g = new_game(rng=random.Random(0))
        a, _ = two_players(g)
        a.angle += 1.5
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_out_of_range(self):
        g = new_game(rng=random.Random(0))
        a, b = two_players(g, (1.5, 8.5), (14.5, 8.5))
        a.angle = 0.0
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_cannot_shoot_self_dead_or_unknown(self):
        g = new_game(rng=random.Random(0))
        _, b = two_players(g)
        self.assertFalse(g.try_hit(0, 0, 1.0))
        self.assertFalse(g.try_hit(0, 99, 1.0))
        b.alive = False
        self.assertFalse(g.try_hit(0, 1, 1.0))

    def test_respawn(self):
        g = new_game(rng=random.Random(0))
        _, b = two_players(g)
        g.try_hit(0, 1, 1.0)
        g.try_hit(0, 1, 1.5)
        g.tick(2.0)
        self.assertFalse(b.alive)
        g.tick(1.5 + Game.RESPAWN_S + 0.01)
        self.assertTrue(b.alive)
        self.assertEqual(b.hp, Game.MAX_HP)

    def test_ffa_winner_and_pause(self):
        g = new_game(mode=FreeForAll(kills_to_win=1), rng=random.Random(0))
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
        g = new_game(rng=random.Random(0))
        a, b = two_players(g)
        st = protocol.State(0, 1, 2.5, 2.5, 0.0, True, False, 1)
        self.assertTrue(g.apply_state(st, 1.0))
        self.assertEqual(b.hp, 1)
        wall = protocol.State(0, 2, 0.5, 0.5, 0.0, False, False, 0xFF)
        g.apply_state(wall, 2.0)
        self.assertEqual((a.x, a.y), (2.5, 2.5))     # position inside a wall ignored

    def test_snapshot_shape(self):
        g = new_game()
        g.add_player(0, "A")
        g.add_player(10, "Bot", is_bot=True)
        s = g.snapshot()
        self.assertEqual(s["type"], "world")
        self.assertEqual(len(s["players"]), 1)
        self.assertEqual(len(s["bots"]), 1)
        self.assertEqual(set(s["bots"][0]), {"id", "x", "y", "hp"})

    def test_respawn_uses_same_spawn_as_first_spawn(self):
        g = new_game(rng=random.Random(0))
        bots = [g.add_player(10 + i, f"Bot{i}", is_bot=True) for i in range(4)]
        starts = [(b.x, b.y) for b in bots]
        self.assertEqual(len(set(starts)), 4)               # four different corners
        for b in bots:
            b.x, b.y = 8.5, 8.5
            g.spawn(b)
        self.assertEqual([(b.x, b.y) for b in bots], starts)


def state_pkt(pid, x, y, angle=0.0, fired=False, ready=False, target=0xFF):
    return protocol.State(pid, 0, x, y, angle, fired, ready, target)


class LifecycleTests(unittest.TestCase):
    def test_lobby_waits_for_everyone_ready(self):
        g = Game(rng=random.Random(0))
        g.add_player(0, "A")
        g.add_player(1, "B")
        g.tick(0.1)
        self.assertEqual(g.state, "lobby")
        g.apply_state(state_pkt(0, 2.5, 2.5, ready=True), 0.2)
        g.tick(0.3)
        self.assertEqual(g.state, "lobby")                  # B is not ready yet
        g.apply_state(state_pkt(1, 13.5, 13.5, ready=True), 0.4)
        g.tick(0.5)
        self.assertEqual(g.state, "playing")
        self.assertIn("match started", g.feed)

    def test_one_player_never_starts(self):
        g = Game()
        g.add_player(0, "A").ready = True
        g.tick(1.0)
        self.assertEqual(g.state, "lobby")

    def test_no_hits_in_lobby_or_after_match(self):
        g = Game(rng=random.Random(0))
        a, b = two_players(g)
        self.assertFalse(g.try_hit(0, 1, 1.0))              # lobby
        g.start_match(1.0)
        a.x, a.y, b.x, b.y = 2.5, 2.5, 5.5, 2.5
        a.angle = 0.0
        g.end_match(a)
        self.assertFalse(g.try_hit(0, 1, 3.0))              # match over

    def test_missed_shots_are_counted_once_per_cooldown(self):
        g = new_game(rng=random.Random(0))
        a, _ = two_players(g)
        g.apply_state(state_pkt(0, 2.5, 2.5, fired=True), 1.0)       # nobody in the crosshair
        g.apply_state(state_pkt(0, 2.5, 2.5, fired=True), 1.05)      # same trigger pull, next packet
        self.assertEqual((a.shots, a.hits), (1, 0))
        g.apply_state(state_pkt(0, 2.5, 2.5, fired=True), 1.5)
        self.assertEqual(a.shots, 2)

    def test_time_up_most_kills_wins(self):
        g = new_game(match_seconds=5.0, rng=random.Random(0))
        a, b = two_players(g)
        a.kills = 1
        t = 0.0
        while g.state == "playing" and t < 10:
            t += 0.1
            g.tick(t)
        self.assertEqual(g.state, "over")
        self.assertIs(g.winner, a)
        self.assertEqual(g.result, "win")
        self.assertLess(t, 5.5)

    def test_time_up_tiebreak_fewer_deaths_then_draw(self):
        g = new_game(match_seconds=1.0, rng=random.Random(0))
        a, b = two_players(g)
        a.kills = b.kills = 2
        a.deaths, b.deaths = 1, 3
        g.state, g.time_left = "playing", 0.0
        g.tick(0.1)
        self.assertIs(g.winner, a)                          # same kills, A died less

        g2 = new_game(match_seconds=1.0, rng=random.Random(0))
        two_players(g2)
        for i in range(1, 30):
            g2.tick(i * 0.1)
        self.assertEqual(g2.state, "over")
        self.assertIsNone(g2.winner)
        self.assertEqual(g2.result, "draw")

    def test_pause_freezes_the_clock(self):
        g = new_game(match_seconds=5.0)
        two_players(g)
        g.tick(0.1)
        g.paused = True
        for i in range(2, 100):
            g.tick(i * 0.1)
        self.assertEqual(g.state, "playing")
        self.assertAlmostEqual(g.time_left, 4.9, places=2)      # only the one tick before the pause counted
        self.assertEqual(g.protocol_state(), protocol.GAME_PAUSED)

    def test_a_long_stall_does_not_eat_the_clock(self):
        g = new_game(match_seconds=60.0)
        two_players(g)
        g.tick(0.1)
        g.tick(30.0)                                        # the server froze for 30 s
        self.assertGreater(g.time_left, 59.0)

    def test_reset_to_lobby(self):
        g = new_game()
        a, _ = two_players(g)
        g.end_match(a)
        self.assertEqual(g.protocol_state(), protocol.GAME_ROUND_OVER)
        g.reset_to_lobby()
        self.assertEqual((g.state, g.winner, g.result), ("lobby", None, ""))

    def test_teleport_rejected_then_allowed_after_time(self):
        g = new_game()
        a, _ = two_players(g, (2.5, 2.5), (13.5, 13.5))
        g.apply_state(state_pkt(0, 2.5, 2.5), 1.0)
        g.apply_state(state_pkt(0, 13.5, 2.5), 1.05)        # 11 squares in 0.05 s
        self.assertEqual((a.x, a.y), (2.5, 2.5))
        g.apply_state(state_pkt(0, 5.5, 2.5), 2.0)          # a normal walk is fine
        self.assertEqual((a.x, a.y), (5.5, 2.5))

    def test_respawn_goes_to_the_farthest_spawn(self):
        g = new_game()
        a, b = two_players(g)
        a.x, a.y = 2.5, 2.5                                 # A stands in the top-left corner
        b.alive, b.respawn_at = False, 1.0
        g.tick(1.5)
        self.assertTrue(b.alive)
        self.assertEqual((b.x, b.y), (13.5, 13.5))          # opposite corner

    def test_snapshot_has_match_info(self):
        g = new_game()
        two_players(g)
        s = g.snapshot()
        self.assertEqual(s["state"], "playing")
        self.assertIn("time_left", s)
        self.assertIsNone(s["winner"])


class BotTests(unittest.TestCase):
    def test_bot_hunts_and_shoots_visible_enemy(self):
        g = new_game(rng=random.Random(3))
        a, b = two_players(g, (2.5, 2.5), (6.5, 2.5))
        brain = BotBrain(g, a)
        t = 0.0
        while t < 10 and b.hp == Game.MAX_HP:
            t += 0.05
            brain.update(0.05, t)
        self.assertLess(b.hp, Game.MAX_HP)

    def test_bot_stays_out_of_walls(self):
        g = new_game(rng=random.Random(5))
        bot = g.add_player(10, "Bot", is_bot=True)
        brain = BotBrain(g, bot)
        for i in range(2000):
            brain.update(0.05, i * 0.05)
            self.assertFalse(g.map.is_wall(bot.x, bot.y))

    def test_hurt_bot_keeps_fleeing_when_enemy_out_of_sight(self):
        g = new_game(rng=random.Random(2))
        bot = g.add_player(10, "Bot", is_bot=True)
        foe = g.add_player(0, "Foe")
        bot.x, bot.y, bot.hp = 2.5, 2.5, 1
        foe.x, foe.y = 5.5, 2.5
        brain = BotBrain(g, bot)
        t = 0.0
        brain.update(0.05, t := t + 0.05)
        self.assertEqual(brain.state, "flee")
        foe.alive = False                                   # the enemy vanishes from view
        while t < brain.flee_until - 0.1:
            brain.update(0.05, t := t + 0.05)
            self.assertEqual(brain.state, "flee")

    def test_demo_match_finishes_deterministically(self):
        lines1, lines2 = [], []
        g1 = run_demo(4, 300, 5, seed=1, out=lines1.append)
        run_demo(4, 300, 5, seed=1, out=lines2.append)
        self.assertEqual(g1.state, "over")
        self.assertEqual(lines1, lines2)


if __name__ == "__main__":
    unittest.main()
