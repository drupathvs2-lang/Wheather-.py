import os
import sqlite3
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIGURATION =================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

TIMEZONE = ZoneInfo("Asia/Kolkata")
DATABASE = "weather_bot.db"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================= DATABASE =================

def db():
    return sqlite3.connect(DATABASE, timeout=30)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT DEFAULT '',
                latitude REAL,
                longitude REAL,
                alerts INTEGER DEFAULT 1,
                alert_time TEXT DEFAULT '07:00'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.execute("""
            INSERT OR IGNORE INTO settings (key, value)
            VALUES ('start_message',
            'Welcome to Weather Alert Bot! 🌦️')
        """)


def register_user(user_id, first_name):
    with db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, first_name)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET first_name = excluded.first_name
        """, (user_id, first_name or ""))


def save_location(user_id, lat, lon):
    with db() as conn:
        conn.execute("""
            UPDATE users
            SET latitude = ?, longitude = ?
            WHERE user_id = ?
        """, (lat, lon, user_id))


def get_user(user_id):
    with db() as conn:
        return conn.execute("""
            SELECT user_id, first_name, latitude, longitude,
                   alerts, alert_time
            FROM users WHERE user_id = ?
        """, (user_id,)).fetchone()


def get_users():
    with db() as conn:
        return conn.execute("""
            SELECT user_id, latitude, longitude,
                   alerts, alert_time
            FROM users
        """).fetchall()


def get_setting(key, default=""):
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()

    return row[0] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
        """, (key, value))


# ================= WEATHER API =================

def get_weather(lat, lon):
    if not WEATHER_API_KEY:
        raise RuntimeError("WEATHER_API_KEY is not configured.")

    response = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={
            "lat": lat,
            "lon": lon,
            "appid": WEATHER_API_KEY,
            "units": "metric",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def get_forecast(lat, lon):
    response = requests.get(
        "https://api.openweathermap.org/data/2.5/forecast",
        params={
            "lat": lat,
            "lon": lon,
            "appid": WEATHER_API_KEY,
            "units": "metric",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def format_weather(data):
    weather = data["weather"][0]
    main = data["main"]
    wind = data.get("wind", {})

    return (
        f"🌦️ WEATHER REPORT\n\n"
        f"📍 {data.get('name', 'Your location')}\n"
        f"🌤️ Condition: {weather['description'].title()}\n"
        f"🌡️ Temperature: {main['temp']:.1f}°C\n"
        f"🤗 Feels like: {main['feels_like']:.1f}°C\n"
        f"💧 Humidity: {main['humidity']}%\n"
        f"💨 Wind: {wind.get('speed', 0)} m/s\n"
        f"🔺 High: {main.get('temp_max', 0):.1f}°C\n"
        f"🔻 Low: {main.get('temp_min', 0):.1f}°C"
    )


# ================= KEYBOARD =================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("🌦️ Weather"),
                KeyboardButton("🌧️ Rain Forecast"),
            ],
            [
                KeyboardButton(
                    "📍 Share Location",
                    request_location=True,
                ),
                KeyboardButton("⏰ Alert Settings"),
            ],
            [KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
    )


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats",
            ),
        ],
        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast",
            ),
        ],
        [
            InlineKeyboardButton(
                "✏️ Change Welcome Message",
                callback_data="admin_welcome",
            ),
        ],
    ])


# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)

    welcome = get_setting(
        "start_message",
        "Welcome to Weather Alert Bot! 🌦️",
    )

    await update.message.reply_text(
        f"Hello {user.first_name or 'there'}! 👋\n\n"
        f"{welcome}\n\n"
        "Share your location to get local weather updates.",
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌦️ WEATHER BOT HELP\n\n"
        "/start - Open the bot\n"
        "/weather - Current weather\n"
        "/forecast - Rain forecast\n"
        "/settime 07:00 - Set daily alert time\n"
        "/alerts_on - Enable daily alerts\n"
        "/alerts_off - Disable daily alerts\n"
        "/admin - Admin panel\n\n"
        "First, share your location.",
        reply_markup=main_keyboard(),
    )


# ================= LOCATION =================

async def location_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    location = update.message.location
    user_id = update.effective_user.id

    register_user(user_id, update.effective_user.first_name)
    save_location(
        user_id,
        location.latitude,
        location.longitude,
    )

    await update.message.reply_text(
        "✅ Location saved!\n"
        "You can now use Weather and Rain Forecast.",
        reply_markup=main_keyboard(),
    )


# ================= WEATHER =================

async def weather_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = get_user(update.effective_user.id)

    if not user or user[2] is None or user[3] is None:
        await update.message.reply_text(
            "📍 Please share your location first.",
            reply_markup=main_keyboard(),
        )
        return

    try:
        data = get_weather(user[2], user[3])
        await update.message.reply_text(format_weather(data))
    except Exception:
        logger.exception("Weather request failed")
        await update.message.reply_text(
            "⚠️ Weather service unavailable. Try again later."
        )


async def forecast_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = get_user(update.effective_user.id)

    if not user or user[2] is None or user[3] is None:
        await update.message.reply_text(
            "📍 Please share your location first."
        )
        return

    try:
        data = get_forecast(user[2], user[3])
        items = data.get("list", [])

        if not items:
            await update.message.reply_text(
                "No forecast data is currently available."
            )
            return

        lines = ["🌧️ RAIN FORECAST — NEXT 24 HOURS\n"]

        for item in items[:8]:
            dt = datetime.fromtimestamp(
                item["dt"], tz=TIMEZONE
            )
            condition = item["weather"][0]["description"].title()
            rain = item.get("rain", {}).get("3h", 0)
            pop = int(item.get("pop", 0) * 100)

            lines.append(
                f"🕒 {dt.strftime('%a %I:%M %p')}\n"
                f"🌤️ {condition}\n"
                f"🌧️ Rain probability: {pop}%\n"
                f"💧 Forecast rain: {rain} mm\n"
            )

        await update.message.reply_text("\n".join(lines))

    except Exception:
        logger.exception("Forecast request failed")
        await update.message.reply_text(
            "⚠️ Could not load the forecast. Try again later."
        )


# ================= ALERT SETTINGS =================

async def settime_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not context.args:
        await update.message.reply_text(
            "Usage: /settime 07:00\n"
            "Use 24-hour Indian local time."
        )
        return

    try:
        alert_time = context.args[0]
        datetime.strptime(alert_time, "%H:%M")
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid time. Example: /settime 07:00"
        )
        return

    with db() as conn:
        conn.execute("""
            UPDATE users SET alert_time = ?
            WHERE user_id = ?
        """, (alert_time, update.effective_user.id))

    await update.message.reply_text(
        f"✅ Daily alert time set to {alert_time} IST."
    )


async def alerts_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        conn.execute(
            "UPDATE users SET alerts = 1 WHERE user_id = ?",
            (update.effective_user.id,),
        )

    await update.message.reply_text("🔔 Daily alerts enabled.")


async def alerts_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        conn.execute(
            "UPDATE users SET alerts = 0 WHERE user_id = ?",
            (update.effective_user.id,),
        )

    await update.message.reply_text("🔕 Daily alerts disabled.")


# ================= DAILY ALERT JOB =================

async def daily_alert_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TIMEZONE).strftime("%H:%M")

    for user_id, lat, lon, enabled, alert_time in get_users():
        if not enabled or lat is None or lon is None:
            continue

        if alert_time != now:
            continue

        try:
            weather = get_weather(lat, lon)
            forecast = get_forecast(lat, lon)

            report = format_weather(weather)
            items = forecast.get("list", [])[:8]

            rain_expected = any(
                item.get("pop", 0) >= 0.5
                or item.get("rain", {}).get("3h", 0) >= 1
                for item in items
            )

            if rain_expected:
                report += (
                    "\n\n🌧️ RAIN ALERT!\n"
                    "Rain may occur in your location "
                    "during the next 24 hours. "
                    "Please check the hourly forecast."
                )
            else:
                report += "\n\n🌤️ No strong rain signal in the next 24 hours."

            await context.bot.send_message(
                chat_id=user_id,
                text=report,
            )

        except Exception:
            logger.exception(
                "Daily alert failed for user %s", user_id
            )


# ================= ADMIN =================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text(
        "👑 WEATHER BOT ADMIN PANEL",
        reply_markup=admin_keyboard(),
    )


async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ Admin only.")
        return

    if query.data == "admin_stats":
        with db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

            locations = conn.execute("""
                SELECT COUNT(*) FROM users
                WHERE latitude IS NOT NULL
            """).fetchone()[0]

        await query.edit_message_text(
            f"📊 BOT STATISTICS\n\n"
            f"👥 Total users: {count}\n"
            f"📍 Users with locations: {locations}"
        )

    elif query.data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text(
            "📢 Send the broadcast message as your next message."
        )

    elif query.data == "admin_welcome":
        context.user_data["awaiting_welcome"] = True
        await query.edit_message_text(
            "✏️ Send the new welcome message."
        )


async def admin_text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    if context.user_data.pop("awaiting_welcome", False):
        set_setting("start_message", update.message.text)
        await update.message.reply_text("✅ Welcome message updated.")
        return

    if context.user_data.pop("awaiting_broadcast", False):
        message = update.message.text
        success = 0
        failed = 0

        for user in get_users():
            try:
                await context.bot.send_message(
                    chat_id=user[0],
                    text=message,
                )
                success += 1
            except Exception:
                failed += 1

        await update.message.reply_text(
            f"📢 Broadcast finished.\n"
            f"✅ Sent: {success}\n"
            f"⚠️ Failed: {failed}"
        )


# ================= BUTTON HANDLER =================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text

    if text == "🌦️ Weather":
        await weather_command(update, context)

    elif text == "🌧️ Rain Forecast":
        await forecast_command(update, context)

    elif text == "⏰ Alert Settings":
        await update.message.reply_text(
            "⏰ DAILY ALERT SETTINGS\n\n"
            "Set time: /settime 07:00\n"
            "Enable: /alerts_on\n"
            "Disable: /alerts_off"
        )

    elif text == "ℹ️ Help":
        await help_command(update, context)


# ================= MAIN =================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set the BOT_TOKEN environment variable.")

    if not WEATHER_API_KEY:
        raise RuntimeError("Set the WEATHER_API_KEY environment variable.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("forecast", forecast_command))
    app.add_handler(CommandHandler("settime", settime_command))
    app.add_handler(CommandHandler("alerts_on", alerts_on))
    app.add_handler(CommandHandler("alerts_off", alerts_off))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(admin_callback))
    app.add_handler(
        MessageHandler(filters.LOCATION, location_handler)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_text_handler,
        ),
        group=0,
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            button_handler,
        ),
        group=1,
    )

    app.job_queue.run_repeating(
        daily_alert_job,
        interval=60,
        first=10,
        name="weather_alerts",
    )

    logger.info("Weather bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
