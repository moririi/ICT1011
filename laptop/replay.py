"""Make an animated GIF replay of a match: a top-down view of the players moving through the maze.

Reads matches.json + positions/match_<id>.json (docs/interfaces.md, 4b)
and saves a GIF. Uses the map and loader from heatmap.py.

    python replay.py --data sample --match 1
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from heatmap import SAMPLES_PER_SECOND, WORLD_MAP, load_match

CELL = 20              # pixels per map square
HEADER = 44            # pixels above the map for the clock and score
FPS = 10               # GIF frames per second
TRAIL_SECONDS = 3      # how much of each player's recent path to draw
END_HOLD_MS = 2500     # how long the last frame stays before the GIF loops

COL_FLOOR = (244, 244, 244)
COL_WALL = (92, 92, 92)
COL_TEXT = (30, 30, 30)
COL_BACKGROUND = (255, 255, 255)
PLAYER_COLOURS = {"A": (28, 126, 214), "B": (224, 49, 49)}  # blue / red, like on the watches


def build_timeline(trail, longest):
    """Returns [(seconds, x, y), ...] for one player.

    Uses the recorded t when it is real. A match rebuilt with `bridge.py --replay` has t close
    to 0 everywhere, so then time comes from the sample order instead (5 per second). A shorter
    trail is lined up with the end, because a player's samples only start once their watch connects.
    """
    expected_end = (len(trail) - 1) / SAMPLES_PER_SECOND
    if trail and trail[-1][2] >= expected_end / 2:
        return [(t, x, y) for x, y, t in trail]
    offset = longest - len(trail)
    return [((i + offset) / SAMPLES_PER_SECOND, x, y) for i, (x, y, _t) in enumerate(trail)]


def round_state(rounds, now):
    """Which round is being played at `now` seconds, and the round score so far."""
    score = {"A": 0, "B": 0}
    start = 0.0
    for r in rounds:
        end = start + r.get("duration_s", 0)
        if now < end:
            return r["round"], score
        if r.get("winner") in score:
            score[r["winner"]] += 1
        start = end
    return (rounds[-1]["round"] if rounds else None), score


def draw_frame(match, timelines, now, font, big_font, final=False):
    width = len(WORLD_MAP[0]) * CELL
    height = HEADER + len(WORLD_MAP) * CELL
    img = Image.new("RGB", (width, height), COL_BACKGROUND)
    draw = ImageDraw.Draw(img)

    # Maze
    for row, cells in enumerate(WORLD_MAP):
        for col, cell in enumerate(cells):
            x0, y0 = col * CELL, HEADER + row * CELL
            draw.rectangle([x0, y0, x0 + CELL - 1, y0 + CELL - 1], fill=COL_WALL if cell else COL_FLOOR)

    def to_px(x, y):
        return x * CELL, HEADER + y * CELL

    for slot, timeline in timelines.items():
        colour = PLAYER_COLOURS.get(slot, (0, 0, 0))
        seen = [p for p in timeline if p[0] <= now]
        if not seen:
            continue  # this player hasn't connected yet
        # Recent path: a faded line
        trail = [to_px(x, y) for t, x, y in seen if t >= now - TRAIL_SECONDS]
        if len(trail) > 1:
            faded = tuple(c + (255 - c) * 3 // 5 for c in colour)
            draw.line(trail, fill=faded, width=4, joint="curve")
        # Current position: a dot with the player's letter
        px, py = to_px(seen[-1][1], seen[-1][2])
        r = CELL * 0.45
        draw.ellipse([px - r, py - r, px + r, py + r], fill=colour, outline=COL_BACKGROUND, width=2)
        draw.text((px, py), slot, fill=COL_BACKGROUND, font=font, anchor="mm")

    # Header: match, clock, round and score
    names = match.get("players", {})
    round_no, score = round_state(match.get("rounds", []), now)
    left = f"Match {match['id']} replay"
    if round_no is not None:
        left += f" · Round {round_no}"
    draw.text((8, 6), left, fill=COL_TEXT, font=font)
    draw.text((width - 8, 6), f"{int(now) // 60}:{int(now) % 60:02d}", fill=COL_TEXT, font=font, anchor="ra")

    if final:
        winner = match.get("winner")
        bottom = f"Winner: {names.get(winner, winner)}" if winner else f"Match {match.get('status', 'over')}"
        final_score = match.get("score", score)
        bottom += f"   {final_score.get('A', 0)} - {final_score.get('B', 0)}"
    else:
        bottom = f"{names.get('A', 'A')} {score['A']} - {score['B']} {names.get('B', 'B')}"
    draw.text((8, 24), bottom, fill=COL_TEXT, font=big_font if final else font)
    return img


def make_replay(match, positions, out_path, speed=6.0):
    """Save the replay as a GIF to out_path (a file path or a file object).

    speed: how many times faster than real time the GIF plays.
    """
    longest = max((len(trail) for trail in positions.values()), default=0)
    timelines = {slot: build_timeline(trail, longest) for slot, trail in sorted(positions.items())}
    end = max((tl[-1][0] for tl in timelines.values() if tl), default=0.0)

    font = ImageFont.load_default(size=13)
    big_font = ImageFont.load_default(size=15)
    step = speed / FPS  # game seconds per GIF frame

    frames = []
    now = 0.0
    while now < end:
        frames.append(draw_frame(match, timelines, now, font, big_font))
        now += step
    frames.append(draw_frame(match, timelines, end, font, big_font, final=True))

    durations = [1000 // FPS] * (len(frames) - 1) + [END_HOLD_MS]
    frames = [f.convert("P", palette=Image.ADAPTIVE, colors=32) for f in frames]
    frames[0].save(out_path, format="GIF", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)


def main():
    parser = argparse.ArgumentParser(description="Make an animated GIF replay of one match")
    parser.add_argument("--data", default=".", help="folder containing matches.json")
    parser.add_argument("--match", type=int, required=True, help="match id")
    parser.add_argument("--speed", type=float, default=6.0, help="playback speed (default 6x)")
    parser.add_argument("--out", help="output GIF (default: replay_match_<id>.gif)")
    args = parser.parse_args()

    match, positions = load_match(Path(args.data), args.match)
    out_path = Path(args.out or f"replay_match_{args.match}.gif")
    make_replay(match, positions, out_path, speed=args.speed)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()