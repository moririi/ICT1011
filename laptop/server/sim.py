"""sim.py - run a whole 2-player match through the real server code, with two fake watches.

Every position, shot and hit travels as the same binary packets a real watch would send, so this
tests the full path (STATE in -> rules -> WORLD / EVENT out) in simulated time (fast).

    cd laptop
    python -m server.sim --seed 1                  # two fake watches
    python -m server.sim --seed 1 --loss 0.1       # 10% of packets lost both ways
    python -m server.sim --seed 1 --bots 1         # plus a server bot
"""
import argparse
import random

from .arena import ArenaServer, Link
from .fakewatch import AutoWatch
from .rules import FreeForAll, Game, OVER

NAMES = {0: "Player A", 1: "Player B"}


def run_sim(seconds=300, seed=0, bots=0, loss=0.0, kills=5, match_seconds=180.0,
            dt=0.05, out=print):
    """Returns (arena, watches). Stops when the match is over or after `seconds`."""
    game = Game(mode=FreeForAll(kills), rng=random.Random(seed), match_seconds=match_seconds)
    arena = ArenaServer(game, rematch=False)
    watches = []
    for pid in (0, 1):
        link = Link(loss, random.Random(seed * 10 + pid))
        arena.add_watch(pid, NAMES[pid], link)
        watches.append(AutoWatch(pid, link, game.map, random.Random(seed * 100 + pid)))
    for _ in range(bots):
        arena.add_bot()

    t, shown = 0.0, 0
    while t < seconds and game.state != OVER:
        t += dt
        for w in watches:
            w.poll()
            w.think(dt, t)
            w.send_state(t)
        arena.step(t)
        while shown < len(game.feed):
            out(f"[{t:6.1f}s] {game.feed[shown]}")
            shown += 1

    out("--- scoreboard ---")
    for p in sorted(game.players.values(), key=lambda q: -q.kills):
        acc = f"{100 * p.hits // p.shots}%" if p.shots else "-"
        out(f"{p.name:9} kills {p.kills}  deaths {p.deaths}  shots {p.shots}  accuracy {acc}")
    out("--- links ---")
    for pid, st in arena.stats.items():
        out(f"{NAMES[pid]}: {st['packets']} packets received, {st['lost']} lost, {st['bad']} bad")
    return arena, watches


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Simulate a 2-player match with two fake watches")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--bots", type=int, default=0)
    ap.add_argument("--loss", type=float, default=0.0, help="fraction of packets lost, e.g. 0.1")
    ap.add_argument("--kills", type=int, default=5)
    ap.add_argument("--time", type=float, default=180.0, help="match time limit in seconds")
    a = ap.parse_args()
    run_sim(a.seconds, a.seed, a.bots, a.loss, a.kills, a.time)
