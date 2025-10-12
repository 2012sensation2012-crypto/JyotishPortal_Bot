import os
import datetime
import pytz
import requests
from astral import LocationInfo
from astral.sun import sun
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import swisseph as swe
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from flask import Flask

# === ИНИЦИАЛИЗАЦИЯ SWISS EPH ===
ephemeris_path = os.path.join(os.path.dirname(__file__), "ephemeris")
swe.set_ephe_path(ephemeris_path)

# Инициализация geopy и других библиотек
geolocator = Nominatim(user_agent="ufo_portal_bot")
tf = TimezoneFinder()

# Создаём Flask-сервер для Render
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "Bot is alive! 🛸"

def get_kp_index(date):
    try:
        # Заглушка — в реальном проекте можно подключить API NOAA
        return 2.0
    except:
        return 2.0

def get_nakshatra(moon_lon):
    nakshatras = [
        ("Ашвини", 0.0, 13.2),
        ("Шатабхиша", 306.4, 320.0),
        ("Мула", 240.0, 253.2),
        ("Уттара Бхадрапада", 333.2, 346.4),
        ("Пурва Ашадха", 270.0, 283.2),
        ("Уттара Ашадха", 283.2, 296.4),
        ("Шравана", 296.4, 309.6),
        ("Пурва Фалгуни", 309.6, 322.8),
        ("Уттара Фалгуни", 322.8, 336.0)
    ]
    for name, start, end in nakshatras:
        if start <= moon_lon < end:
            return name
    return None

def is_night(lat, lon, dt):
    try:
        tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
        local_tz = pytz.timezone(tz_str)
        local_dt = dt.astimezone(local_tz)
        city = LocationInfo("", "", tz_str, lat, lon)
        s = sun(city.observer, date=local_dt.date())
        return local_dt < s["dawn"] or local_dt > s["dusk"]
    except Exception as e:
        print(f"⚠️ Ошибка определения ночи: {e}")
        return True  # По умолчанию считаем, что ночь

def classify_event(lat, lon, dt):
    jd = swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute/60.0)
    sun_pos = swe.calc_ut(jd, swe.SUN)[0][0] % 360
    moon_pos = swe.calc_ut(jd, swe.MOON)[0][0] % 360
    rahu_pos = swe.calc_ut(jd, swe.MEAN_NODE)[0][0] % 360

    lon_360 = lon if lon >= 0 else 360 + lon
    cond1 = min(abs(lon_360 - rahu_pos), abs(lon_360 - rahu_pos + 360), abs(lon_360 - rahu_pos - 360)) <= 3
    angle = (moon_pos - sun_pos) % 360
    in_8th = 210 <= angle <= 240
    in_12th = 330 <= angle <= 360
    nakshatra = get_nakshatra(moon_pos)
    in_mula = nakshatra == "Мула"
    cond2 = in_8th or in_12th or in_mula
    cond3 = nakshatra in ["Ашвини", "Шатабхиша", "Мула", "Уттара Бхадрапада", "Пурва Ашадха", "Уттара Ашадха", "Шравана", "Пурва Фалгуни", "Уттара Фалгуни"]
    cond4 = 25 <= abs(lat) <= 50
    cond5 = is_night(lat, lon, dt)
    kp = get_kp_index(dt.date())
    cond6 = kp <= 5

    if (cond1 or cond3 or cond5) and cond2 and cond4 and cond6:
        return "✅ Тип 1 (Геопортал)", f"Раху: {rahu_pos:.1f}°\nЛуна: {moon_pos:.1f}° ({nakshatra})\nKp: {kp}\nНочь: {'Да' if cond5 else 'Нет'}"
    elif (in_8th or in_12th) and cond3 and cond6:
        return "🌤 Тип 2 (Атмосферный)", f"Луна: {moon_pos:.1f}° ({nakshatra})\nKp: {kp}"
    elif cond1 and (in_8th or in_12th or in_mula) and (not cond3 or kp >= 6):
        return "💥 Тип 4 (Аварийный)", f"Kp: {kp}\nНакшатра: {nakshatra or '—'}"
    elif cond6 and cond5:
        return "👁️ Тип 5 (Наблюдательный)", f"Луна: {moon_pos:.1f}° ({nakshatra or '—'})\nKp: {kp}\n(Событие зафиксировано людьми)"
    elif cond5 and cond6:
        return "👽 Тип 6 (Контактный)", f"Луна: {moon_pos:.1f}° ({nakshatra or '—'})\nKp: {kp}\n(Контакт зафиксирован людьми)"
    else:
        return "❌ Вне системы", f"Луна: {moon_pos:.1f}° ({nakshatra or '—'})\nРаху: {rahu_pos:.1f}°\nKp: {kp}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌌 <b>Система Инопланетян порталов v2.1</b>\n\n"
        "Отправь системе координаты в следующем формате:\n"
        "• <code>5 июля 1947, Roswell, USA</code>\n"
        "• <code>5 июля 1947, Розуэлл, США</code>\n"
        "• <code>5 июля 1947, 33.3943, -104.5230</code>\n\n"
        "Приветствуем уфологов, данная система расчета порталов рассчитывается по древней ведической науке, выявлена четкая работающая система, на данный момент она позволяет рассчитать как будущие порталы, так и удостоверить реальность прошлых.",
        parse_mode="HTML"
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        if "," not in text:
            raise ValueError("Формат: дата, место или координаты")

        parts = text.split(",")
        if len(parts) < 2:
            raise ValueError("Недостаточно данных")

        date_str = parts[0].strip()
        rest = ",".join(parts[1:]).strip()

        months = {
            # Русские месяцы
            "января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
            "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12,
            # Английские месяцы
            "january":1, "february":2, "march":3, "april":4, "may":5, "june":6,
            "july":7, "august":8, "september":9, "october":10, "november":11, "december":12,
            # Сокращения
            "jan":1, "feb":2, "mar":3, "apr":4, "may":5, "jun":6,
            "jul":7, "aug":8, "sep":9, "oct":10, "nov":11, "dec":12
        }
        date_parts = date_str.split()
        if len(date_parts) == 3:
            day, month_str, year = int(date_parts[0]), date_parts[1].lower().rstrip('.'), int(date_parts[2])
            month = months.get(month_str, 1)
            dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)  # 15:00 UTC — соответствует утру в США
        else:
            raise ValueError("Только формат: 5 июля 1947")

        # Проверяем, является ли rest координатами (широта, долгота)
        try:
            coords = [float(x.strip()) for x in rest.split(",")]
            if len(coords) != 2:
                raise ValueError("Координаты должны быть в формате: широта, долгота")
            lat, lon = coords
            loc = None  # Не используем geocoder для координат
        except ValueError:
            # Если не координаты — используем geocoder
            loc = geolocator.geocode(rest, timeout=10)
            if not loc:
                raise ValueError("Место не найдено")
            lat, lon = loc.latitude, loc.longitude

        result, details = classify_event(lat, lon, dt)
        await update.message.reply_text(f"{result}\n\n{details}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ {str(e)}\n\n"
            "📌 Примеры запросов:\n"
            "• 5 июля 1947, Roswell, USA\n"
            "• 5 июля 1947, Розуэлл, США\n"
            "• 5 июля 1947, 33.3943, -104.5230",
            parse_mode="HTML"
        )

if __name__ == "__main__":
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    app_bot = Application.builder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    # Запускаем HTTP-сервер в фоне
    from threading import Thread
    def run_flask():
        flask_app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
    thread = Thread(target=run_flask)
    thread.daemon = True
    thread.start()

    # Запускаем бота
    try:
        print("🚀 Запуск бота...")
        app_bot.run_polling()
    except KeyboardInterrupt:
        print("🛑 Бот остановлен вручную.")
    except Exception as e:
        print(f"💥 Критическая ошибка: {e}")
        import time
        while True:
            time.sleep(60)