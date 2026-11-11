"""Draw a heatmap of where each player spent their time in a match.

Reads matches.json + positions/match_<id>.json (docs/interfaces.md, 4b)
and saves a PNG with one map per player.

    python heatmap.py --data sample --match 1
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # save to file, no window
import matplotlib.pyplot as plt
import numpy as np

SAMPLES_PER_SECOND = 5  # bridge.py records POS 5x per second

# Copy of worldMap in firmware/tinyboy/tinyboy.ino (0 = floor, 1/2 = walls).
# Keep in sync if the map changes.
WORLD_MAP = [
    [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1],
    [1,0,0,0,0,0,0,1,0,0,0,0,0,0,0,1],
    [1,0,0,0,0,0,0,1,0,0,0,0,0,0,0,1],
    [1,0,0,2,2,0,0,0,0,0,0,2,2,0,0,1],
    [1,0,0,2,0,0,0,0,0,0,0,0,2,0,0,1],
    [1,0,0,0,0,0,1,1,1,1,0,0,0,0,0,1],
    [1,0,0,0,0,0,1,0,0,1,0,0,0,0,0,1],
    [1,1,1,0,0,0,0,0,0,0,0,0,0,1,1,1],
    [1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1],
    [1,0,0,0,0,0,1,0,0,1,0,0,0,0,0,1],
    [1,0,0,0,0,0,1,1,1,1,0,0,0,0,0,1],
    [1,0,0,2,0,0,0,0,0,0,0,0,2,0,0,1],
    [1,0,0,2,2,0,0,0,0,0,0,2,2,0,0,1],
    [1,0,0,0,0,0,0,0,1,0,0,0,0,0,0,1],
    [1,0,0,0,0,0,0,0,1,0,0,0,0,0,0,1],
    [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1],
]


def load_match(data_dir: Path, match_id: int):
    with open(data_dir / "matches.json", encoding="utf-8") as f:
        data = json.load(f)
    for match in data.get("matches", []):
        if match["id"] == match_id:
            with open(data_dir / match["positions_file"], encoding="utf-8") as f:
                return match, json.load(f)
    sys.exit(f"Match {match_id} not found in {data_dir / 'matches.json'}")


def seconds_per_square(trail):
    """Count how many seconds the player spent in each map square."""
    grid = np.zeros((len(WORLD_MAP), len(WORLD_MAP[0])))
    for x, y, _t in trail:
        col, row = int(x), int(y)
        if 0 <= row < grid.shape[0] and 0 <= col < grid.shape[1]:
            grid[row][col] += 1 / SAMPLES_PER_SECOND
    return grid


def draw(match, positions, out_path: Path):
    walls = np.array(WORLD_MAP) > 0
    slots = sorted(positions)
    fig, axes = plt.subplots(1, len(slots), figsize=(5 * len(slots), 5.4))
    axes = np.atleast_1d(axes)

    for ax, slot in zip(axes, slots):
        grid = seconds_per_square(positions[slot])
        heat = np.ma.masked_where(walls | (grid == 0), grid)

        ax.set_facecolor("#f4f4f4")  # floor never visited
        ax.imshow(np.ma.masked_where(~walls, walls), cmap="Greys", vmin=0, vmax=1.4)
        img = ax.imshow(heat, cmap="YlOrRd", vmin=0)
        fig.colorbar(img, ax=ax, fraction=0.046, pad=0.04, label="seconds")

        name = match["players"].get(slot, slot)
        score = match.get("score", {}).get(slot, "?")
        ax.set_title(f"{name} ({slot}) – rounds won: {score}")
        ax.set_xticks([])
        ax.set_yticks([])

    winner = match.get("winner") or "none"
    fig.suptitle(f"Match {match['id']} heatmap · {match['status']} · winner: {winner}")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Make a per-player heatmap PNG for one match")
    parser.add_argument("--data", default=".", help="folder containing matches.json")
    parser.add_argument("--match", type=int, required=True, help="match id")
    parser.add_argument("--out", help="output PNG (default: heatmap_match_<id>.png)")
    args = parser.parse_args()

    data_dir = Path(args.data)
    match, positions = load_match(data_dir, args.match)
    out_path = Path(args.out or f"heatmap_match_{args.match}.png")
    draw(match, positions, out_path)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
