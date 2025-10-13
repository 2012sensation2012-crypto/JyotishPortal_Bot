import os
import datetime
import pytz
from astral import LocationInfo
from astral.sun import sun
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import swisseph as swe
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from flask import Flask
from functools import lru_cache

# === ИНИЦИАЛИЗАЦИЯ ===
ephemeris_path = os.path.join(os.path.dirname(__file__), "ephemeris")
swe.set_ephe_path(ephemeris_path)
geolocator = Nominatim(user_agent="ufo_portal_bot")
tf = TimezoneFinder()
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "Bot is alive! 🛸"

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def get_kp_index(date):
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
    return "—"

def is_night(lat, lon, dt):
    try:
        tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
        local_tz = pytz.timezone(tz_str)
        local_dt = dt.astimezone(local_tz)
        city = LocationInfo("", "", tz_str, lat, lon)
        s = sun(city.observer, date=local_dt.date())
        return local_dt < s["dawn"] or local_dt > s["dusk"]
    except:
        return True

@lru_cache(maxsize=1000)
def classify_event_cached(lat, lon, date_str):
    dt = datetime.datetime.fromisoformat(date_str).replace(tzinfo=pytz.UTC)
    jd = swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute/60.0)
    sun_pos = swe.calc_ut(jd, swe.SUN)[0][0] % 360
    moon_pos = swe.calc_ut(jd, swe.MOON)[0][0] % 360
    rahu_pos = swe.calc_ut(jd, swe.MEAN_NODE)[0][0] % 360

    lon_360 = lon if lon >= 0 else 360 + lon
    rahu_diff = min(abs(lon_360 - rahu_pos), abs(lon_360 - rahu_pos + 360), abs(lon_360 - rahu_pos - 360))
    cond1 = rahu_diff <= 3
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
        event_type = "✅ Тип 1 (Геопортал)"
    elif (in_8th or in_12th) and cond3 and cond6:
        event_type = "🌤 Тип 2 (Атмосферный)"
    elif cond1 and (in_8th or in_12th or in_mula) and kp >= 6:
        event_type = "💥 Тип 4 (Аварийный)"
    elif cond5 and cond6:
        event_type = "👁️ Тип 5 (Наблюдательный)"
    else:
        event_type = "❌ Вне системы"

    details = (
        f"• Накшатра: {nakshatra}\n"
        f"• Дом Луны: {'8-й' if in_8th else '12-й' if in_12th else '—'}\n"
        f"• Rahu: {rahu_diff:.1f}° от долготы\n"
        f"• Kp: {kp} | Ночь: {'Да' if cond5 else 'Нет'}"
    )
    return event_type, details

def classify_event(lat, lon, dt):
    return classify_event_cached(lat, lon, dt.isoformat())

# === СЛОВАРЬ КЛЮЧЕВЫХ ЗОН ===
PLACE_SYNONYMS = {
    "Розуэлл": "Roswell, New Mexico",
    "Зона 51": "Rachel, Nevada",
    "Седона": "Sedona, Arizona",
    "Тунгуска": "60.9, 101.9",
    "Мачу-Пикчу": "Machu Picchu, Peru",
    "Наска": "Nazca, Peru",
    "Стоунхендж": "Stonehenge, UK",
    "Кайлас": "Mount Kailash, Tibet",
    "Бермуды": "25.0, -71.0",
    "Пирамиды": "Giza, Egypt",
}

MONTHS = {
    "января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
    "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
}

# === TELEGRAM ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌌 <b>Система Инопланетных Порталов v4.0</b>\n\n"
        "Отправьте:\n"
        "• <code>5 июля 1947, Розуэлл</code> — анализ события\n"
        "• <code>2026</code> — поиск порталов в году\n\n"
        "Поддерживаемые зоны: Розуэлл, Зона 51, Тунгуска, Мачу-Пикчу, Кайлас и др.",
        parse_mode="HTML"
    )

async def manual_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        if "," not in text:
            raise ValueError("Формат: <code>дата, место</code>")

        date_part, loc_part = [x.strip() for x in text.split(",", 1)]
        words = date_part.split()
        if len(words) != 3:
            raise ValueError("Формат: <code>5 июля 1947</code>")

        day = int(words[0])
        month = MONTHS.get(words[1].lower().rstrip('.'), 1)
        year = int(words[2])
        dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)

        for ru, en in PLACE_SYNONYMS.items():
            loc_part = loc_part.replace(ru, en)

        try:
            coords = [float(x) for x in loc_part.split(",")]
            if len(coords) != 2:
                raise ValueError()
            lat, lon = coords
        except:
            loc = geolocator.geocode(loc_part, timeout=10)
            if not loc:
                raise ValueError("Место не найдено")
            lat, lon = loc.latitude, loc.longitude

        event_type, _ = classify_event(lat, lon, dt)
        await update.message.reply_text(event_type, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(
            f"⚠️ {str(e)}\n\nПример: <code>5 июля 1947, Розуэлл</code>",
            parse_mode="HTML"
        )

async def search_portals_by_year(update: Update, context: ContextTypes.DEFAULT_TYPE, year: int):
    await update.message.reply_text(f"⏳ Поиск порталов в {year} году...")
    lat, lon = 33.3943, -104.5230  # Розуэлл
    results = []

    for month in range(1, 13):
        for day in range(1, 32):
            try:
                dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)
                event_type, details = classify_event(lat, lon, dt)
                if event_type != "❌ Вне системы":
                    results.append(f"<b>{day:02d}.{month:02d}.{year}</b>\n{event_type}\n{details}\n")
            except:
                continue

    if not results:
        await update.message.reply_text(f"❌ В {year} году порталы не обнаружены.")
        return

    full = "\n".join(results) + f"\n<i>({('Будущее' if year > datetime.datetime.now().year else 'Прошлое')} событие)</i>"
    for i in range(0, len(full), 4000):
        await update.message.reply_text(full[i:i+4000], parse_mode="HTML")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.isdigit() and len(text) == 4:
        await search_portals_by_year(update, context, int(text))
    else:
        await manual_search(update, context)

# === ЗАПУСК ===
if __name__ == "__main__":
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    from threading import Thread
    Thread(target=lambda: flask_app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000))), daemon=True).start()

    print("🚀 Бот запущен!")
    app.run_polling()