import os
import sqlite3
import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
DB_FILE = "trash_tracker.db"

# 1. Database Management
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_index INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flatmates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute("INSERT OR IGNORE INTO state (id, current_index) VALUES (1, 0)")
        conn.commit()

def get_turn_state():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_index FROM state WHERE id = 1")
        row = cur.fetchone()
        idx = row[0] if row else 0

        cur.execute("SELECT username FROM flatmates ORDER BY id ASC")
        members = [r[0] for r in cur.fetchall()]
        
        if members and idx >= len(members):
            idx = 0
            conn.execute("UPDATE state SET current_index = 0 WHERE id = 1")
            conn.commit()

        return idx, members

def format_mention(handle: str) -> str:
    handle = handle.strip()
    return handle if handle.startswith("@") else f"@{handle}"

# 2. Command Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Trash tracker is running!\n\n"
        "Commands:\n"
        "• `/add @user` – Add a flatmate to the rotation\n"
        "• `/list` – View the roster & current turn\n"
        "• `/who` – See whose turn it is\n"
        "• `/done` – Finish turn and notify next person\n"
        "• `/skip` – Skip the current person"
    )

async def who_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx, members = get_turn_state()
    if not members:
        await update.message.reply_text("No flatmates registered yet. Add someone with `/add @username`.")
        return

    current = format_mention(members[idx])
    await update.message.reply_text(
        f"🗑️ It is currently {current}'s turn to take out the trash!",
        parse_mode=ParseMode.HTML
    )

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx, members = get_turn_state()
    if not members:
        await update.message.reply_text("No flatmates found. Use `/add @username` first.")
        return

    finished = format_mention(members[idx])
    next_idx = (idx + 1) % len(members)
    next_person = format_mention(members[next_idx])

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE state SET current_index = ? WHERE id = 1", (next_idx,))
        conn.commit()

    await update.message.reply_text(
        f"✅ Thanks, {finished}!\n"
        f"👉 Up next: {next_person} — your turn!",
        parse_mode=ParseMode.HTML
    )

async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx, members = get_turn_state()
    if not members:
        return

    skipped = format_mention(members[idx])
    next_idx = (idx + 1) % len(members)
    next_person = format_mention(members[next_idx])

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE state SET current_index = ? WHERE id = 1", (next_idx,))
        conn.commit()

    await update.message.reply_text(
        f"⏭️ Skipped {skipped}.\n"
        f"👉 Up next: {next_person} — your turn!",
        parse_mode=ParseMode.HTML
    )

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/add @username`")
        return

    raw_user = context.args[0]
    username = format_mention(raw_user)

    with sqlite3.connect(DB_FILE) as conn:
        try:
            conn.execute("INSERT INTO flatmates (username) VALUES (?)", (username,))
            conn.commit()
            await update.message.reply_text(f"Added {username} to the rotation.", parse_mode=ParseMode.HTML)
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"{username} is already in the list.")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx, members = get_turn_state()
    if not members:
        await update.message.reply_text("No flatmates registered yet. Add someone with `/add @username`.")
        return

    lines = ["<b>Current Rotation Order:</b>"]
    for i, member in enumerate(members):
        pointer = "👉 " if i == idx else "   "
        lines.append(f"{pointer}{i + 1}. {format_mention(member)}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# 3. HTTP Server for Render Health Check
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health server running on port {PORT}")
    return runner

# 4. Entrypoint
async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in environment variables!")
        return

    init_db()
    runner = await start_web_server()

    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start_command))
    tg_app.add_handler(CommandHandler("who", who_command))
    tg_app.add_handler(CommandHandler("done", done_command))
    tg_app.add_handler(CommandHandler("skip", skip_command))
    tg_app.add_handler(CommandHandler("add", add_command))
    tg_app.add_handler(CommandHandler("list", list_command))

    # Correct lifecycle: initialize and start through the async context manager
    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram polling started successfully.")
        
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await tg_app.updater.stop()
            await tg_app.stop()
            await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
