"""world.py - the map, line of sight and pathfinding for the ARENA game server.

The map is a copy of worldMap in firmware/tinyboy/tinyboy.ino (0 = floor, 1/2 = wall).
Keep them in sync until the team moves to a shared maps/*.json file.
"""
import math
from collections import deque

DEFAULT_GRID = [
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

# Spawn points (centres of open squares), one per player slot 0-3
DEFAULT_SPAWNS = [(2.5, 2.5), (13.5, 13.5), (13.5, 2.5), (2.5, 13.5)]


class GameMap:
    def __init__(self, grid=None, spawns=None):
        self.grid = grid if grid is not None else DEFAULT_GRID
        self.spawns = spawns if spawns is not None else DEFAULT_SPAWNS
        self.h = len(self.grid)
        self.w = len(self.grid[0])

    def is_wall(self, x, y):
        ix, iy = int(math.floor(x)), int(math.floor(y))
        if ix < 0 or iy < 0 or ix >= self.w or iy >= self.h:
            return True
        return self.grid[iy][ix] != 0

    def line_of_sight(self, x0, y0, x1, y1):
        """True if no wall lies between the two points (same stepping as hasLineOfSight on the watch)."""
        dx, dy = x1 - x0, y1 - y0
        steps = int(math.hypot(dx, dy) / 0.1)
        for i in range(1, steps):
            t = i / steps
            if self.is_wall(x0 + dx * t, y0 + dy * t):
                return False
        return True

    def open_cells(self):
        return [(x, y) for y in range(self.h) for x in range(self.w) if self.grid[y][x] == 0]

    def neighbours(self, cell):
        x, y = cell
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < self.w and 0 <= ny < self.h and self.grid[ny][nx] == 0:
                yield (nx, ny)

    def find_path(self, start, goal):
        """Breadth-first search between two grid cells. Returns a list of cells from start to goal, or None."""
        if start == goal:
            return [start]
        prev = {start: None}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in self.neighbours(cur):
                if nxt in prev:
                    continue
                prev[nxt] = cur
                if nxt == goal:
                    path = [nxt]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])
                    return path[::-1]
                queue.append(nxt)
        return None

    def is_connected(self):
        """Playability check: every open cell can reach every other open cell."""
        cells = self.open_cells()
        if not cells:
            return False
        seen = {cells[0]}
        queue = deque([cells[0]])
        while queue:
            for nxt in self.neighbours(queue.popleft()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return len(seen) == len(cells)


def cell_of(x, y):
    return (int(math.floor(x)), int(math.floor(y)))


def centre_of(cell):
    return (cell[0] + 0.5, cell[1] + 0.5)
