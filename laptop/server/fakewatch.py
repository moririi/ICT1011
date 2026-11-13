"""fakewatch.py - software stand-ins for the TinyBoy watches, so the whole server can be tested
with no hardware.

A FakeWatch behaves like the watch firmware: it moves ITSELF (with wall collision, like tryMove in
tinyboy.ino), reports its position/angle/fire state in STATE packets, and only knows about the
world through the WORLD / EVENT packets it receives. Two controllers are provided:

  AutoWatch      - a simple computer player (wanders, turns to face and chases what it can see)
  KeyboardWatch  - you, pressing keys (see play.py)
"""
import math
import random
import struct

from . import protocol as proto
from .rules import wrap_angle
from .world import GameMap, cell_of, centre_of


class FakeWatch:
    SPEED = 2.5              # squares per second (same as the watch)
    TURN_SPEED = 2.5         # radians per second
    FIRE_INTERVAL = 0.4      # the watch gun fires by itself every 400 ms
    FIRE_FLAG_S = 0.1        # the fired flag stays on for 100 ms (docs/interfaces.md)
    CROSSHAIR_TOL = 0.12     # radians: how close to the centre of the screen counts as "in the crosshair"
    MARGIN = 0.2             # keep this far from walls

    def __init__(self, pid, link, game_map=None):
        self.pid = pid
        self.link = link
        self.map = game_map or GameMap()
        self.x, self.y = self.map.spawns[pid % len(self.map.spawns)]
        self.angle = 0.0
        self.seq = 0
        self.ready = False
        self.game_state = proto.GAME_LOBBY
        self.health = 2
        self.flags = 0
        self.entities = []       # what the last WORLD packet showed: (id, x, y)
        self.log = []            # EVENTs received: (kind, a, b); RESPAWN is not logged (it repeats)
        self.last_fire = -99.0

    @property
    def playing(self):
        return self.game_state == proto.GAME_PLAYING and self.health > 0

    # ---- receiving ---------------------------------------------------
    def poll(self):
        """Read everything the server sent since the last call."""
        while self.link.to_watch:
            data = self.link.to_watch.popleft()
            try:
                if data[0] == proto.MAGIC_WORLD:
                    w = proto.parse_world(data)
                    if w:
                        self.game_state, self.health = w["game_state"], w["health"]
                        self.flags, self.entities = w["flags"], w["entities"]
                elif data[0] == proto.MAGIC_EVENT:
                    ev = proto.parse_event(data)
                    if ev and ev[0] == proto.EVT_RESPAWN:
                        self.x, self.y = ev[1] / 16.0 + 1 / 32, ev[2] / 16.0 + 1 / 32   # server moved us
                    elif ev:
                        self.log.append(ev)
            except (struct.error, IndexError):
                pass                                    # a damaged packet is simply ignored

    # ---- movement (same collision rule as tryMove in the firmware) ----
    def turn(self, a):
        self.angle = wrap_angle(self.angle + a)

    def try_move(self, dx, dy):
        cx = self.x + dx + (self.MARGIN if dx > 0 else -self.MARGIN)
        cy = self.y + dy + (self.MARGIN if dy > 0 else -self.MARGIN)
        if not self.map.is_wall(cx, self.y):
            self.x += dx
        if not self.map.is_wall(self.x, cy):
            self.y += dy

    def walk(self, forward, strafe, dt):
        """forward / strafe are -1..1."""
        d = self.SPEED * dt
        fx, fy = math.cos(self.angle), math.sin(self.angle)
        self.try_move((fx * forward - fy * strafe) * d, (fy * forward + fx * strafe) * d)

    # ---- shooting ----------------------------------------------------
    def crosshair_target(self):
        """Id of the nearest visible player in the crosshair with a clear view, else NO_TARGET."""
        best = None
        for eid, x, y in self.entities:
            dx, dy = x - self.x, y - self.y
            if abs(wrap_angle(math.atan2(dy, dx) - self.angle)) > self.CROSSHAIR_TOL:
                continue
            if not self.map.line_of_sight(self.x, self.y, x, y):
                continue
            d = math.hypot(dx, dy)
            if best is None or d < best[0]:
                best = (d, eid)
        return best[1] if best else proto.NO_TARGET

    # ---- sending -----------------------------------------------------
    def send_state(self, now):
        if self.playing and now - self.last_fire >= self.FIRE_INTERVAL:
            self.last_fire = now                        # auto-fire
        fired = self.playing and now - self.last_fire < self.FIRE_FLAG_S
        target = self.crosshair_target() if fired else proto.NO_TARGET
        pkt = proto.State(self.pid, self.seq, self.x, self.y, self.angle, fired, self.ready, target)
        self.seq = (self.seq + 1) & 0xFF
        self.link.send_to_server(proto.build_state(pkt))

    def think(self, dt, now):
        """Decide controls for this tick. Overridden by the controllers below."""


class AutoWatch(FakeWatch):
    """A computer-controlled watch: wanders the maze, and chases and faces whatever it can see."""

    def __init__(self, pid, link, game_map=None, rng=None):
        super().__init__(pid, link, game_map)
        self.rng = rng or random.Random(pid)
        self.path = []

    def _face(self, target_angle, dt):
        diff = wrap_angle(target_angle - self.angle)
        step = self.TURN_SPEED * dt
        self.turn(max(-step, min(step, diff)))
        return diff

    def think(self, dt, now):
        self.ready = True
        if not self.playing:
            self.path = []
            return
        if self.entities:                                # an enemy is visible: face it and close in
            _, ex, ey = self.entities[0]
            dx, dy = ex - self.x, ey - self.y
            diff = self._face(math.atan2(dy, dx), dt)
            if math.hypot(dx, dy) > 3.0 and abs(diff) < 1.0:
                self.walk(1.0, 0.0, dt)
            self.path = []
            return
        self._patrol(dt)

    def _patrol(self, dt):
        if not self.path:
            cells = [c for c in self.map.open_cells()
                     if abs(c[0] - self.x) + abs(c[1] - self.y) >= 3]
            goal = self.rng.choice(cells)
            self.path = self.map.find_path(cell_of(self.x, self.y), goal) or []
        if not self.path:
            return
        tx, ty = centre_of(self.path[0])
        dx, dy = tx - self.x, ty - self.y
        if math.hypot(dx, dy) < 0.2:
            self.path.pop(0)
            return
        diff = self._face(math.atan2(dy, dx), dt)
        if abs(diff) < 0.5:
            self.walk(1.0, 0.0, dt)


class KeyboardWatch(FakeWatch):
    """You. Each key press adds a little movement that is then carried out at walking speed, so
    holding a key (fast key repeat) can never move you faster than the real watch could.
    Keys: W/S walk, Q/E strafe, A/D turn, R ready/unready (lobby), X quit."""

    STEP = 0.3               # squares per key press
    TURN = 0.25              # radians per key press
    BUDGET = 1.0             # most movement that can be queued up

    def __init__(self, pid, link, game_map=None):
        super().__init__(pid, link, game_map)
        self._fwd = self._strafe = self._turn = 0.0

    def press(self, key):
        """Handle one key. Returns True if the player asked to quit."""
        k = key.lower()
        cap = lambda v: max(-self.BUDGET, min(self.BUDGET, v))
        if k == "w":
            self._fwd = cap(self._fwd + self.STEP)
        elif k == "s":
            self._fwd = cap(self._fwd - self.STEP)
        elif k == "e":
            self._strafe = cap(self._strafe + self.STEP)
        elif k == "q":
            self._strafe = cap(self._strafe - self.STEP)
        elif k == "d":
            self._turn = cap(self._turn + self.TURN)
        elif k == "a":
            self._turn = cap(self._turn - self.TURN)
        elif k == "r":
            self.ready = not self.ready
        return k == "x"

    def poll(self):
        before = self.game_state
        super().poll()
        if self.game_state == proto.GAME_LOBBY and before != proto.GAME_LOBBY:
            self.ready = False                  # back in the lobby: ready up again for the next match

    @staticmethod
    def _take(budget, limit):
        used = max(-limit, min(limit, budget))
        return used, budget - used

    def think(self, dt, now):
        if not self.playing:
            self._fwd = self._strafe = self._turn = 0.0
            return
        f, self._fwd = self._take(self._fwd, self.SPEED * dt * 0.7)
        s, self._strafe = self._take(self._strafe, self.SPEED * dt * 0.7)
        t, self._turn = self._take(self._turn, self.TURN_SPEED * dt)
        self.turn(t)
        fx, fy = math.cos(self.angle), math.sin(self.angle)
        self.try_move(fx * f - fy * s, fy * f + fx * s)
