import os
import logging
import asyncio
import threading
from datetime import time

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

# ---------------- CONFIGURATION ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_KEY = os.getenv("API_KEY", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8661663559"))

DEVELOPER = "https://t.me/telgram_boy"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------- FLASK SERVER ----------------

app = Flask(__name__)


@app.route("/")
def home():
    return "Weather Alert Bot is running!", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def run_web_server():
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# ---------------- WEATHER API ----------------

def get_weather(lat, lon):
    if not API_KEY:
        return None, "Weather API key is missing."

    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": lat,
                "lon": lon,
                "appid": API_KEY,
                "units": "metric",
            },
            timeout=15,
        )

        data = response.json()

        if response.status_code != 200:
            return None, data.get("message", "Weather API error.")

        weather = {
            "city": data.get("name", "Your location"),
            "temperature": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "humidity": data["main"]["humidity"],
            "description": data["weather"][0]["description"],
            "wind": data["wind"].get("speed", 0),
        }

        return weather, None

    except requests.RequestException:
        logger.exception("Weather API request failed")
        return None, "Unable to connect to the weather service."

    except (KeyError, ValueError, TypeError):
        logger.exception("Invalid weather API response")
        return None, "Could not read weather data."


def format_weather(weather):
    return (
        f"🌦️ Weather Report: {weather['city']}\n\n"
        f"🌡️ Temperature: {weather['temperature']}°C\n"
        f"🤔 Feels like: {weather['feels_like']}°C\n"
        f"☁️ Condition: {weather['description'].title()}\n"
        f"💧 Humidity: {weather['humidity']}%\n"
        f"💨 Wind: {weather['wind']} m/s"
    )


# ---------------- BOT COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    keyboard = [
        [
            InlineKeyboardButton("🌦️ Weather", callback_data="weather"),
            InlineKeyboardButton("🌧️ Rain Alert", callback_data="alert"),
        ],
        [
            InlineKeyboardButton(
                "👨‍💻 Developer",
                url=DEVELOPER,
            )
        ],
    ]

    if user.id == ADMIN_ID:
        keyboard.append([
            InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin")
        ])

    await update.effective_message.reply_text(
        f"Hello {user.first_name}! 👋\n\n"
        "Welcome to Weather Alert Bot.\n\n"
        "🌦️ Get current weather for your location.\n"
        "🌧️ Receive a daily weather and rain report.\n\n"
        "Choose an option below:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data in ("weather", "alert"):
        context.user_data["action"] = query.data

        await query.message.reply_text(
            "📍 Please share your location using Telegram's "
            "attachment menu → Location → Send My Current Location."
        )

    elif query.data == "admin":
        if query.from_user.id != ADMIN_ID:
            await query.message.reply_text("⛔ Access denied.")
            return

        keyboard = [
            [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
        ]

        await query.message.reply_text(
            "⚙️ Admin Panel",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data == "stats":
        if query.from_user.id != ADMIN_ID:
            return

        users = context.application.bot_data.get("users", {})
        await query.message.reply_text(
            f"📊 Users with saved locations: {len(users)}"
        )

    elif query.data == "broadcast":
        if query.from_user.id != ADMIN_ID:
            return

        context.user_data["broadcast"] = True
        await query.message.reply_text(
            "Send the message you want to broadcast to saved users."
        )


# ---------------- LOCATION HANDLER ----------------

async def location_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    location = update.message.location
    user_id = update.effective_user.id

    lat = location.latitude
    lon = location.longitude

    users = context.application.bot_data.setdefault("users", {})
    users[user_id] = {"lat": lat, "lon": lon}

    action = context.user_data.get("action", "weather")

    weather, error = await asyncio.to_thread(get_weather, lat, lon)

    if error:
        await update.message.reply_text(f"⚠️ {error}")
        return

    await update.message.reply_text(format_weather(weather))

    if action == "alert":
        context.application.bot_data.setdefault(
            "alert_users", set()
        ).add(user_id)

        await update.message.reply_text(
            "✅ Rain alert enabled!\n\n"
            "I will send a daily weather report at 7:00 AM "
            "while the bot is running."
        )


# ---------------- TEXT AND BROADCAST HANDLER ----------------

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    message = update.message.text

    if user_id == ADMIN_ID and context.user_data.get("broadcast"):
        context.user_data["broadcast"] = False

        users = context.application.bot_data.get("users", {})
        sent = 0

        for target_id in list(users.keys()):
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=message,
                )
                sent += 1
            except Exception:
                logger.exception("Broadcast failed for user %s", target_id)

        await update.message.reply_text(
            f"✅ Broadcast completed.\nDelivered to: {sent} users."
        )
        return

    await update.message.reply_text(
        "Please use the Weather or Rain Alert buttons."
    )


# ---------------- DAILY WEATHER ALERT ----------------

async def daily_alert(context: ContextTypes.DEFAULT_TYPE):
    users = context.application.bot_data.get("users", {})
    alert_users = context.application.bot_data.get("alert_users", set())

    for user_id in list(alert_users):
        user_data = users.get(user_id)

        if not user_data:
            continue

        weather, error = await asyncio.to_thread(
            get_weather,
            user_data["lat"],
            user_data["lon"],
        )

        if error:
            logger.warning("Alert failed for user %s: %s", user_id, error)
            continue

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🌅 Daily Weather Alert\n\n" + format_weather(weather),
            )
        except Exception:
            logger.exception("Could not send alert to %s", user_id)


# ---------------- ERROR HANDLER ----------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "An exception occurred while processing an update:",
        exc_info=context.error,
    )


# ---------------- START BOT ----------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    if not API_KEY:
        raise RuntimeError("API_KEY environment variable is missing.")

    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(
        MessageHandler(filters.LOCATION, location_handler)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    application.add_error_handler(error_handler)

    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue is unavailable. Install python-telegram-bot[job-queue]."
        )

    application.job_queue.run_daily(
        daily_alert,
        time=time(hour=7, minute=0),
        name="daily_weather_alert",
    )

    logger.info("Starting Weather Alert Bot...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
