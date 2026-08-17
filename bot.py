import asyncio
import datetime
import logging
import os
import sqlite3
from typing import Optional

from aiohttp import web
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Interval Configurations (in seconds)
NORMAL_BASE_SECONDS = 20 * 60  # 20 minutes
NORMAL_NAG_SECONDS = 3 * 60    # 3 minutes

TEST_BASE_SECONDS = 60         # 1 minute (Test Mode)
TEST_NAG_SECONDS = 15          # 15 seconds (Test Mode)

DB_PATH = "reminders.db"

# Global in-memory active tasks: user_id -> asyncio.Task
active_tasks = {}
telegram_app: Optional[Application] = None


# --- Database Persistence Layer ---

def init_db():
    """Initializes the SQLite database to store user state."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                is_enabled INTEGER DEFAULT 1,
                is_nagging INTEGER DEFAULT 0,
                is_test_mode INTEGER DEFAULT 0,
                next_alert_time TEXT,
                last_done_time TEXT,
                nag_count INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()


def get_user_data(user_id: int, chat_id: Optional[int] = None) -> dict:
    """Fetches user state from SQLite or creates default entry."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        else:
            default_chat = chat_id or user_id
            cursor.execute(
                """
                INSERT INTO users (user_id, chat_id, is_enabled, is_nagging, is_test_mode, nag_count)
                VALUES (?, ?, 1, 0, 0, 0)
                """,
                (user_id, default_chat),
            )
            conn.commit()
            return {
                "user_id": user_id,
                "chat_id": default_chat,
                "is_enabled": 1,
                "is_nagging": 0,
                "is_test_mode": 0,
                "next_alert_time": None,
                "last_done_time": None,
                "nag_count": 0,
            }


def update_user_data(user_id: int, **kwargs):
    """Updates specified fields for a user in SQLite."""
    if not kwargs:
        return
    set_clauses = [f"{key} = ?" for key in kwargs.keys()]
    values = list(kwargs.values()) + [user_id]
    query = f"UPDATE users SET {', '.join(set_clauses)} WHERE user_id = ?"
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(query, values)
        conn.commit()


def get_all_active_users():
    """Returns all users who have reminders enabled."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE is_enabled = 1")
        return [dict(row) for row in cursor.fetchall()]


# --- UI & Dashboard Helpers ---

def get_dashboard_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Builds interactive inline keyboard for the dashboard."""
    status_label = "⏸ Pause Reminders" if state["is_enabled"] else "▶️ Resume Reminders"
    test_label = "⚡ Test Mode: ON (1m/15s)" if state["is_test_mode"] else "⚡ Test Mode: OFF (20m/3m)"

    keyboard = [
        [InlineKeyboardButton("✅ DONE", callback_data="action_done")],
        [InlineKeyboardButton(status_label, callback_data="toggle_service")],
        [InlineKeyboardButton(test_label, callback_data="toggle_test_mode")],
        [
            InlineKeyboardButton("🔔 Trigger Now", callback_data="trigger_now"),
            InlineKeyboardButton("🔄 Refresh Status", callback_data="refresh_status"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_dashboard_text(state: dict) -> str:
    """Generates formatted status overview markdown."""
    status_str = "🟢 Reminders Active" if state["is_enabled"] else "🔴 Reminders Paused"
    state_str = "🚨 Nagging Mode Active" if state["is_nagging"] else "⏳ Waiting for Interval"
    mode_str = "Fast Test Mode (1m base / 15s nag)" if state["is_test_mode"] else "Normal Mode (20m base / 3m nag)"

    last_done_val = state.get("last_done_time")
    if last_done_val:
        try:
            dt = datetime.datetime.fromisoformat(last_done_val)
            last_done_str = dt.strftime("%I:%M:%S %p")
        except Exception:
            last_done_str = last_done_val
    else:
        last_done_str = "None yet"

    next_alert_val = state.get("next_alert_time")
    if state["is_enabled"] and next_alert_val:
        try:
            next_dt = datetime.datetime.fromisoformat(next_alert_val)
            remaining_seconds = int((next_dt - datetime.datetime.now()).total_seconds())
            if remaining_seconds > 0:
                mins, secs = divmod(remaining_seconds, 60)
                next_str = f"in {mins:02d}m {secs:02d}s ({next_dt.strftime('%I:%M %p')})"
            else:
                next_str = "Due now"
        except Exception:
            next_str = "Scheduled"
    else:
        next_str = "Paused"

    nag_info = f"\n*Nag Count:* {state.get('nag_count', 0)}" if state["is_nagging"] else ""

    return (
        "⏱️ *INTERVAL REMINDER DASHBOARD*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"*Status:* {status_str}\n"
        f"*State:* {state_str}{nag_info}\n"
        f"*Mode:* {mode_str}\n"
        f"*Next Alert:* {next_str}\n"
        f"*Last Completed:* `{last_done_str}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "_Click the buttons below to manage your reminders:_"
    )


# --- Timer & Nagging Logic ---

def cancel_timer(user_id: int):
    """Cancels any running async timer for the user."""
    task = active_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()


def schedule_reminder(user_id: int, chat_id: int, is_nag: bool = False, delay_seconds: Optional[int] = None):
    """Schedules the next reminder or nag alert."""
    cancel_timer(user_id)

    state = get_user_data(user_id, chat_id)
    if not state["is_enabled"]:
        return

    if delay_seconds is None:
        if state["is_test_mode"]:
            delay_seconds = TEST_NAG_SECONDS if is_nag else TEST_BASE_SECONDS
        else:
            delay_seconds = NORMAL_NAG_SECONDS if is_nag else NORMAL_BASE_SECONDS

    next_alert_dt = datetime.datetime.now() + datetime.timedelta(seconds=delay_seconds)
    update_user_data(
        user_id,
        is_nagging=1 if is_nag else 0,
        next_alert_time=next_alert_dt.isoformat(),
        chat_id=chat_id,
    )

    async def timer_job():
        try:
            await asyncio.sleep(delay_seconds)
            current_state = get_user_data(user_id, chat_id)
            if not current_state["is_enabled"]:
                return

            if telegram_app is None:
                return

            bot = telegram_app.bot
            if is_nag:
                new_nag_count = current_state.get("nag_count", 0) + 1
                update_user_data(user_id, nag_count=new_nag_count)
                text = (
                    f"🚨 *NAG ALERT (#{new_nag_count})*\n"
                    "You haven't acknowledged the task yet!\n"
                    "Alerting repeatedly until you tap *DONE*."
                )
            else:
                update_user_data(user_id, nag_count=0)
                text = (
                    "⏰ *20-Minute Task Reminder!*\n"
                    "Time to take action! Please complete your task and tap *DONE*."
                )

            done_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ DONE", callback_data="action_done")],
                [InlineKeyboardButton("📊 Dashboard", callback_data="refresh_status")],
            ])

            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=done_keyboard,
            )

            # Auto-schedule nagging alert if not acknowledged
            schedule_reminder(user_id, chat_id, is_nag=True)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error executing reminder job for user {user_id}: {e}")

    active_tasks[user_id] = asyncio.create_task(timer_job())


async def handle_done(user_id: int, chat_id: int):
    """Handles task completion, resets timers, and begins next interval."""
    cancel_timer(user_id)
    now_iso = datetime.datetime.now().isoformat()
    update_user_data(
        user_id,
        is_nagging=0,
        nag_count=0,
        last_done_time=now_iso,
    )
    state = get_user_data(user_id, chat_id)
    if state["is_enabled"]:
        schedule_reminder(user_id, chat_id, is_nag=False)


# --- Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command: initializes user and displays dashboard."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    state = get_user_data(user.id, chat_id)

    # If reminders enabled but no active timer, schedule base reminder
    if state["is_enabled"] and user.id not in active_tasks:
        schedule_reminder(user.id, chat_id, is_nag=False)
        state = get_user_data(user.id, chat_id)

    await update.message.reply_text(
        text=format_dashboard_text(state),
        parse_mode="Markdown",
        reply_markup=get_dashboard_keyboard(state),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /status command: displays live dashboard."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    state = get_user_data(user.id, chat_id)
    await update.message.reply_text(
        text=format_dashboard_text(state),
        parse_mode="Markdown",
        reply_markup=get_dashboard_keyboard(state),
    )


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /done command."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    await handle_done(user.id, chat_id)
    now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
    await update.message.reply_text(
        f"✅ *Task marked as DONE at {now_str}!* Next interval scheduled.",
        parse_mode="Markdown",
    )


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /pause or /stop command."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    cancel_timer(user.id)
    update_user_data(user.id, is_enabled=0, is_nagging=0, next_alert_time=None)
    await update.message.reply_text("⏸ Reminders paused. Send /resume or /start to continue.")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /resume command."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    update_user_data(user.id, is_enabled=1)
    schedule_reminder(user.id, chat_id, is_nag=False)
    await update.message.reply_text("▶️ Reminders resumed! Next alert scheduled.")


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /test command to toggle test mode."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    state = get_user_data(user.id, chat_id)
    new_mode = 0 if state["is_test_mode"] else 1
    update_user_data(user.id, is_test_mode=new_mode)
    if state["is_enabled"]:
        schedule_reminder(user.id, chat_id, is_nag=False)
    mode_text = "ENABLED (1m base / 15s nag)" if new_mode else "DISABLED (20m base / 3m nag)"
    await update.message.reply_text(f"⚡ Fast Test Mode is now *{mode_text}*.", parse_mode="Markdown")


async def trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /trigger command to manually trigger alert."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    done_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ DONE", callback_data="action_done")],
    ])
    await update.message.reply_text(
        "🔔 *Manual Alert Triggered!*\nTap DONE below to acknowledge.",
        parse_mode="Markdown",
        reply_markup=done_keyboard,
    )
    schedule_reminder(user.id, chat_id, is_nag=True)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /help command."""
    help_text = (
        "💡 *INTERVAL REMINDER BOT - HELP*\n\n"
        "• `/start` - Launch interactive dashboard\n"
        "• `/status` - Check current countdown & status\n"
        "• `/done` - Mark current interval as completed\n"
        "• `/pause` - Pause recurring reminders\n"
        "• `/resume` - Resume recurring reminders\n"
        "• `/test` - Toggle 1m/15s fast test mode\n"
        "• `/trigger` - Manually trigger an alert immediately\n\n"
        "⚙️ *How it works:*\n"
        "1. Sends a reminder every 20 minutes (or 1 min in Test Mode).\n"
        "2. If not acknowledged within 3 minutes (or 15 sec), sends nagging alerts every 3 minutes until you tap DONE."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


# --- Callback Query Handler ---

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all inline keyboard button clicks."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    chat_id = update.effective_chat.id
    data = query.data

    if data == "action_done":
        await handle_done(user.id, chat_id)
        now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
        await query.edit_message_text(
            text=f"✅ *Task marked DONE at {now_str}!*\nTimer reset for the next interval.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 View Dashboard", callback_data="refresh_status")],
            ]),
        )
        return

    elif data == "toggle_service":
        state = get_user_data(user.id, chat_id)
        new_enabled = 0 if state["is_enabled"] else 1
        update_user_data(user.id, is_enabled=new_enabled)
        if new_enabled:
            schedule_reminder(user.id, chat_id, is_nag=False)
        else:
            cancel_timer(user.id)
            update_user_data(user.id, is_nagging=0, next_alert_time=None)

    elif data == "toggle_test_mode":
        state = get_user_data(user.id, chat_id)
        new_test = 0 if state["is_test_mode"] else 1
        update_user_data(user.id, is_test_mode=new_test)
        if state["is_enabled"]:
            schedule_reminder(user.id, chat_id, is_nag=False)

    elif data == "trigger_now":
        done_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ DONE", callback_data="action_done")],
        ])
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔔 *Manual Alert Triggered!*\nTap DONE below to acknowledge.",
            parse_mode="Markdown",
            reply_markup=done_keyboard,
        )
        schedule_reminder(user.id, chat_id, is_nag=True)

    # Refresh dashboard view
    state = get_user_data(user.id, chat_id)
    try:
        await query.edit_message_text(
            text=format_dashboard_text(state),
            parse_mode="Markdown",
            reply_markup=get_dashboard_keyboard(state),
        )
    except Exception:
        pass


# --- Background Cloud Health Check Server ---

async def run_healthcheck_server():
    """Runs a lightweight aiohttp server for platforms requiring open HTTP ports (Render/Koyeb)."""
    async def handle_ping(request):
        return web.Response(text="Interval Reminder Telegram Bot is healthy & running 24/7!")

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)

    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check server started on port {port}")


# --- Application Startup & Main Entry ---

async def main():
    global telegram_app
    init_db()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set! Please configure it in .env or cloud settings.")
        print("\n❌ ERROR: TELEGRAM_BOT_TOKEN is missing!")
        print("Please set TELEGRAM_BOT_TOKEN in your .env file or environment variables.\n")
        return

    # Build Application
    telegram_app = Application.builder().token(token).build()

    # Register Handlers
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("done", done_command))
    telegram_app.add_handler(CommandHandler("pause", pause_command))
    telegram_app.add_handler(CommandHandler("stop", pause_command))
    telegram_app.add_handler(CommandHandler("resume", resume_command))
    telegram_app.add_handler(CommandHandler("test", test_command))
    telegram_app.add_handler(CommandHandler("trigger", trigger_command))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CallbackQueryHandler(button_callback_handler))

    # Restore active reminders from database on startup
    active_users = get_all_active_users()
    for u in active_users:
        u_id = u["user_id"]
        c_id = u.get("chat_id") or u_id
        is_nag = bool(u.get("is_nagging", 0))
        schedule_reminder(u_id, c_id, is_nag=is_nag)
    logger.info(f"Restored reminders for {len(active_users)} active user(s).")

    # Start healthcheck server in the background
    await run_healthcheck_server()

    # Run bot polling
    logger.info("Bot started successfully. Listening for updates...")
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    # Keep running forever
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
