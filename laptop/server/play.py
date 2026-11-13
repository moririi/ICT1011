"""play.py - play a match on the laptop with the keyboard (you = Player A) against a fake watch.

    cd laptop
    python -m server.play                 # you vs a computer-controlled fake watch
    python -m server.play --bots 1        # ... plus a server bot

Keys:  W / S walk   Q / E strafe   A / D turn   R ready (do this first!)   X quit
Your gun fires by itself, like on the watch. The map is a top-down spectator view: you are 'A'
(Player B is 'B', bots are 1, 2, ...). The real watch screen would only show what you can see.
Windows only (uses msvcrt for key presses).
"""
import argparse
import os
import random
import time

from .arena import TICK_HZ, ArenaServer, Link
from .fakewatch import AutoWatch, KeyboardWatch
from .rules import FreeForAll, Game, LOBBY, OVER


def _letter(p):
    return chr(ord("A") + p.id) if p.id < 10 else str(p.id - 9)


def render_ascii(game, you=None):
    """Top-down text picture of the match: map, players, clock, scores and recent events."""
    rows = [["#" if cell else "." for cell in row] for row in game.map.grid]
    for p in game.players.values():
        cx = max(0, min(game.map.w - 1, int(p.x)))
        cy = max(0, min(game.map.h - 1, int(p.y)))
        rows[cy][cx] = _letter(p) if p.alive else "x"
    lines = ["".join(r) for r in rows]
    head = f"{game.state.upper()}"
    if game.paused:
        head += " (paused)"
    if game.state == "playing":
        head += f"   time left {game.time_left:4.0f}s"
    elif game.state == OVER:
        head += f"   {game.winner.name + ' wins' if game.winner else 'draw'}"
    lines.insert(0, head)
    lines.append("")
    for p in game.players.values():
        mark = " <- you" if p.id == you else ""
        lines.append(f"{_letter(p)} {p.name:9} kills {p.kills}  deaths {p.deaths}  hp {p.hp if p.alive else 0}{mark}")
    lines.append("")
    lines.extend(game.feed[-4:])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Play against a fake watch with the keyboard")
    ap.add_argument("--bots", type=int, default=0)
    ap.add_argument("--kills", type=int, default=5)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    try:
        import msvcrt
    except ImportError:
        print("play.py needs Windows (msvcrt). Use `python -m server.sim` instead.")
        return 1
    os.system("")                                  # turns on ANSI colour/cursor codes in the Windows console

    rng = random.Random(a.seed)
    game = Game(mode=FreeForAll(a.kills), rng=rng)
    arena = ArenaServer(game, rematch=True)
    human_link, ai_link = Link(), Link()
    arena.add_watch(0, "Player A", human_link)
    arena.add_watch(1, "Player B", ai_link)
    for _ in range(a.bots):
        arena.add_bot()
    human = KeyboardWatch(0, human_link, game.map)
    ai = AutoWatch(1, ai_link, game.map, random.Random(a.seed))

    dt, start, nxt, last_draw = 1.0 / TICK_HZ, time.monotonic(), time.monotonic(), 0.0
    print("\x1b[2J", end="")
    while True:
        now = time.monotonic() - start
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):             # arrow / function keys send two characters
                msvcrt.getwch()
                continue
            if ch == "\x03" or human.press(ch):          # Ctrl+C arrives as a plain character here
                return 0
        for w in (human, ai):
            w.poll()
            w.think(dt, now)
            w.send_state(now)
        arena.step(now)
        if now - last_draw >= 0.1:
            last_draw = now
            extra = "  (press R to ready up)" if game.state == LOBBY and not human.ready else ""
            print("\x1b[H" + render_ascii(game, you=0) + extra + "\x1b[J", flush=True)
        nxt += dt
        time.sleep(max(0.0, nxt - time.monotonic()))


if __name__ == "__main__":
    raise SystemExit(main())
