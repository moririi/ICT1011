"""rules.py - server-authoritative game rules: lobby/match lifecycle, hits, health, respawns,
scoring and game modes.

The watch only reports WHO was in its crosshair when it fired. The server decides whether that
shot really hit (range, line of sight, aim, fire rate) - see Game.try_hit().

Match lifecycle:  LOBBY --(everyone ready)--> PLAYING --(kills reached / time up)--> OVER
                  OVER --reset_to_lobby()--> LOBBY      (restart() = jump straight back to PLAYING)
"""
import math
import random
from dataclasses import dataclass
from typing import Optional

from . import protocol
from .world import GameMap

LOBBY, PLAYING, OVER = "lobby", "playing", "over"


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
    ready: bool = False         # lobby: bots are always ready, watches set it with the ready flag
    respawn_at: float = 0.0
    last_shot: float = -99.0
    last_state_t: Optional[float] = None   # time of the last accepted position (None after a spawn)
    spawn_idx: int = 0          # which spawn point this player starts on (join order)

    @property
    def accuracy(self):
        return self.hits / self.shots if self.shots else 0.0


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
    MAX_SPEED = 4.0          # squares/second a position update may imply (the watch walks at 2.5)
    SPEED_SLACK = 0.5        # extra squares allowed for rounding / jitter

    def __init__(self, game_map=None, mode=None, rng=None, match_seconds=180.0, min_players=2):
        self.map = game_map or GameMap()
        self.mode = mode or FreeForAll()
        self.rng = rng or random.Random()
        self.match_seconds = match_seconds   # 0 = no time limit
        self.min_players = min_players
        self.players = {}
        self.feed = []           # human-readable events, newest last
        self.now = 0.0
        self.state = LOBBY
        self.paused = False
        self.winner = None       # Player who won, or None (not finished yet, or a draw)
        self.result = ""         # "" while running, then "win" or "draw"
        self.time_left = match_seconds

    @property
    def active(self):
        """True while a match is being played (not lobby, not over, not paused)."""
        return self.state == PLAYING and not self.paused

    # ---- setup -------------------------------------------------------
    def add_player(self, pid, name, is_bot=False):
        p = Player(pid, name, is_bot, ready=is_bot, spawn_idx=len(self.players))
        self.players[pid] = p
        self.spawn(p)
        return p

    def spawn(self, p):
        """Put a player on their own starting spawn point at full health."""
        p.x, p.y = self.map.spawns[p.spawn_idx % len(self.map.spawns)]
        p.hp, p.alive, p.last_state_t = self.MAX_HP, True, None

    def respawn(self, p):
        """Respawn on the spawn point farthest from every living enemy (stops spawn camping)."""
        others = [q for q in self.players.values() if q is not p and q.alive]

        def clearance(i):
            sx, sy = self.map.spawns[i]
            return min((math.hypot(sx - q.x, sy - q.y) for q in others), default=0.0)

        best = max(range(len(self.map.spawns)), key=lambda i: (clearance(i), -i))
        p.x, p.y = self.map.spawns[best]
        p.hp, p.alive, p.last_state_t = self.MAX_HP, True, None

    # ---- lifecycle ---------------------------------------------------
    def start_match(self, now, label="match started"):
        self.now = now
        for p in self.players.values():
            p.kills = p.deaths = p.shots = p.hits = 0
            p.last_shot = -99.0
            self.spawn(p)
        self.state, self.paused = PLAYING, False
        self.winner, self.result = None, ""
        self.time_left = self.match_seconds
        self.feed.append(label)

    def restart(self):
        """Referee restart: scores reset and the match starts again immediately."""
        self.start_match(self.now, "match restarted")

    def end_match(self, winner):
        self.state = OVER
        self.winner = winner
        self.result = "win" if winner else "draw"
        self.feed.append(f"{winner.name} wins the match" if winner else "match drawn")

    def reset_to_lobby(self):
        self.state, self.paused = LOBBY, False
        self.winner, self.result = None, ""
        for p in self.players.values():
            p.ready = p.is_bot

    @staticmethod
    def _rank_key(p):
        return (p.kills, -p.deaths, p.accuracy)   # more kills, then fewer deaths, then better aim

    def _time_up(self):
        ranked = sorted(self.players.values(), key=self._rank_key, reverse=True)
        if len(ranked) > 1 and self._rank_key(ranked[0]) == self._rank_key(ranked[1]):
            self.end_match(None)                  # still level: a draw
        else:
            self.end_match(ranked[0] if ranked else None)

    # ---- input from a watch -----------------------------------------
    def apply_state(self, st, now):
        """Apply a parsed protocol.State from a watch. Returns True if the shot hit."""
        p = self.players.get(st.player_id)
        if p is None:
            return False
        if self.state == LOBBY:
            p.ready = st.ready
            return False
        if not self.active or not p.alive:
            return False
        if not self.map.is_wall(st.x, st.y) and self._plausible(p, st.x, st.y, now):
            p.x, p.y, p.last_state_t = st.x, st.y, now
        p.angle = st.angle
        if st.fired:
            if st.target_id != protocol.NO_TARGET:
                return self.try_hit(p.id, st.target_id, now)
            self.register_miss(p.id, now)         # fired with nobody in the crosshair
        return False

    def _plausible(self, p, x, y, now):
        """Reject a position that would need the player to move impossibly fast (teleporting)."""
        if p.last_state_t is None:                # first packet after a spawn: the watch resets
            return True
        allowed = self.MAX_SPEED * (now - p.last_state_t) + self.SPEED_SLACK
        return math.hypot(x - p.x, y - p.y) <= allowed

    # ---- shooting ----------------------------------------------------
    def _start_shot(self, shooter_id, now):
        """Common checks for any shot. Returns the shooter, or None if the shot is not allowed."""
        s = self.players.get(shooter_id)
        if not self.active or s is None or not s.alive:
            return None
        if now - s.last_shot < self.FIRE_INTERVAL - 1e-9:
            return None                                    # firing too fast
        s.last_shot = now
        s.shots += 1
        return s

    def register_miss(self, shooter_id, now):
        """A shot fired at nobody still counts as a shot (keeps accuracy honest)."""
        self._start_shot(shooter_id, now)

    def try_hit(self, shooter_id, target_id, now, accuracy=100):
        """The core rule: did this shot hit? Range, line of sight, aim, then bot accuracy."""
        t = self.players.get(target_id)
        if t is None or target_id == shooter_id or not t.alive:
            return False
        s = self._start_shot(shooter_id, now)
        if s is None:
            return False
        dx, dy = t.x - s.x, t.y - s.y
        if math.hypot(dx, dy) > self.RANGE:
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
        dt = min(max(now - self.now, 0.0), 0.25)   # a stall must not eat the match clock
        self.now = now
        if self.state == LOBBY:
            if len(self.players) >= self.min_players and all(p.ready for p in self.players.values()):
                self.start_match(now)
            return
        if not self.active:
            return
        for p in self.players.values():
            if not p.alive and now >= p.respawn_at:
                self.respawn(p)
        w = self.mode.winner(self)
        if w is not None:
            self.end_match(w)
            return
        if self.match_seconds > 0:
            self.time_left = max(0.0, self.time_left - dt)
            if self.time_left <= 0:
                self._time_up()

    # ---- output ------------------------------------------------------
    def protocol_state(self):
        """The gameState byte for WORLD packets (protocol.GAME_*)."""
        if self.state == LOBBY:
            return protocol.GAME_LOBBY
        if self.state == OVER:
            return protocol.GAME_ROUND_OVER
        return protocol.GAME_PAUSED if self.paused else protocol.GAME_PLAYING

    def snapshot(self):
        """JSON for the web app (docs/interfaces.md, WebSocket "world")."""
        def row(p):
            return {"id": p.id, "name": p.name, "x": round(p.x, 2), "y": round(p.y, 2),
                    "angle": round(p.angle, 2), "hp": p.hp if p.alive else 0, "score": p.kills}
        humans = [row(p) for p in self.players.values() if not p.is_bot]
        bots = [{k: r[k] for k in ("id", "x", "y", "hp")}
                for r in (row(p) for p in self.players.values() if p.is_bot)]
        return {"type": "world", "t": int(self.now * 1000), "state": self.state,
                "paused": self.paused, "time_left": round(self.time_left, 1),
                "winner": self.winner.name if self.winner else None, "result": self.result,
                "players": humans, "bots": bots, "powerups": [], "feed": self.feed[-5:]}
