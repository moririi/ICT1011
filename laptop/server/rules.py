"""rules.py - server-authoritative game rules: hits, health, respawns, scoring and game modes.

The watch only reports WHO was in its crosshair when it fired. The server decides whether that
shot really hit (range, line of sight, aim, fire rate) - see Game.try_hit().
"""
import math
import random
from dataclasses import dataclass

from .world import GameMap


@dataclass
class Player:
    id: int
    name: str
    is_bot: bool = False
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0          # radians, 0 = facing +x (same as the watch)
    hp: int = 2
    kills: int = 0
    deaths: int = 0
    shots: int = 0
    hits: int = 0
    alive: bool = True
    respawn_at: float = 0.0
    last_shot: float = -99.0
    spawn_idx: int = 0          # which spawn point this player uses (join order)


class FreeForAll:
    """Every player for themselves; first to kills_to_win kills wins."""
    name = "ffa"

    def __init__(self, kills_to_win=5):
        self.kills_to_win = kills_to_win

    def on_kill(self, game, killer, victim):
        killer.kills += 1

    def winner(self, game):
        best = max(game.players.values(), key=lambda p: p.kills, default=None)
        if best is not None and best.kills >= self.kills_to_win:
            return best
        return None


def wrap_angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Game:
    MAX_HP = 2
    FIRE_INTERVAL = 0.4      # seconds between shots (same as the watch gun)
    RANGE = 12.0             # squares
    AIM_TOLERANCE = 0.35     # radians the shooter may be off the target and still hit
    RESPAWN_S = 2.0

    def __init__(self, game_map=None, mode=None, rng=None):
        self.map = game_map or GameMap()
        self.mode = mode or FreeForAll()
        self.rng = rng or random.Random()
        self.players = {}
        self.feed = []           # human-readable events, newest last
        self.now = 0.0
        self.paused = False
        self.winner = None       # Player once the match is decided

    # ---- setup -------------------------------------------------------
    def add_player(self, pid, name, is_bot=False):
        p = Player(pid, name, is_bot, spawn_idx=len(self.players))
        self.players[pid] = p
        self.spawn(p)
        return p

    def spawn(self, p):
        sx, sy = self.map.spawns[p.spawn_idx % len(self.map.spawns)]
        p.x, p.y, p.hp, p.alive = sx, sy, self.MAX_HP, True

    def restart(self):
        for p in self.players.values():
            p.kills = p.deaths = p.shots = p.hits = 0
            self.spawn(p)
        self.winner = None
        self.feed.append("match restarted")

    # ---- input from a watch -----------------------------------------
    def apply_state(self, st, now):
        """Apply a parsed protocol.State from a watch."""
        p = self.players.get(st.player_id)
        if p is None or not p.alive or self.paused or self.winner:
            return False
        if not self.map.is_wall(st.x, st.y):     # ignore impossible positions
            p.x, p.y = st.x, st.y
        p.angle = st.angle
        if st.fired and st.target_id != 0xFF:
            return self.try_hit(p.id, st.target_id, now)
        return False

    # ---- the core rule: did this shot hit? ---------------------------
    def try_hit(self, shooter_id, target_id, now, accuracy=100):
        if self.paused or self.winner:
            return False
        s = self.players.get(shooter_id)
        t = self.players.get(target_id)
        if s is None or t is None or s is t or not s.alive or not t.alive:
            return False
        if now - s.last_shot < self.FIRE_INTERVAL - 1e-9:
            return False                                   # firing too fast
        s.last_shot = now
        s.shots += 1
        dx, dy = t.x - s.x, t.y - s.y
        dist = math.hypot(dx, dy)
        if dist > self.RANGE:
            return False
        if not self.map.line_of_sight(s.x, s.y, t.x, t.y):
            return False
        if abs(wrap_angle(math.atan2(dy, dx) - s.angle)) > self.AIM_TOLERANCE:
            return False
        if self.rng.random() * 100 >= accuracy:
            return False                                   # bots can miss
        s.hits += 1
        t.hp -= 1
        if t.hp <= 0:
            t.alive = False
            t.deaths += 1
            t.respawn_at = now + self.RESPAWN_S
            self.mode.on_kill(self, s, t)
            self.feed.append(f"{s.name} eliminated {t.name}")
        else:
            self.feed.append(f"{s.name} hit {t.name}")
        return True

    # ---- time --------------------------------------------------------
    def tick(self, now):
        self.now = now
        if self.paused or self.winner:
            return
        for p in self.players.values():
            if not p.alive and now >= p.respawn_at:
                self.spawn(p)
        w = self.mode.winner(self)
        if w is not None:
            self.winner = w
            self.feed.append(f"{w.name} wins the match")

    # ---- output for the web app (docs/interfaces.md, WebSocket "world") ----
    def snapshot(self):
        def row(p):
            return {"id": p.id, "name": p.name, "x": round(p.x, 2), "y": round(p.y, 2),
                    "angle": round(p.angle, 2), "hp": p.hp if p.alive else 0, "score": p.kills}
        humans = [row(p) for p in self.players.values() if not p.is_bot]
        bots = [{k: r[k] for k in ("id", "x", "y", "hp")}
                for r in (row(p) for p in self.players.values() if p.is_bot)]
        return {"type": "world", "t": int(self.now * 1000), "players": humans, "bots": bots,
                "powerups": [], "feed": self.feed[-5:]}
