"""bots.py - server-side bots: pathfinding (BFS) + a small state machine.

States: PATROL (wander) -> HUNT (go to where an enemy was last seen) -> ATTACK (see an enemy,
aim and shoot) -> FLEE (low health: run away for a few seconds). The accuracy numbers are the
ones used by the bot on the watch (firmware/tinyboy/tinyboy.ino).
"""
import math

from .rules import wrap_angle
from .world import cell_of, centre_of


class BotBrain:
    SPEED = 1.6               # squares per second
    TURN_SPEED = 3.0          # radians per second
    SIGHT = 10.0              # squares
    REACTION_S = 0.6          # delay between spotting an enemy and the first shot
    FIRE_INTERVAL = 0.7
    AIM_TOLERANCE = 0.15
    BASE_ACCURACY = 75        # percent at point blank
    FALLOFF = 5               # percent lost per square
    MIN_ACCURACY = 20
    FLEE_S = 3.0              # how long it runs when hurt
    FLEE_COOLDOWN_S = 6.0     # after fleeing it fights again for at least this long

    def __init__(self, game, player):
        self.game = game
        self.p = player
        self.state = "patrol"
        self.path = []
        self.goal = None
        self.last_known = None
        self.seen_since = None
        self.flee_until = 0.0
        self.flee_ready_at = 0.0
        self.flee_from = None     # where the enemy was when the bot started running
        self._spawns = player.spawns

    def _reset(self):
        """Forget everything: used after the server moves the bot (respawn, new match)."""
        self.state, self.path, self.goal = "patrol", [], None
        self.last_known = self.seen_since = self.flee_from = None
        self.flee_until = self.flee_ready_at = 0.0

    # ---- perception --------------------------------------------------
    def _visible_enemy(self):
        best, best_d = None, self.SIGHT
        for q in self.game.players.values():
            if q is self.p or not q.alive:
                continue
            d = math.hypot(q.x - self.p.x, q.y - self.p.y)
            if d <= best_d and self.game.map.line_of_sight(self.p.x, self.p.y, q.x, q.y):
                best, best_d = q, d
        return best

    # ---- movement ----------------------------------------------------
    def _goto(self, x, y):
        goal = cell_of(x, y)
        if goal != self.goal or not self.path:
            self.goal = goal
            self.path = self.game.map.find_path(cell_of(self.p.x, self.p.y), goal) or []

    def _follow(self, dt, speed_factor=1.0):
        step = self.SPEED * speed_factor * dt
        while self.path and step > 0:
            tx, ty = centre_of(self.path[0])
            dx, dy = tx - self.p.x, ty - self.p.y
            d = math.hypot(dx, dy)
            if d < 1e-6:
                self.path.pop(0)
                continue
            move = min(step, d)
            self.p.x += dx / d * move
            self.p.y += dy / d * move
            self.p.angle = math.atan2(dy, dx)
            step -= move
            if move >= d - 1e-9:
                self.path.pop(0)

    def _turn_towards(self, target_angle, dt):
        diff = wrap_angle(target_angle - self.p.angle)
        max_turn = self.TURN_SPEED * dt
        self.p.angle = wrap_angle(self.p.angle + max(-max_turn, min(max_turn, diff)))

    # ---- behaviours --------------------------------------------------
    def _patrol(self, dt):
        if not self.path:
            cells = [c for c in self.game.map.open_cells()
                     if abs(c[0] - self.p.x) + abs(c[1] - self.p.y) >= 4]
            if cells:
                c = self.game.rng.choice(cells)
                self._goto(*centre_of(c))
        self._follow(dt)

    def _hunt(self, dt):
        self._goto(*self.last_known)
        self._follow(dt)
        if not self.path:
            self.last_known = None

    def _flee(self, dt):
        fx, fy = self.flee_from
        if not self.path:
            far = max(self.game.map.open_cells(),
                      key=lambda c: math.hypot(c[0] + .5 - fx, c[1] + .5 - fy)
                      + self.game.rng.random())
            self._goto(*centre_of(far))
        self._follow(dt, 1.2)

    def _attack(self, enemy, dt, now):
        dx, dy = enemy.x - self.p.x, enemy.y - self.p.y
        dist = math.hypot(dx, dy)
        target = math.atan2(dy, dx)
        self._turn_towards(target, dt)
        if dist > 5:                                   # close the distance at half speed
            self._goto(enemy.x, enemy.y)
            self._follow(dt, 0.5)
        if self.seen_since is None:
            self.seen_since = now
        aimed = abs(wrap_angle(target - self.p.angle)) < self.AIM_TOLERANCE
        if (aimed and now - self.seen_since >= self.REACTION_S
                and now - self.p.last_shot >= self.FIRE_INTERVAL):
            acc = max(self.MIN_ACCURACY, self.BASE_ACCURACY - dist * self.FALLOFF)
            self.game.try_hit(self.p.id, enemy.id, now, accuracy=acc)

    # ---- one step ----------------------------------------------------
    def update(self, dt, now):
        p = self.p
        if p.spawns != self._spawns:             # the server teleported us: old paths are meaningless
            self._spawns = p.spawns
            self._reset()
        if not p.alive:
            self._reset()
            return
        if not self.game.active:
            return
        enemy = self._visible_enemy()
        fleeing = now < self.flee_until          # a flee runs its full time, even out of sight
        if enemy:
            self.last_known = (enemy.x, enemy.y)
            if p.hp <= 1 and not fleeing and now >= self.flee_ready_at:
                self.flee_until = now + self.FLEE_S
                self.flee_ready_at = self.flee_until + self.FLEE_COOLDOWN_S
                self.path = []
                fleeing = True
            if fleeing:
                self.flee_from = (enemy.x, enemy.y)
        if fleeing:
            self.state = "flee"
        elif enemy:
            self.state = "attack"
        elif self.last_known is not None:
            self.state = "hunt"
        else:
            self.state = "patrol"

        if self.state != "attack":
            self.seen_since = None
        if self.state == "attack":
            self._attack(enemy, dt, now)
        elif self.state == "flee":
            self._flee(dt)
        elif self.state == "hunt":
            self._hunt(dt)
        else:
            self._patrol(dt)
