"""arena.py - the real-time ARENA server: one link per watch, a 20 Hz step, per-player WORLD packets.

A "link" is anything with two queues: `to_server` (STATE packets from the watch) and `to_watch`
(WORLD / EVENT packets for the watch). `Link` below is an in-process stand-in used by the fake
watches; the Bluetooth link (net_ble.py, later) will provide the same two queues, so nothing in
this file changes when real watches arrive.

What each watch is told every tick (docs/interfaces.md, section 3.2):
  * WORLD: game state, my health, hit/scored flags, and ONLY the enemies I can see.
    Dark maze rule: an enemy is visible if it is close AND in line of sight, or if it just fired
    (muzzle flash) AND in line of sight. A watch therefore never learns where hidden players are.
  * EVENT: match start/end, kills, and RESPAWN (the watch must move to the spawn it is given).
    RESPAWN is repeated every tick until the watch has arrived, so one lost packet is harmless.
"""
import asyncio
import math
import random
import time
from collections import deque

from . import protocol as proto
from .bots import BotBrain
from .rules import Game, OVER, PLAYING

TICK_HZ = 20
VISION_RANGE = 7.0       # squares: how far you can see an enemy in the dark
FLASH_S = 0.3            # an enemy that fired this recently is visible (muzzle flash)


class Link:
    """In-process stand-in for the Bluetooth connection to one watch. `loss` drops packets."""

    def __init__(self, loss=0.0, rng=None):
        self.to_server = deque()
        self.to_watch = deque()
        self.loss = loss
        self.rng = rng or random.Random()

    def _delivered(self):
        return self.rng.random() >= self.loss

    def send_to_server(self, data):
        if self._delivered():
            self.to_server.append(data)

    def send_to_watch(self, data):
        if self._delivered():
            self.to_watch.append(data)


class ArenaServer:
    DISCONNECT_S = 3.0   # a watch silent this long during a match aborts the match
    REMATCH_S = 5.0      # after a match, wait this long, then go back to the lobby

    def __init__(self, game=None, rematch=True, rng=None):
        self.game = game or Game(rng=rng)
        self.rematch = rematch
        self.links = {}      # player id -> link
        self.brains = []     # server-side bots
        self.stats = {}      # player id -> packet counters
        self._last = None
        self._over_since = None

    # ---- setup -------------------------------------------------------
    def add_watch(self, pid, name, link):
        self.game.add_player(pid, name)
        self.links[pid] = link
        self.stats[pid] = {"packets": 0, "lost": 0, "bad": 0, "last_seq": None,
                           "last_seen": None, "disconnects": 0}

    def add_bot(self, name=None):
        pid = max([9] + list(self.game.players)) + 1
        p = self.game.add_player(pid, name or f"Bot{pid - 9}", is_bot=True)
        self.brains.append(BotBrain(self.game, p))
        return p

    # ---- one tick ----------------------------------------------------
    def step(self, now):
        dt = 1.0 / TICK_HZ if self._last is None else min(max(now - self._last, 0.0), 0.25)
        self._last = now
        self._receive(now)
        self._check_links(now)
        for brain in self.brains:
            brain.update(dt, now)
        self.game.tick(now)
        self._rematch(now)
        self._send(now)

    def _receive(self, now):
        for pid, link in self.links.items():
            st = self.stats[pid]
            while link.to_server:
                st_pkt = proto.parse_state(link.to_server.popleft())
                if st_pkt is None or st_pkt.player_id != pid:    # garbage, or pretending to be someone else
                    st["bad"] += 1
                    continue
                if st["last_seq"] is not None:
                    gap = (st_pkt.seq - st["last_seq"] - 1) & 0xFF
                    if gap < 128:                                # (a huge gap is a duplicate / reorder)
                        st["lost"] += gap
                st["last_seq"] = st_pkt.seq
                st["packets"] += 1
                st["last_seen"] = now
                self.game.apply_state(st_pkt, now)

    def _check_links(self, now):
        g = self.game
        if g.state != PLAYING:
            return
        for pid in self.links:
            seen = self.stats[pid]["last_seen"]
            if seen is not None and now - seen > self.DISCONNECT_S:
                g.feed.append(f"{g.players[pid].name} disconnected - match aborted")
                self.stats[pid]["disconnects"] += 1
                self.stats[pid]["last_seen"] = None
                g.reset_to_lobby()
                return

    def _rematch(self, now):
        if self.game.state != OVER:
            self._over_since = None
            return
        if self._over_since is None:
            self._over_since = now
        elif self.rematch and now - self._over_since >= self.REMATCH_S:
            self.game.reset_to_lobby()
            self._over_since = None

    # ---- what each watch is told ------------------------------------
    def visible_to(self, pid, now):
        """Enemies this player can see right now, nearest first, as (id, x, y)."""
        g, me = self.game, self.game.players[pid]
        seen = []
        for q in g.players.values():
            if q is me or not q.alive:
                continue
            d = math.hypot(q.x - me.x, q.y - me.y)
            if not g.map.line_of_sight(me.x, me.y, q.x, q.y):
                continue
            if d <= VISION_RANGE or now - q.last_shot <= FLASH_S:
                seen.append((d, q.id, q.x, q.y))
        seen.sort()
        return [(i, x, y) for _, i, x, y in seen[:proto.MAX_ENTITIES]]

    def _world_packet(self, pid, now):
        g, me = self.game, self.game.players[pid]
        flags = (proto.WORLD_HIT_ME if now < me.hurt_until else 0) | \
                (proto.WORLD_I_HIT if now < me.scored_until else 0)
        entities = self.visible_to(pid, now) if g.state == PLAYING else []
        return proto.build_world(g.protocol_state(), me.hp if me.alive else 0, flags, entities)

    def _send(self, now):
        g = self.game
        events = list(g.events)
        g.events.clear()
        for pid, link in self.links.items():
            for ev in events:
                kind = ev[0]
                if kind == "match_start":
                    link.send_to_watch(proto.build_event(proto.EVT_MATCH_START))
                elif kind == "match_end":
                    link.send_to_watch(proto.build_event(proto.EVT_MATCH_END,
                                                         0xFF if ev[1] is None else ev[1]))
                elif kind == "kill":
                    link.send_to_watch(proto.build_event(proto.EVT_KILL, ev[1], ev[2]))
            me = g.players[pid]
            if me.awaiting_spawn and g.state == PLAYING and me.alive:
                link.send_to_watch(proto.build_event(proto.EVT_RESPAWN,
                                                     proto.pos_to_byte(me.x), proto.pos_to_byte(me.y)))
            link.send_to_watch(self._world_packet(pid, now))

    # ---- real-time driver -------------------------------------------
    async def run(self, seconds=None, clock=time.monotonic):
        """Step at 20 Hz in real time (forever if seconds is None)."""
        period = 1.0 / TICK_HZ
        start = nxt = clock()
        while seconds is None or clock() - start < seconds:
            self.step(clock())
            nxt += period
            await asyncio.sleep(max(0.0, nxt - clock()))
