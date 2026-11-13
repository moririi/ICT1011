"""Tests for the real-time arena: links, WORLD/EVENT packets, fake watches and full simulated matches.
Run from the laptop folder:  python -m unittest -v"""
import asyncio
import random
import unittest

from server import protocol as proto
from server.arena import ArenaServer, Link, VISION_RANGE
from server.fakewatch import AutoWatch, FakeWatch, KeyboardWatch
from server.play import render_ascii
from server.rules import FreeForAll, Game
from server.sim import run_sim


def quiet(_):
    pass


def make_arena(kills=5, rematch=False):
    g = Game(mode=FreeForAll(kills), rng=random.Random(0))
    arena = ArenaServer(g, rematch=rematch)
    links = {0: Link(), 1: Link()}
    arena.add_watch(0, "A", links[0])
    arena.add_watch(1, "B", links[1])
    return arena, links


def playing_arena(**kw):
    """An arena whose match has started, with both watches confirmed at their spawn points."""
    arena, links = make_arena(**kw)
    arena.game.start_match(0.0)
    for pid in (0, 1):
        p = arena.game.players[pid]
        p.awaiting_spawn = False
    return arena, links


def state_bytes(pid, x, y, seq=0, angle=0.0, fired=False, ready=False, target=proto.NO_TARGET):
    return proto.build_state(proto.State(pid, seq, x, y, angle, fired, ready, target))


def drain(link):
    """Everything the server sent to the watch, split into (worlds, events)."""
    worlds, events = [], []
    while link.to_watch:
        data = link.to_watch.popleft()
        if data[0] == proto.MAGIC_WORLD:
            worlds.append(proto.parse_world(data))
        elif data[0] == proto.MAGIC_EVENT:
            events.append(proto.parse_event(data))
    return worlds, events


class SimulationTests(unittest.TestCase):
    def test_two_fake_watches_play_a_whole_match(self):
        arena, _ = run_sim(seed=1, out=quiet)
        self.assertEqual(arena.game.state, "over")
        self.assertIsNotNone(arena.game.winner)
        for st in arena.stats.values():
            self.assertGreater(st["packets"], 100)
            self.assertEqual((st["lost"], st["bad"]), (0, 0))

    def test_match_survives_packet_loss(self):
        lost = 0
        for seed in range(3):
            arena, _ = run_sim(seed=seed, loss=0.1, out=quiet)
            self.assertEqual(arena.game.state, "over")
            self.assertTrue(all(st["bad"] == 0 for st in arena.stats.values()))
            lost += sum(st["lost"] for st in arena.stats.values())
        self.assertGreater(lost, 0)             # the loss really happened and was noticed

    def test_simulation_is_deterministic(self):
        a, b = [], []
        run_sim(seed=4, out=a.append)
        run_sim(seed=4, out=b.append)
        self.assertEqual(a, b)

    def test_server_bot_can_join_the_match(self):
        arena, _ = run_sim(seed=3, bots=1, out=quiet)
        self.assertEqual(arena.game.state, "over")
        self.assertEqual(len(arena.game.players), 3)


class VisibilityTests(unittest.TestCase):
    def test_far_enemy_hidden_unless_it_just_fired(self):
        arena, _ = playing_arena()
        a, b = arena.game.players[0], arena.game.players[1]
        a.x, a.y, b.x, b.y = 2.5, 8.5, 12.5, 8.5           # 10 squares apart, clear line of sight
        self.assertGreater(10.0, VISION_RANGE)
        self.assertEqual(arena.visible_to(0, 5.0), [])
        b.last_shot = 4.9                                   # muzzle flash
        self.assertEqual([e[0] for e in arena.visible_to(0, 5.0)], [1])
        self.assertEqual(arena.visible_to(0, 5.5), [])      # the flash is over

    def test_near_enemy_visible_but_not_through_a_wall(self):
        arena, _ = playing_arena()
        a, b = arena.game.players[0], arena.game.players[1]
        a.x, a.y, b.x, b.y = 2.5, 2.5, 5.5, 2.5
        self.assertEqual([e[0] for e in arena.visible_to(0, 5.0)], [1])
        a.x, a.y, b.x, b.y = 5.5, 6.5, 7.5, 6.5             # 2 squares apart, wall between
        self.assertEqual(arena.visible_to(0, 5.0), [])

    def test_dead_players_are_not_shown_and_list_is_capped(self):
        arena, _ = playing_arena()
        g = arena.game
        a = g.players[0]
        a.x, a.y = 2.5, 2.5
        spots = [(1.5, 1.5), (3.5, 1.5), (4.5, 1.5), (5.5, 1.5), (6.5, 1.5), (1.5, 3.5)]
        for x, y in spots:
            arena.add_bot()
            bot = g.players[max(g.players)]
            bot.x, bot.y = x, y
        g.players[1].x, g.players[1].y = 2.5, 4.5
        seen = arena.visible_to(0, 5.0)
        self.assertEqual(len(seen), proto.MAX_ENTITIES)
        dists = [((x - 2.5) ** 2 + (y - 2.5) ** 2) for _, x, y in seen]
        self.assertEqual(dists, sorted(dists))              # nearest first
        for p in g.players.values():
            if p is not a:
                p.alive = False
        self.assertEqual(arena.visible_to(0, 5.0), [])

    def test_world_packet_only_carries_visible_enemies(self):
        arena, links = playing_arena()
        a, b = arena.game.players[0], arena.game.players[1]
        a.x, a.y, b.x, b.y = 2.5, 8.5, 12.5, 8.5
        arena.step(1.0)
        worlds, _ = drain(links[0])
        self.assertEqual(worlds[-1]["entities"], [])
        self.assertEqual(worlds[-1]["game_state"], proto.GAME_PLAYING)
        self.assertEqual(worlds[-1]["health"], 2)


class PacketHandlingTests(unittest.TestCase):
    def test_spoofed_and_garbage_packets_are_rejected(self):
        arena, links = make_arena()
        links[0].send_to_server(state_bytes(1, 5.5, 5.5, ready=True))   # watch 0 claims to be player 1
        links[0].send_to_server(b"\x01\x02")
        arena.step(0.05)
        self.assertEqual(arena.stats[0]["bad"], 2)
        self.assertEqual(arena.stats[0]["packets"], 0)
        self.assertFalse(arena.game.players[1].ready)

    def test_lost_packets_are_counted_from_sequence_gaps(self):
        arena, links = make_arena()
        for seq in (254, 255, 0, 1, 5, 5):                              # 2,3,4 lost; last one is a duplicate
            links[0].send_to_server(state_bytes(0, 2.5, 2.5, seq=seq))
        arena.step(0.05)
        self.assertEqual(arena.stats[0]["lost"], 3)

    def test_silent_watch_aborts_the_match(self):
        arena, links = make_arena()
        t = 0.0
        while t < 1.0:                                                  # both ready up and the match starts
            t += 0.05
            for pid in (0, 1):
                links[pid].send_to_server(state_bytes(pid, *arena.game.map.spawns[pid], seq=int(t * 20), ready=True))
            arena.step(t)
        self.assertEqual(arena.game.state, "playing")
        while t < 6.0:                                                  # then watch 1 goes silent
            t += 0.05
            links[0].send_to_server(state_bytes(0, *arena.game.map.spawns[0], seq=int(t * 20)))
            arena.step(t)
        self.assertEqual(arena.game.state, "lobby")
        self.assertTrue(any("disconnected" in line for line in arena.game.feed))
        self.assertEqual(arena.stats[1]["disconnects"], 1)

    def test_rematch_returns_to_the_lobby(self):
        arena, _ = playing_arena(rematch=True)
        arena.game.end_match(arena.game.players[0])
        arena.step(1.0)
        self.assertEqual(arena.game.state, "over")
        arena.step(1.0 + ArenaServer.REMATCH_S + 0.1)
        self.assertEqual(arena.game.state, "lobby")

    def test_run_loop_steps_in_real_time(self):
        arena, _ = make_arena()
        asyncio.run(arena.run(0.3))
        self.assertGreater(arena.game.now, 0.0)
        self.assertIsNotNone(arena._last)


class EventTests(unittest.TestCase):
    def test_kill_and_match_end_events_reach_both_watches(self):
        arena, links = playing_arena(kills=1)
        g = arena.game
        a, b = g.players[0], g.players[1]
        a.x, a.y, b.x, b.y, a.angle = 2.5, 2.5, 5.5, 2.5, 0.0
        g.now = 1.0
        self.assertTrue(g.try_hit(0, 1, 1.0))
        self.assertTrue(g.try_hit(0, 1, 1.5))
        arena.step(2.0)
        for pid in (0, 1):
            _, events = drain(links[pid])
            self.assertIn((proto.EVT_KILL, 0, 1), events)
            self.assertIn((proto.EVT_MATCH_END, 0, 0), events)
            self.assertIn((proto.EVT_MATCH_START, 0, 0), events)

    def test_hit_flags_are_on_briefly(self):
        arena, links = playing_arena()
        g = arena.game
        a, b = g.players[0], g.players[1]
        a.x, a.y, b.x, b.y, a.angle = 2.5, 2.5, 5.5, 2.5, 0.0
        self.assertTrue(g.try_hit(0, 1, 1.0))
        arena.step(1.05)
        wa, _ = drain(links[0])
        wb, _ = drain(links[1])
        self.assertTrue(wa[-1]["flags"] & proto.WORLD_I_HIT)
        self.assertTrue(wb[-1]["flags"] & proto.WORLD_HIT_ME)
        self.assertEqual(wb[-1]["health"], 1)
        arena.step(1.6)
        wa, _ = drain(links[0])
        self.assertEqual(wa[-1]["flags"], 0)

    def test_respawn_is_repeated_until_the_watch_arrives(self):
        arena, links = playing_arena()
        g = arena.game
        a, b = g.players[0], g.players[1]
        a.x, a.y = 2.5, 2.5
        b.x, b.y = 8.5, 8.5
        b.alive, b.respawn_at = False, 1.0
        arena.step(1.0)                                               # server respawns B far from A
        self.assertTrue(b.alive and b.awaiting_spawn)
        spawn = (b.x, b.y)
        self.assertEqual(spawn, (13.5, 13.5))
        links[1].send_to_server(state_bytes(1, 8.5, 8.5, seq=1))      # stale position from before the respawn
        arena.step(1.05)
        self.assertEqual((b.x, b.y), spawn)                           # ignored
        _, events = drain(links[1])
        respawns = [e for e in events if e[0] == proto.EVT_RESPAWN]
        self.assertGreaterEqual(len(respawns), 2)                     # repeated every tick
        self.assertEqual((respawns[0][1], respawns[0][2]), (proto.pos_to_byte(13.5), proto.pos_to_byte(13.5)))
        links[1].send_to_server(state_bytes(1, 13.5, 13.5, seq=2))    # the watch has moved to its spawn
        arena.step(1.1)
        self.assertFalse(b.awaiting_spawn)
        drain(links[1])
        arena.step(1.15)
        _, events = drain(links[1])
        self.assertFalse([e for e in events if e[0] == proto.EVT_RESPAWN])    # stops once arrived

    def test_watch_that_misses_the_respawn_is_trusted_after_a_timeout(self):
        arena, links = playing_arena()
        g = arena.game
        b = g.players[1]
        g.players[0].x, g.players[0].y = 2.5, 2.5
        b.alive, b.respawn_at = False, 1.0
        arena.step(1.0)
        links[1].send_to_server(state_bytes(1, 8.5, 8.5, seq=1))
        arena.step(1.0 + Game.SPAWN_CONFIRM_S + 0.1)
        links[1].send_to_server(state_bytes(1, 8.5, 8.5, seq=2))
        arena.step(1.0 + Game.SPAWN_CONFIRM_S + 0.15)
        self.assertEqual((b.x, b.y), (8.5, 8.5))


class FakeWatchTests(unittest.TestCase):
    def test_watch_reads_world_and_obeys_respawn(self):
        link = Link()
        w = FakeWatch(0, link)
        link.send_to_watch(proto.build_world(proto.GAME_PLAYING, 2, 0, [(1, 5.5, 2.5)]))
        link.send_to_watch(proto.build_event(proto.EVT_RESPAWN, proto.pos_to_byte(13.5), proto.pos_to_byte(2.5)))
        link.send_to_watch(proto.build_event(proto.EVT_KILL, 1, 0))
        link.send_to_watch(b"\xC2\x01")                               # damaged packet: ignored, no crash
        w.poll()
        self.assertTrue(w.playing)
        self.assertEqual(w.entities[0][0], 1)
        self.assertAlmostEqual(w.x, 13.5, delta=0.1)
        self.assertEqual(w.log, [(proto.EVT_KILL, 1, 0)])

    def test_crosshair_picks_nearest_visible_target_in_line(self):
        w = FakeWatch(0, Link())
        w.x, w.y, w.angle = 2.5, 2.5, 0.0
        w.entities = [(1, 5.5, 2.5), (2, 4.5, 2.5), (3, 4.5, 4.5)]
        self.assertEqual(w.crosshair_target(), 2)                     # nearest of the two in line
        w.angle = 1.5
        self.assertEqual(w.crosshair_target(), proto.NO_TARGET)
        w.x, w.y, w.angle, w.entities = 5.5, 6.5, 0.0, [(1, 7.5, 6.5)]
        self.assertEqual(w.crosshair_target(), proto.NO_TARGET)       # a wall is in the way

    def test_watch_sends_state_packets_the_server_can_read(self):
        link = Link()
        w = FakeWatch(1, link)
        w.ready, w.game_state, w.health = True, proto.GAME_PLAYING, 2
        w.entities = []
        w.send_state(1.0)
        st = proto.parse_state(link.to_server.popleft())
        self.assertEqual((st.player_id, st.ready, st.fired), (1, True, True))   # auto-fire on the first tick
        w.send_state(1.05)
        self.assertEqual(proto.parse_state(link.to_server.popleft()).seq, 1)

    def test_keyboard_movement_is_rate_limited_and_stops_at_walls(self):
        w = KeyboardWatch(0, Link())
        w.game_state, w.health = proto.GAME_PLAYING, 2
        w.x, w.y, w.angle = 8.5, 8.5, 0.0
        for _ in range(30):
            w.press("w")                                              # key-repeat burst
        last, biggest = (w.x, w.y), 0.0
        for i in range(40):
            w.think(0.05, i * 0.05)
            biggest = max(biggest, abs(w.x - last[0]) + abs(w.y - last[1]))
            last = (w.x, w.y)
        self.assertLessEqual(biggest, w.SPEED * 0.05 + 1e-9)          # never faster than walking speed
        self.assertLessEqual(w.x - 8.5, w.BUDGET + 1e-9)              # queued movement is capped

        w.x, w.y, w.angle = 1.5, 1.5, 3.14159
        for _ in range(10):
            w.press("w")
        for i in range(60):
            w.think(0.05, i * 0.05)
        self.assertGreaterEqual(w.x, 1.2 - 1e-6)                      # did not walk into the wall

    def test_keyboard_keys(self):
        w = KeyboardWatch(0, Link())
        self.assertFalse(w.ready)
        self.assertFalse(w.press("r"))
        self.assertTrue(w.ready)
        w.press("R")
        self.assertFalse(w.ready)
        self.assertTrue(w.press("x"))
        w.game_state, w.health = proto.GAME_PLAYING, 2
        a0 = w.angle
        w.press("d")
        w.think(0.05, 0.0)
        self.assertGreater(w.angle, a0)

    def test_keyboard_player_must_ready_up_again_after_a_match(self):
        link = Link()
        w = KeyboardWatch(0, link)
        w.press("r")
        link.send_to_watch(proto.build_world(proto.GAME_PLAYING, 2, 0, []))
        w.poll()
        self.assertTrue(w.ready)                                      # still ready during the match
        link.send_to_watch(proto.build_world(proto.GAME_ROUND_OVER, 2, 0, []))
        link.send_to_watch(proto.build_world(proto.GAME_LOBBY, 2, 0, []))
        w.poll()
        self.assertFalse(w.ready)                                     # back in the lobby: not ready

    def test_auto_watch_readies_up_and_wanders(self):
        w = AutoWatch(0, Link(), rng=random.Random(1))
        w.think(0.05, 0.0)
        self.assertTrue(w.ready)
        w.game_state, w.health = proto.GAME_PLAYING, 2
        start = (w.x, w.y)
        for i in range(200):
            w.think(0.05, i * 0.05)
        self.assertNotEqual((w.x, w.y), start)


class RobustnessTests(unittest.TestCase):
    def test_hostile_packets_never_break_the_server(self):
        import struct
        rng = random.Random(7)
        g = Game(mode=FreeForAll(3), rng=random.Random(1), match_seconds=30)
        arena = ArenaServer(g, rematch=True)
        links = {0: Link(), 1: Link()}
        for pid in (0, 1):
            arena.add_watch(pid, f"P{pid}", links[pid])
        arena.add_bot()
        t = 0.0
        for i in range(3000):
            t += 0.05
            for pid in (0, 1):
                for _ in range(rng.randint(0, 3)):
                    if rng.random() < 0.4:
                        data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 24)))
                    else:                                             # valid layout, random content
                        data = struct.pack("<BBBhhBBB", 0xC1, pid, rng.randrange(256),
                                           rng.randint(-32768, 32767), rng.randint(-32768, 32767),
                                           rng.randrange(256), rng.randrange(256), rng.randrange(256))
                    links[pid].send_to_server(data)
            arena.step(t)
            for pid in (0, 1):
                links[pid].to_watch.clear()
            for p in g.players.values():
                self.assertTrue(0 <= p.hp <= g.MAX_HP)
                self.assertFalse(g.map.is_wall(p.x, p.y), (i, p.id, p.x, p.y))
                self.assertLessEqual(p.hits, p.shots)
            self.assertGreaterEqual(g.time_left, 0.0)

    def test_consecutive_matches_keep_watch_and_server_in_step(self):
        g = Game(mode=FreeForAll(2), rng=random.Random(3), match_seconds=60)
        arena = ArenaServer(g, rematch=True)
        watches = []
        for pid in (0, 1):
            link = Link()
            arena.add_watch(pid, f"P{pid}", link)
            watches.append(AutoWatch(pid, link, g.map, random.Random(pid)))
        t, starts, prev = 0.0, 0, g.state
        for _ in range(int(150 / 0.05)):
            t += 0.05
            for w in watches:
                w.poll()
                w.think(0.05, t)
                w.send_state(t)
            arena.step(t)
            if g.state == "playing":
                for w in watches:
                    p = g.players[w.pid]
                    if p.alive and not p.awaiting_spawn:
                        self.assertLess(abs(w.x - p.x) + abs(w.y - p.y), 1.0)
            starts += prev != "playing" and g.state == "playing"
            prev = g.state
        self.assertGreaterEqual(starts, 2)                            # at least two matches back to back
        self.assertEqual([s["disconnects"] for s in arena.stats.values()], [0, 0])


class RenderTests(unittest.TestCase):
    def test_render_shows_map_players_and_state(self):
        g = Game(rng=random.Random(0))
        g.add_player(0, "Player A")
        g.add_player(1, "Player B")
        text = render_ascii(g, you=0)
        lines = text.splitlines()
        self.assertEqual(lines[0], "LOBBY")
        self.assertEqual(len(lines[1]), 16)
        self.assertIn("A", lines[3])                                  # Player A spawns in row 2 of the map
        self.assertIn("<- you", text)
        g.start_match(0.0)
        self.assertIn("PLAYING", render_ascii(g))


if __name__ == "__main__":
    unittest.main()
