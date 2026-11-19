import argparse
import asyncio
import io
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from heatmap import draw as draw_heatmap
from replay import make_replay
import database

# Load environment variables from .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID")  # topic inside the group (optional)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Silence routine 3-second heartbeat logs
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DATA_DIR = Path(".")


def read_data_safely(max_retries: int = 3, delay: float = 1.0) -> dict:
    """Reads matches.json and returns the full dictionary with retry logic."""
    file_path = DATA_DIR / "matches.json"
    if not file_path.exists():
        return {}

    for attempt in range(max_retries):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                elif isinstance(data, list):
                    return {"matches": data}
        except (json.JSONDecodeError, PermissionError, OSError):
            if attempt < max_retries - 1:
                import time
                time.sleep(delay)
            else:
                logger.warning("Could not read matches.json after retries.")
                return {}
    return {}


def calculate_stats(data: dict) -> dict:
    """
    Retrieves player stats. Uses top-level 'players' dict if present,
    otherwise calculates career stats from finished match rounds.
    """
    stats = {}

    # Case A: Top-level "players" dict contains career totals
    top_players = data.get("players", {})
    if isinstance(top_players, dict) and top_players:
        for p_key, p_info in top_players.items():
            if isinstance(p_info, dict):
                stats[p_key] = {
                    "display_name": p_info.get("name", p_key),
                    "matches": p_info.get("matches", 0),
                    "wins": p_info.get("wins", 0),
                    "losses": p_info.get("losses", 0),
                    "rounds_won": p_info.get("rounds_won", 0),
                    "rounds_played": 0,
                    "shots": p_info.get("shots", 0),
                    "hits": p_info.get("hits", 0),
                }
        if stats:
            # The totals do not include rounds played, so count them from the finished matches
            for m in data.get("matches", []):
                if m.get("status") == "finished":
                    for p_id in m.get("players", {}):
                        if p_id in stats:
                            stats[p_id]["rounds_played"] += len(m.get("rounds", []))
            return stats

    # Case B: Fallback calculation iterating through finished matches & rounds
    stats = defaultdict(lambda: {
        "display_name": "",
        "matches": 0,
        "wins": 0,
        "losses": 0,
        "rounds_won": 0,
        "rounds_played": 0,
        "shots": 0,
        "hits": 0,
    })

    matches = data.get("matches", [])
    for m in matches:
        if m.get("status") != "finished":
            continue

        winner = m.get("winner")
        match_players = m.get("players", {})

        for p_id, display in match_players.items():
            s = stats[p_id]
            s["display_name"] = display
            s["matches"] += 1
            s["rounds_played"] += len(m.get("rounds", []))
            if p_id == winner:
                s["wins"] += 1
            else:
                s["losses"] += 1

        for r in m.get("rounds", []):
            r_win = r.get("winner")
            if r_win:
                stats[r_win]["rounds_won"] += 1
            for p_id, shot_count in r.get("shots", {}).items():
                stats[p_id]["shots"] += shot_count
            for p_id, hit_count in r.get("hits", {}).items():
                stats[p_id]["hits"] += hit_count

    return stats


# No shot accuracy anywhere: the gun fires automatically, so hits/shots always looks terrible.
# Rounds won / rounds played and hits per round are fairer.
def win_rate(rounds_won: int, rounds_played: int) -> float:
    return rounds_won / rounds_played if rounds_played > 0 else 0.0


def format_win_rate(rounds_won: int, rounds_played: int) -> str:
    return f"{win_rate(rounds_won, rounds_played) * 100:.0f}%"


def format_hits_per_round(hits: int, rounds_played: int) -> str:
    if rounds_played <= 0:
        return "0.0"
    return f"{hits / rounds_played:.1f}"


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ==========================================
# COMMAND HANDLERS
# ==========================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎮 *PROJECT ARENA HELPER BOT*\n\n"
        "Track live scores, match results, and leaderboard stats directly from your phone!\n\n"
        "*Commands:*\n"
        "🏆 `/leaderboard` - Player rankings by wins\n"
        "📊 `/stats <name>` - Career stats for a specific player\n"
        "⚔️ `/lastmatch` - Details of the most recent finished match\n"
        "🔥 `/heatmap [match_id]` - Where each player spent their time (default: latest match)\n"
        "🎬 `/replay [match_id]` - Animated top-down replay of a match (default: latest match)\n"
        "📈 `/top [wins|winrate|hits|rounds]` - Top players from the match database\n"
        "ℹ️ `/help` - Show this list"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = read_data_safely()
    stats = calculate_stats(data)

    if not stats:
        await update.message.reply_text("No finished matches recorded yet.")
        return

    # Rank by wins descending, then round win rate descending
    ranked = sorted(
        stats.items(),
        key=lambda item: (
            item[1]["wins"],
            win_rate(item[1]["rounds_won"], item[1]["rounds_played"])
        ),
        reverse=True
    )

    lines = ["🏆 *ARENA LEADERBOARD*\n"]
    medals = ["🥇", "🥈", "🥉"]

    for idx, (p_id, s) in enumerate(ranked, start=1):
        rank_icon = medals[idx - 1] if idx <= 3 else f"`{idx}.`"
        rate = format_win_rate(s["rounds_won"], s["rounds_played"])
        name = s["display_name"] or p_id
        lines.append(f"{rank_icon} *{name}* — {s['wins']}W / {s['losses']}L | Rounds won: `{rate}`")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/stats <player_name>`", parse_mode=ParseMode.MARKDOWN)
        return

    target = " ".join(context.args).strip()
    data = read_data_safely()
    stats = calculate_stats(data)

    # Case-insensitive match check against ID ("A") or display name ("Player A")
    player_data = None
    matched_label = target
    for p_id, p_stats in stats.items():
        if target.lower() in (p_id.lower(), p_stats["display_name"].lower()):
            player_data = p_stats
            matched_label = p_stats["display_name"]
            break

    if not player_data:
        await update.message.reply_text(f"No match history found for player *{target}*.", parse_mode=ParseMode.MARKDOWN)
        return

    rate = format_win_rate(player_data["rounds_won"], player_data["rounds_played"])
    per_round = format_hits_per_round(player_data["hits"], player_data["rounds_played"])
    text = (
        f"📊 *PLAYER STATS: {matched_label}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• *Matches Played:* {player_data['matches']}\n"
        f"• *Wins:* {player_data['wins']} | *Losses:* {player_data['losses']}\n"
        f"• *Rounds Won:* {player_data['rounds_won']} of {player_data['rounds_played']} (`{rate}`)\n"
        f"• *Hits Landed:* {player_data['hits']} (`{per_round}` per round)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


def format_match_report(match: dict) -> str:
    match_id = match.get("id", "N/A")
    mode = match.get("mode", "1v1 Duel")
    winner_key = match.get("winner", "None")
    player_names = match.get("players", {})

    winner_name = player_names.get(winner_key, winner_key)
    scores = match.get("score") or match.get("scores") or {}
    rounds = match.get("rounds", [])
    duration = sum(r.get("duration_s") or 0 for r in rounds)

    lines = [
        f"⚔️ *MATCH #{match_id} COMPLETED*",
        f"Mode: _{mode}_",
    ]
    if duration:
        lines.append(f"Duration: {format_duration(duration)}")
    lines.append(f"🏆 *Winner: {winner_name}*\n")

    # Scores are rounds won, shown as one line: "Alice 3 – 0 Bob"
    if len(scores) == 2:
        (a_key, a_score), (b_key, b_score) = sorted(scores.items())
        lines.append(
            f"*Rounds:* {player_names.get(a_key, a_key)} {a_score} – {b_score} {player_names.get(b_key, b_key)}"
        )
    elif scores:
        lines.append("*Rounds:* " + " · ".join(
            f"{player_names.get(k, k)} {v}" for k, v in sorted(scores.items())
        ))

    if rounds:
        lines.append("\n*Round Breakdown:*")
        for r in rounds:
            r_num = r.get("round", "?")
            r_winner_key = r.get("winner")
            if r_winner_key in player_names:
                result = f"Won by *{player_names[r_winner_key]}*"
            else:
                result = "Draw"
            if r.get("duration_s"):
                result += f" ({r['duration_s']:.1f} s)"
            lines.append(f"`R{r_num}`: {result}")

    return "\n".join(lines)


async def lastmatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = read_data_safely()
    matches = data.get("matches", [])
    finished = [m for m in matches if m.get("status") == "finished"]

    if not finished:
        await update.message.reply_text("No completed matches found.")
        return

    last = finished[-1]
    text = format_match_report(last)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


def render_heatmap_png(match: dict) -> io.BytesIO:
    """Loads the match's positions file and draws its heatmap into memory."""
    with open(DATA_DIR / match["positions_file"], "r", encoding="utf-8") as f:
        positions = json.load(f)
    image = io.BytesIO()
    draw_heatmap(match, positions, image)
    image.seek(0)
    return image


async def heatmap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = read_data_safely()
    matches = [m for m in data.get("matches", []) if m.get("positions_file")]

    if not matches:
        await update.message.reply_text("No matches with position data yet.")
        return

    if context.args:
        try:
            match_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Usage: `/heatmap [match_id]`", parse_mode=ParseMode.MARKDOWN)
            return
        match = next((m for m in matches if m.get("id") == match_id), None)
        if match is None:
            await update.message.reply_text(f"No position data for match #{match_id}.")
            return
    else:
        match = matches[-1]

    try:
        # Drawing takes ~1 s, so run it off the event loop
        image = await asyncio.to_thread(render_heatmap_png, match)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not draw heatmap for match #{match.get('id')}: {e}")
        await update.message.reply_text(f"Could not read the position data for match #{match.get('id')}.")
        return

    await update.message.reply_photo(photo=image, caption=f"🔥 Heatmap – Match #{match['id']}")


def render_replay_gif(match: dict) -> io.BytesIO:
    """Loads the match's positions file and draws its replay GIF into memory."""
    with open(DATA_DIR / match["positions_file"], "r", encoding="utf-8") as f:
        positions = json.load(f)
    gif = io.BytesIO()
    make_replay(match, positions, gif)
    gif.seek(0)
    return gif


async def replay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = read_data_safely()
    matches = [m for m in data.get("matches", []) if m.get("positions_file")]

    if not matches:
        await update.message.reply_text("No matches with position data yet.")
        return

    if context.args:
        try:
            match_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Usage: `/replay [match_id]`", parse_mode=ParseMode.MARKDOWN)
            return
        match = next((m for m in matches if m.get("id") == match_id), None)
        if match is None:
            await update.message.reply_text(f"No position data for match #{match_id}.")
            return
    else:
        match = matches[-1]

    try:
        # Drawing every frame takes a few seconds, so run it off the event loop
        gif = await asyncio.to_thread(render_replay_gif, match)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not draw replay for match #{match.get('id')}: {e}")
        await update.message.reply_text(f"Could not read the position data for match #{match.get('id')}.")
        return

    await update.message.reply_animation(
        animation=gif,
        filename=f"replay_match_{match['id']}.gif",
        caption=f"🎬 Replay – Match #{match['id']}",
    )


TOP_TITLES = {"wins": "Wins", "winrate": "Round win rate", "hits": "Hits per round", "rounds": "Rounds won"}


def load_top(stat: str):
    """Syncs matches.json into arena.db, then reads the top players and the fastest round."""
    database.import_all(DATA_DIR)
    return database.top_players(DATA_DIR, stat), database.fastest_round(DATA_DIR)


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stat = context.args[0].lower() if context.args else "wins"
    if stat not in TOP_TITLES:
        await update.message.reply_text(
            "Usage: `/top [wins|winrate|hits|rounds]`", parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        players, fastest = await asyncio.to_thread(load_top, stat)
    except (OSError, json.JSONDecodeError, sqlite3.Error) as e:
        logger.warning(f"Could not update the match database: {e}")
        await update.message.reply_text("Could not read the match data right now.")
        return

    if not players:
        await update.message.reply_text("No finished matches recorded yet.")
        return

    lines = [f"📈 *TOP PLAYERS – {TOP_TITLES[stat].upper()}*\n"]
    medals = ["🥇", "🥈", "🥉"]
    for idx, p in enumerate(players, start=1):
        rank_icon = medals[idx - 1] if idx <= 3 else f"`{idx}.`"
        rate = format_win_rate(p["rounds_won"], p["rounds_played"])
        per_round = format_hits_per_round(p["hits"], p["rounds_played"])
        lines.append(
            f"{rank_icon} *{p['name']}* — {p['wins']}W in {p['matches']} | "
            f"Rounds: {p['rounds_won']}/{p['rounds_played']} (`{rate}`) | `{per_round}` hits/round"
        )
    if fastest:
        lines.append(
            f"\n⚡ Fastest round: *{fastest['duration_s']:.1f} s* by {fastest['name']} "
            f"(match #{fastest['match_id']}, R{fastest['round']})"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)   


# ==========================================
# BACKGROUND AUTO-REPORT JOB
# ==========================================

async def poll_new_matches(context: ContextTypes.DEFAULT_TYPE):
    """Checks for newly finished matches every few seconds and posts to the group."""
    chat_id = context.job.data.get("chat_id")
    if not chat_id:
        return

    data = read_data_safely()
    matches = data.get("matches", [])
    processed_ids = context.bot_data.setdefault("processed_matches", set())

    # On first boot, mark all existing finished matches as processed
    if context.bot_data.get("initialized") is not True:
        for m in matches:
            if m.get("status") == "finished" and "id" in m:
                processed_ids.add(m["id"])
        context.bot_data["initialized"] = True
        return

    # Check for newly finished matches
    for m in matches:
        m_id = m.get("id")
        if m.get("status") == "finished" and m_id not in processed_ids:
            processed_ids.add(m_id)
            report = format_match_report(m)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=int(TELEGRAM_THREAD_ID) if TELEGRAM_THREAD_ID else None,
                    text=f"📢 *AUTO MATCH REPORT*\n\n{report}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Failed to broadcast match #{m_id}: {e}")


# ==========================================
# MAIN RUNNER
# ==========================================

def main():
    parser = argparse.ArgumentParser(description="Project Arena Telegram Bot")
    parser.add_argument(
        "--data",
        type=str,
        default=".",
        help="Path to folder containing matches.json (e.g. my_test)",
    )
    args = parser.parse_args()

    global DATA_DIR
    DATA_DIR = Path(args.data)

    if not TELEGRAM_TOKEN:
        sys.exit("Error: TELEGRAM_TOKEN is not set in laptop/.env")

    print(f"[BOT] Watching data directory: {DATA_DIR.resolve()}")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("lastmatch", lastmatch_command))
    app.add_handler(CommandHandler("heatmap", heatmap_command))
    app.add_handler(CommandHandler("replay", replay_command))
    app.add_handler(CommandHandler("top", top_command))

    # Set up background polling job (every 3.0 seconds)
    if TELEGRAM_CHAT_ID:
        app.job_queue.run_repeating(
            poll_new_matches,
            interval=3.0,
            first=1.0,
            data={"chat_id": TELEGRAM_CHAT_ID},
        )
        print(f"[BOT] Auto-reports targeted to Chat ID: {TELEGRAM_CHAT_ID}")
    else:
        print("[BOT] WARNING: TELEGRAM_CHAT_ID not configured. Auto-reports disabled.")

    print("[BOT] Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()