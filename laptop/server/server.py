"""server.py - the ARENA game server loop (20 Hz).

For now it runs an all-bot demo match, so the rules and bots can be tested with no watch:

    cd laptop
    python -m server.server --bots 4 --seconds 120 --seed 1
    python -m server.server --bots 4 --realtime          # watch it play out at normal speed

Later: a BLE link to the real watches (protocol.py packets) and a WebSocket for the web app,
both fed from Game.snapshot().
"""
import argparse
import random
import time

from .bots import BotBrain
from .rules import FreeForAll, Game

TICK_HZ = 20


def run_demo(n_bots=4, seconds=120, kills_to_win=5, seed=None, realtime=False, out=print):
    rng = random.Random(seed)
    game = Game(mode=FreeForAll(kills_to_win), rng=rng)
    brains = []
    for i in range(n_bots):
        p = game.add_player(10 + i, f"Bot{i + 1}", is_bot=True)
        p.x, p.y = game.map.spawns[i % len(game.map.spawns)]
        brains.append(BotBrain(game, p))

    dt = 1.0 / TICK_HZ
    shown = 0
    t = 0.0
    while t < seconds and not game.winner:
        t += dt
        for b in brains:
            b.update(dt, t)
        game.tick(t)
        while shown < len(game.feed):
            out(f"[{t:6.1f}s] {game.feed[shown]}")
            shown += 1
        if realtime:
            time.sleep(dt)

    out("--- scoreboard ---")
    for p in sorted(game.players.values(), key=lambda q: -q.kills):
        acc = f"{100 * p.hits // p.shots}%" if p.shots else "-"
        out(f"{p.name:6} kills {p.kills}  deaths {p.deaths}  shots {p.shots}  accuracy {acc}")
    return game


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ARENA game server (bot-only demo for now)")
    ap.add_argument("--bots", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--kills", type=int, default=5, help="kills needed to win")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--realtime", action="store_true")
    a = ap.parse_args()
    run_demo(a.bots, a.seconds, a.kills, a.seed, a.realtime)
