import os
import logging
import asyncio
import sqlite3
import threading

from datetime import time
from zoneinfo import ZoneInfo

import requests
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==================================================
# CONFIGURATION
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8661663559"))

DEVELOPER = "https://t.me/telgram_boy"
DATABASE = os.getenv("DATABASE_PATH", "weather_bot.db")

TIMEZONE = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

logger = logging.getLogger("weather_bot")

# ==================================================
# FLASK SERVER
# ==================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Weather Alert Bot is running!", 200


@web_app.route("/health")
def health():
    return {"status": "ok"}, 200


def run_web_server():
    port = int(os.environ.get("PORT", "8080"))

    web_app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
        threaded=True,
    )


# ==================================================
# SQLITE DATABASE
# ==================================================

db_lock = threading.Lock()


def get_db():
    connection = sqlite3.connect(
        DATABASE,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db_lock:
        with get_db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    alerts_enabled INTEGER NOT NULL DEFAULT 0
                )
            """)

            db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            db.commit()


def save_location(user_id, latitude, longitude):
    with db_lock:
        with get_db() as db:
            db.execute("""
                INSERT INTO users (
                    user_id,
                    latitude,
                    longitude,
                    alerts_enabled
                )
                VALUES (?, ?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    latitude = excluded.latitude,
                    longitude = excluded.longitude
            """, (user_id, latitude, longitude))

            db.commit()


def enable_alert(user_id):
    with db_lock:
        with get_db() as db:
            db.execute("""
                UPDATE users
                SET alerts_enabled = 1
                WHERE user_id = ?
            """, (user_id,))

            db.commit()


def get_user(user_id):
    with db_lock:
        with get_db() as db:
            return db.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()


def get_all_users():
    with db_lock:
        with get_db() as db:
            return db.execute(
                "SELECT * FROM users"
            ).fetchall()


def get_alert_users():
    with db_lock:
        with get_db() as db:
            return db.execute("""
                SELECT * FROM users
                WHERE alerts_enabled = 1
            """).fetchall()


def get_user_count():
    with db_lock:
        with get_db() as db:
            return db.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]


# ==================================================
# WEATHER API
# ==================================================

def get_weather(latitude, longitude):
    if not API_KEY:
        return None, "Weather API key is missing."

    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": latitude,
                "lon": longitude,
                "appid": API_KEY,
                "units": "metric",
            },
            timeout=15,
        )

        try:
            data = response.json()
        except ValueError:
            return None, "Invalid response from weather service."

        if response.status_code != 200:
            return None, data.get(
                "message",
                "Weather API request failed.",
            )

        weather_info = data["weather"][0]
        main_info = data["main"]

        weather = {
            "city": data.get("name") or "Your location",
            "temperature": main_info["temp"],
            "feels_like": main_info["feels_like"],
            "humidity": main_info["humidity"],
            "description": weather_info["description"],
            "wind": data.get("wind", {}).get("speed", 0),
        }

        return weather, None

    except requests.RequestException:
        logger.exception("Weather API connection failed")
        return None, "Could not connect to the weather service."

    except (KeyError, IndexError, TypeError):
        logger.exception("Invalid weather data")
        return None, "Could not process the weather information."


def format_weather(weather):
    return (
        f"🌦️ WEATHER REPORT\n"
        f"📍 Location: {weather['city']}\n\n"
        f"🌡️ Temperature: {weather['temperature']}°C\n"
        f"🤔 Feels like: {weather['feels_like']}°C\n"
        f"☁️ Condition: {weather['description'].title()}\n"
        f"💧 Humidity: {weather['humidity']}%\n"
        f"💨 Wind: {weather['wind']} m/s"
    )


# ==================================================
# START COMMAND
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    keyboard = [
        [
            InlineKeyboardButton(
                "🌦️ Weather",
                callback_data="weather",
            ),
            InlineKeyboardButton(
                "🌧️ Rain Alert",
                callback_data="alert",
            ),
        ],
        [
            InlineKeyboardButton(
                "👨‍💻 Developer",
                url=DEVELOPER,
            ),
        ],
    ]

    if user.id == ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton(
                "⚙️ Admin Panel",
                callback_data="admin",
            ),
        ])

    await update.effective_message.reply_text(
        f"Hello {user.first_name}! 👋\n\n"
        "Welcome to Weather Alert Bot.\n\n"
        "🌦️ Check the current weather at your location.\n"
        "🌧️ Receive daily weather reports at 7:00 AM IST.\n\n"
        "Choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ==================================================
# BUTTON HANDLER
# ==================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data in ("weather", "alert"):
        context.user_data["action"] = query.data

        await query.message.reply_text(
            "📍 Please share your current location.\n\n"
            "Telegram → Attachment → Location → "
            "Send My Current Location."
        )

    elif query.data == "admin":
        if query.from_user.id != ADMIN_ID:
            await query.message.reply_text("⛔ Access denied.")
            return

        keyboard = [
            [
                InlineKeyboardButton(
                    "📊 Statistics",
                    callback_data="stats",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📢 Broadcast",
                    callback_data="broadcast",
                ),
            ],
        ]

        await query.message.reply_text(
            "⚙️ ADMIN PANEL",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "stats":
        if query.from_user.id != ADMIN_ID:
            await query.message.reply_text("⛔ Access denied.")
            return

        users = get_user_count()
        alerts = len(get_alert_users())

        await query.message.reply_text(
            f"📊 BOT STATISTICS\n\n"
            f"👥 Saved users: {users}\n"
            f"🌧️ Active alert subscriptions: {alerts}"
        )

    elif query.data == "broadcast":
        if query.from_user.id != ADMIN_ID:
            await query.message.reply_text("⛔ Access denied.")
            return

        context.user_data["broadcast"] = True

        await query.message.reply_text(
            "📢 Send the text message you want to broadcast.\n\n"
            "The message will be sent to saved users."
        )


# ==================================================
# LOCATION HANDLER
# ==================================================

async def location_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    location = update.effective_message.location
    user_id = update.effective_user.id

    if location is None:
        return

    latitude = location.latitude
    longitude = location.longitude

    await asyncio.to_thread(
        save_location,
        user_id,
        latitude,
        longitude,
    )

    action = context.user_data.get("action", "weather")

    weather, error = await asyncio.to_thread(
        get_weather,
        latitude,
        longitude,
    )

    if error:
        await update.effective_message.reply_text(
            f"⚠️ {error}"
        )
        return

    await update.effective_message.reply_text(
        format_weather(weather)
    )

    if action == "alert":
        await asyncio.to_thread(enable_alert, user_id)

        await update.effective_message.reply_text(
            "✅ DAILY WEATHER ALERT ENABLED!\n\n"
            "I will send your daily weather report at "
            "7:00 AM Kerala time (IST), while the bot "
            "and its scheduled job are running."
        )

    context.user_data.pop("action", None)


# ==================================================
# TEXT AND BROADCAST HANDLER
# ==================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    message = update.effective_message.text

    if user_id == ADMIN_ID and context.user_data.get("broadcast"):
        context.user_data["broadcast"] = False

        users = await asyncio.to_thread(get_all_users)

        sent = 0
        failed = 0

        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=message,
                )
                sent += 1

            except Exception:
                failed += 1
                logger.exception(
                    "Broadcast failed for user %s",
                    user["user_id"],
                )

        await update.effective_message.reply_text(
            "📢 BROADCAST FINISHED\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}"
        )
        return

    await update.effective_message.reply_text(
        "Please use the Weather or Rain Alert buttons."
    )


# ==================================================
# DAILY WEATHER ALERT
# ==================================================

async def daily_alert(
    context: ContextTypes.DEFAULT_TYPE,
):
    users = await asyncio.to_thread(get_alert_users)

    logger.info("Sending daily alerts to %s users", len(users))

    for user in users:
        weather, error = await asyncio.to_thread(
            get_weather,
            user["latitude"],
            user["longitude"],
        )

        if error:
            logger.warning(
                "Weather lookup failed for %s: %s",
                user["user_id"],
                error,
            )
            continue

        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=(
                    "🌅 DAILY WEATHER ALERT\n\n"
                    + format_weather(weather)
                ),
            )

        except Exception:
            logger.exception(
                "Could not send daily alert to %s",
                user["user_id"],
            )


# ==================================================
# ERROR HANDLER
# ==================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Exception while processing an update",
        exc_info=(
            type(context.error),
            context.error,
            context.error.__traceback__,
        ) if context.error else None,
    )


# ==================================================
# MAIN
# ==================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing from Render Environment."
        )

    if not API_KEY:
        raise RuntimeError(
            "API_KEY is missing from Render Environment."
        )

    init_db()

    # Flask health server
    threading.Thread(
        target=run_web_server,
        daemon=True,
        name="flask-health-server",
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.LOCATION,
            location_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(error_handler)

    if application.job_queue is None:
        raise RuntimeError(
            'Install python-telegram-bot with the "job-queue" extra.'
        )

    application.job_queue.run_daily(
        daily_alert,
        time=time(
            hour=7,
            minute=0,
            tzinfo=TIMEZONE,
        ),
        name="daily_weather_alert",
    )

    logger.info("Starting Weather Alert Bot...")

    # Start only one polling loop in this process.
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
