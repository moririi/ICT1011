"""SQLite match database: copies matches.json + positions/ into arena.db and answers stats queries.

matches.json stays the source of truth (only bridge.py writes it, docs/interfaces.md 4b).
Importing again is safe: each match is replaced, never duplicated.

    python database.py --data sample                  # import, then print the top players
    python database.py --data sample --top accuracy   # rank by wins | accuracy | hits | rounds
"""
import argparse
import json
import sqlite3
from pathlib import Path

DB_NAME = "arena.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY,          -- same id as in matches.json
    game        TEXT NOT NULL DEFAULT 'fps',  -- room for other games / modes later
    started     TEXT,
    ended       TEXT,
    status      TEXT,                         -- finished / aborted / in_progress
    winner_slot TEXT                          -- 'A', 'B' or NULL
);
CREATE TABLE IF NOT EXISTS match_players (
    match_id   INTEGER NOT NULL REFERENCES matches(id),
    slot       TEXT    NOT NULL,              -- 'A' or 'B'
    player_id  INTEGER NOT NULL REFERENCES players(id),
    rounds_won INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (match_id, slot)
);
CREATE TABLE IF NOT EXISTS rounds (
    match_id    INTEGER NOT NULL REFERENCES matches(id),
    round       INTEGER NOT NULL,
    winner_slot TEXT,                         -- 'A', 'B' or 'draw'
    duration_s  REAL,
    PRIMARY KEY (match_id, round)
);
CREATE TABLE IF NOT EXISTS round_stats (
    match_id INTEGER NOT NULL,
    round    INTEGER NOT NULL,
    slot     TEXT    NOT NULL,
    shots    INTEGER NOT NULL DEFAULT 0,
    hits     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (match_id, round, slot)
);
CREATE TABLE IF NOT EXISTS positions (
    match_id INTEGER NOT NULL,
    slot     TEXT    NOT NULL,
    t        REAL    NOT NULL,                -- seconds since match start
    x        REAL    NOT NULL,                -- map squares
    y        REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS positions_by_match ON positions (match_id, slot);
"""

# Career stats per player, counting finished matches only (like the bot's /leaderboard)
PLAYER_STATS_SQL = """
SELECT p.name,
       COUNT(*)                                         AS matches,
       SUM(m.winner_slot = mp.slot)                     AS wins,
       SUM(mp.rounds_won)                               AS rounds_won,
       COALESCE(SUM(s.shots), 0)                        AS shots,
       COALESCE(SUM(s.hits), 0)                         AS hits,
       1.0 * COALESCE(SUM(s.hits), 0) / MAX(COALESCE(SUM(s.shots), 0), 1) AS accuracy
FROM match_players mp
JOIN players p ON p.id = mp.player_id
JOIN matches m ON m.id = mp.match_id
LEFT JOIN (SELECT match_id, slot, SUM(shots) AS shots, SUM(hits) AS hits
           FROM round_stats GROUP BY match_id, slot) s
       ON s.match_id = mp.match_id AND s.slot = mp.slot
WHERE m.status = 'finished'
GROUP BY p.id
"""

TOP_ORDER = {
    "wins": "wins DESC, accuracy DESC",
    "accuracy": "accuracy DESC, wins DESC",
    "hits": "hits DESC, accuracy DESC",
    "rounds": "rounds_won DESC, wins DESC",
}

FASTEST_ROUND_SQL = """
SELECT r.match_id, r.round, r.duration_s, p.name
FROM rounds r
JOIN matches m        ON m.id = r.match_id
JOIN match_players mp ON mp.match_id = r.match_id AND mp.slot = r.winner_slot
JOIN players p        ON p.id = mp.player_id
WHERE m.status = 'finished' AND r.duration_s > 0
ORDER BY r.duration_s
LIMIT 1
"""


def connect(data_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir / DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def player_id(conn, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM players WHERE name = ?", (name,)).fetchone()["id"]


def import_match(conn, data_dir: Path, match: dict):
    """Replaces everything stored for this match with what matches.json says now."""
    match_id = match["id"]
    for table in ("positions", "round_stats", "rounds", "match_players", "matches"):
        conn.execute(f"DELETE FROM {table} WHERE {'id' if table == 'matches' else 'match_id'} = ?", (match_id,))

    conn.execute(
        "INSERT INTO matches (id, started, ended, status, winner_slot) VALUES (?, ?, ?, ?, ?)",
        (match_id, match.get("started"), match.get("ended"), match.get("status"), match.get("winner")),
    )
    score = match.get("score", {})
    for slot, name in match.get("players", {}).items():
        conn.execute(
            "INSERT INTO match_players (match_id, slot, player_id, rounds_won) VALUES (?, ?, ?, ?)",
            (match_id, slot, player_id(conn, name), score.get(slot, 0)),
        )

    for r in match.get("rounds", []):
        conn.execute(
            "INSERT INTO rounds (match_id, round, winner_slot, duration_s) VALUES (?, ?, ?, ?)",
            (match_id, r["round"], r.get("winner"), r.get("duration_s")),
        )
        shots, hits = r.get("shots", {}), r.get("hits", {})
        for slot in set(shots) | set(hits):
            conn.execute(
                "INSERT INTO round_stats (match_id, round, slot, shots, hits) VALUES (?, ?, ?, ?, ?)",
                (match_id, r["round"], slot, shots.get(slot, 0), hits.get(slot, 0)),
            )

    positions_file = match.get("positions_file")
    if positions_file and (data_dir / positions_file).exists():
        with open(data_dir / positions_file, encoding="utf-8") as f:
            trails = json.load(f)
        conn.executemany(
            "INSERT INTO positions (match_id, slot, t, x, y) VALUES (?, ?, ?, ?, ?)",
            [(match_id, slot, t, x, y) for slot, trail in trails.items() for x, y, t in trail],
        )


def import_all(data_dir: Path) -> int:
    """Imports every match in data_dir/matches.json. Returns how many matches were imported."""
    with open(data_dir / "matches.json", encoding="utf-8") as f:
        matches = json.load(f).get("matches", [])
    conn = connect(data_dir)
    try:
        with conn:  # one transaction: all matches or nothing
            for match in matches:
                import_match(conn, data_dir, match)
    finally:
        conn.close()
    return len(matches)


def top_players(data_dir: Path, stat: str = "wins", limit: int = 5) -> list:
    conn = connect(data_dir)
    try:
        sql = f"{PLAYER_STATS_SQL} ORDER BY {TOP_ORDER[stat]} LIMIT ?"
        return [dict(row) for row in conn.execute(sql, (limit,))]
    finally:
        conn.close()


def fastest_round(data_dir: Path):
    conn = connect(data_dir)
    try:
        row = conn.execute(FASTEST_ROUND_SQL).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Import matches.json into SQLite and show the top players")
    parser.add_argument("--data", default=".", help="folder containing matches.json")
    parser.add_argument("--top", choices=sorted(TOP_ORDER), default="wins", help="stat to rank by")
    args = parser.parse_args()

    data_dir = Path(args.data)
    count = import_all(data_dir)
    print(f"Imported {count} matches into {data_dir / DB_NAME}")

    print(f"\nTop players by {args.top}:")
    for i, p in enumerate(top_players(data_dir, args.top), start=1):
        print(f"{i}. {p['name']}: {p['wins']} wins / {p['matches']} matches, "
              f"{p['hits']}/{p['shots']} hits ({p['accuracy']:.0%}), {p['rounds_won']} rounds won")

    fastest = fastest_round(data_dir)
    if fastest:
        print(f"\nFastest round: {fastest['duration_s']:.1f} s by {fastest['name']} "
              f"(match {fastest['match_id']}, round {fastest['round']})")


if __name__ == "__main__":
    main()