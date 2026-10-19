import os
import logging
import sqlite3
import threading
import asyncio
from datetime import time as datetime_time

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
DB_NAME = "weather_bot.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================================================
# FLASK WEB SERVER
# Helps hosting platforms detect an open port.
# ==================================================

web = Flask(__name__)


@web.route("/")
def home():
    return "Weather Alert Bot is running!", 200


@web.route("/health")
def health():
    return {"status": "ok"}, 200


def start_web_server():
    port = int(os.getenv("PORT", "8080"))
    web.run(host="0.0.0.0", port=port, use_reloader=False)


# ==================================================
# DATABASE
# ==================================================

def db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT DEFAULT '',
                lat REAL,
                lon REAL,
                alerts INTEGER DEFAULT 0,
                alert_time TEXT DEFAULT '07:00'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            )
        """)

        defaults = {
            "start_message": (
                "🌦️ Welcome to Weather Alert Bot!\n\n"
                "Get current weather reports and daily weather alerts."
            ),
            "start_photo": "",
            "alert_time": "07:00",
        }

        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


def get_setting(key, default=""):
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )


def save_user(user_id, first_name):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, first_name)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET first_name=excluded.first_name
            """,
            (user_id, first_name),
        )


def save_location(user_id, lat, lon):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, lat, lon)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET lat=excluded.lat, lon=excluded.lon
            """,
            (user_id, lat, lon),
        )


# ==================================================
# WEATHER API
# Get a free API key from OpenWeather.
# ==================================================

def get_weather(lat, lon):
    if not API_KEY:
        return None, "Missing API_KEY. Add it to your hosting environment."

    url = "https://api.openweathermap.org/data/2.5/weather"

    try:
        response = requests.get(
            url,
            params={
                "lat": lat,
                "lon": lon,
                "appid": API_KEY,
                "units": "metric",
            },
            timeout=15,
        )

        if response.status_code != 200:
            logger.warning("Weather API returned %s", response.status_code)
            return None, "Weather service unavailable. Check your API key."

        data = response.json()

        weather = {
            "city": data.get("name", "Your location"),
            "temperature": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "humidity": data["main"]["humidity"],
            "description": data["weather"][0]["description"].title(),
            "wind": data["wind"].get("speed", 0),
        }

        return weather, None

    except requests.RequestException:
        logger.exception("Weather API request failed")
        return None, "Could not connect to the weather service."


def format_weather(weather):
    return (
        f"🌤️ WEATHER REPORT\n\n"
        f"📍 Location: {weather['city']}\n"
        f"🌡️ Temperature: {weather['temperature']}°C\n"
        f"🤗 Feels like: {weather['feels_like']}°C\n"
        f"☁️ Condition: {weather['description']}\n"
        f"💧 Humidity: {weather['humidity']}%\n"
        f"💨 Wind speed: {weather['wind']} m/s"
    )


# ==================================================
# KEYBOARDS
# ==================================================

def main_keyboard(user_id):
    buttons = [
        [
            InlineKeyboardButton("🌤️ Weather", callback_data="weather"),
            InlineKeyboardButton("🌧️ Alert", callback_data="alert"),
        ],
        [
            InlineKeyboardButton("👨‍💻 Developer", url=DEVELOPER),
        ],
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton("👑 Admin Panel", callback_data="admin")
        ])

    return InlineKeyboardMarkup(buttons)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistics", callback_data="stats"),
            InlineKeyboardButton("📢 Broadcast", callback_data="broadcast"),
        ],
        [
            InlineKeyboardButton("✏️ Start Message", callback_data="edit_message"),
            InlineKeyboardButton("🖼️ Start Photo", callback_data="edit_photo"),
        ],
        [
            InlineKeyboardButton("⏰ Alert Time", callback_data="edit_time"),
        ],
        [
            InlineKeyboardButton("⬅️ Main Menu", callback_data="home"),
        ],
    ])


# ==================================================
# /START COMMAND
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.first_name or "")

    message = get_setting("start_message")
    photo = get_setting("start_photo")
    keyboard = main_keyboard(user.id)

    if photo:
        try:
            await update.effective_message.reply_photo(
                photo=photo,
                caption=message,
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.exception("Could not send configured start photo")

    await update.effective_message.reply_text(
        message,
        reply_markup=keyboard,
    )


# ==================================================
# WEATHER AND ALERT FUNCTIONS
# ==================================================

async def send_weather(update, context):
    user_id = update.effective_user.id

    with db() as conn:
        row = conn.execute(
            "SELECT lat, lon FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if not row or row["lat"] is None or row["lon"] is None:
        context.user_data["waiting_location"] = True

        await update.effective_message.reply_text(
            "📍 Please share your location using the attachment menu "
            "in Telegram.\n\n"
            "Tap 📎 → Location → Send Current Location."
        )
        return

    weather, error = await asyncio.to_thread(
        get_weather, row["lat"], row["lon"]
    )

    if error:
        await update.effective_message.reply_text(f"⚠️ {error}")
        return

    await update.effective_message.reply_text(format_weather(weather))


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = update.effective_message.location
    user_id = update.effective_user.id

    save_location(user_id, location.latitude, location.longitude)

    waiting = context.user_data.pop("waiting_location", False)

    with db() as conn:
        row = conn.execute(
            "SELECT alerts FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        alerts_enabled = bool(row["alerts"]) if row else False

    if waiting and not alerts_enabled:
        with db() as conn:
            conn.execute(
                "UPDATE users SET alerts = 1 WHERE user_id = ?",
                (user_id,),
            )

        await update.effective_message.reply_text(
            "✅ Location saved!\n"
            "🌧️ Daily weather alerts are enabled.\n"
            "⏰ Default alert time: 7:00 AM"
        )

    weather, error = await asyncio.to_thread(
        get_weather, location.latitude, location.longitude
    )

    if error:
        await update.effective_message.reply_text(f"⚠️ {error}")
    else:
        await update.effective_message.reply_text(format_weather(weather))


async def daily_weather_alert(context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        users = conn.execute(
            """
            SELECT user_id, lat, lon
            FROM users
            WHERE alerts = 1 AND lat IS NOT NULL AND lon IS NOT NULL
            """
        ).fetchall()

    for user in users:
        try:
            weather, error = await asyncio.to_thread(
                get_weather, user["lat"], user["lon"]
            )

            if error:
                continue

            text = "🌅 DAILY WEATHER ALERT\n\n" + format_weather(weather)

            if "rain" in weather["description"].lower():
                text += "\n\n🌧️ Rain may be occurring. Please check local conditions."

            await context.bot.send_message(
                chat_id=user["user_id"],
                text=text,
            )

        except Exception:
            logger.exception("Could not send alert to %s", user["user_id"])


# ==================================================
# CALLBACK BUTTONS
# ==================================================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    action = query.data

    if action == "home":
        await query.message.reply_text(
            "🌦️ Weather Alert Bot",
            reply_markup=main_keyboard(user_id),
        )

    elif action == "weather":
        await send_weather(update, context)

    elif action == "alert":
        context.user_data["waiting_location"] = True

        await query.message.reply_text(
            "🌧️ To enable daily weather alerts, send your current location.\n\n"
            "📎 Tap Location → Send Current Location."
        )

    elif action == "admin":
        if user_id != ADMIN_ID:
            await query.answer("Not authorized.", show_alert=True)
            return

        await query.message.reply_text(
            "👑 ADMIN PANEL",
            reply_markup=admin_keyboard(),
        )

    elif action == "stats":
        if user_id != ADMIN_ID:
            return

        with db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

            alerts = conn.execute(
                "SELECT COUNT(*) FROM users WHERE alerts = 1"
            ).fetchone()[0]

        await query.message.reply_text(
            f"📊 BOT STATISTICS\n\n"
            f"👥 Total users: {total}\n"
            f"🌧️ Active alerts: {alerts}"
        )

    elif action == "broadcast":
        if user_id != ADMIN_ID:
            return

        context.user_data["admin_action"] = "broadcast"
        await query.message.reply_text(
            "📢 Send the text you want to broadcast to all bot users."
        )

    elif action == "edit_message":
        if user_id != ADMIN_ID:
            return

        context.user_data["admin_action"] = "edit_message"
        await query.message.reply_text("✏️ Send your new start message.")

    elif action == "edit_photo":
        if user_id != ADMIN_ID:
            return

        context.user_data["admin_action"] = "edit_photo"
        await query.message.reply_text(
            "🖼️ Send the photo you want to use as the start picture."
        )

    elif action == "edit_time":
        if user_id != ADMIN_ID:
            return

        context.user_data["admin_action"] = "edit_time"
        await query.message.reply_text(
            "⏰ Send the alert time in 24-hour format, for example 07:00."
        )


# ==================================================
# ADMIN TEXT AND PHOTO INPUT
# ==================================================

async def handle_admin_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        return

    action = context.user_data.get("admin_action")
    message = update.effective_message

    if not action:
        return

    if action == "edit_message" and message.text:
        set_setting("start_message", message.text)
        context.user_data.pop("admin_action", None)

        await message.reply_text("✅ Start message updated.")

    elif action == "edit_photo" and message.photo:
        set_setting("start_photo", message.photo[-1].file_id)
        context.user_data.pop("admin_action", None)

        await message.reply_text("✅ Start photo updated.")

    elif action == "edit_time" and message.text:
        try:
            hour, minute = map(int, message.text.strip().split(":"))

            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError

            new_time = f"{hour:02d}:{minute:02d}"
            set_setting("alert_time", new_time)

            # Update all registered users to the selected time.
            with db() as conn:
                conn.execute(
                    "UPDATE users SET alert_time = ?", (new_time,)
                )

            context.user_data.pop("admin_action", None)

            await message.reply_text(
                f"✅ Daily alert time updated to {new_time}.\n"
                "The time uses the hosting server's timezone."
            )

        except ValueError:
            await message.reply_text(
                "❌ Invalid time. Please use HH:MM, such as 07:00."
            )

    elif action == "broadcast" and message.text:
        context.user_data.pop("admin_action", None)

        with db() as conn:
            users = conn.execute(
                "SELECT user_id FROM users"
            ).fetchall()

        sent = 0

        for row in users:
            try:
                await context.bot.send_message(
                    chat_id=row["user_id"],
                    text=message.text,
                )
                sent += 1
            except Exception:
                logger.warning(
                    "Broadcast failed for user %s", row["user_id"]
                )

        await message.reply_text(
            f"✅ Broadcast finished.\nDelivered: {sent}/{len(users)}"
        )


# ==================================================
# APPLICATION STARTUP
# ==================================================

async def post_init(application: Application):
    # Keep the scheduler in Asia/Kolkata time.
    from zoneinfo import ZoneInfo

    application.job_queue.scheduler.configure(
        timezone=ZoneInfo("Asia/Kolkata")
    )

    hour, minute = map(
        int, get_setting("alert_time", "07:00").split(":")
    )

    application.job_queue.run_daily(
        daily_weather_alert,
        time=datetime_time(
            hour=hour,
            minute=minute,
            tzinfo=ZoneInfo("Asia/Kolkata"),
        ),
        name="daily_weather_alert",
    )

    logger.info("Weather bot started successfully.")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN environment variable.")

    if not API_KEY:
        logger.warning("API_KEY is missing; weather requests will fail.")

    init_db()

    threading.Thread(
        target=start_web_server,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CallbackQueryHandler(buttons)
    )

    application.add_handler(
        MessageHandler(filters.LOCATION, handle_location)
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            handle_admin_input,
        )
    )

    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
