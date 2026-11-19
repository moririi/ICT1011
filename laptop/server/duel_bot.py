"""duel_bot.py - Player B's brain for watch_link.py: the server bot plays the duel against the watch.

The bot is the same BotBrain the ARENA server uses (bots.py): it walks the maze with BFS pathfinding,
turns to face the watch player when it can see them, and shoots when it has line of sight.
It runs inside a small private Game that only holds the two players. That Game is never ticked, so
it never decides kills, respawns or the winner - DuelB (watch_link.py) owns the duel rules.

Shooter decides hits: when the bot fires, Game.try_hit decides whether the shot lands (range, wall
in the way, aim, then the bot's accuracy roll), and the result goes to DuelB.fire().

Difficulty changes the bot's accuracy, reaction time, fire rate and turning speed.
"""
import random

from .bots import BotBrain
from .rules import Game

DIFFICULTY = {
    #          accuracy at point blank, % lost per square, lowest accuracy, reaction, time between shots, turn speed
    "easy":   dict(BASE_ACCURACY=45, FALLOFF=5, MIN_ACCURACY=10, REACTION_S=1.0, FIRE_INTERVAL=0.9, TURN_SPEED=2.0),
    "normal": dict(BASE_ACCURACY=75, FALLOFF=5, MIN_ACCURACY=20, REACTION_S=0.6, FIRE_INTERVAL=0.7, TURN_SPEED=3.0),
    "hard":   dict(BASE_ACCURACY=90, FALLOFF=3, MIN_ACCURACY=40, REACTION_S=0.3, FIRE_INTERVAL=0.5, TURN_SPEED=4.0),
}

WATCH_ID, BOT_ID = 0, 1     # join order = spawn points: watch (2.5, 2.5), bot (13.5, 13.5)


class DuelBot:
    def __init__(self, difficulty="normal", rng=None):
        if difficulty not in DIFFICULTY:
            raise ValueError(f"difficulty must be one of {', '.join(DIFFICULTY)}")
        self.difficulty = difficulty
        self.game = Game(rng=rng or random.Random(), match_seconds=0)
        self.watch = self.game.add_player(WATCH_ID, "Watch")
        self.bot = self.game.add_player(BOT_ID, "LaptopBot", is_bot=True)
        self.game.state = "playing"             # never ticked: no game clock, respawns or winner here
        self.brain = BotBrain(self.game, self.bot)
        for name, value in DIFFICULTY[difficulty].items():
            setattr(self.brain, name, value)

    def reset_round(self, duel, now):
        """New round: both players back on their spawn points; the bot forgets its old path."""
        self.game.now = now
        self.game.spawn(self.watch)
        self.game.spawn(self.bot)
        self.bot.angle = duel.angle
        self.bot.last_shot = -99.0
        duel.x, duel.y = self.bot.x, self.bot.y

    def update(self, duel, dt, now):
        g = self.game
        g.now = now
        if duel.a is not None:                  # where the watch player is right now
            self.watch.x, self.watch.y, self.watch.angle = duel.a.x, duel.a.y, duel.a.angle
        self.watch.hp, self.watch.alive = Game.MAX_HP, True          # DuelB decides the watch player's health
        self.bot.hp = max(1, Game.MAX_HP - duel.hits_taken)          # hurt bot -> it may flee
        shots_before, hits_before = self.bot.shots, self.bot.hits
        self.brain.update(dt, now)
        duel.x, duel.y, duel.angle = self.bot.x, self.bot.y, self.bot.angle
        new_hits = self.bot.hits - hits_before
        for i in range(self.bot.shots - shots_before):
            duel.fire(now, hit=i < new_hits)
