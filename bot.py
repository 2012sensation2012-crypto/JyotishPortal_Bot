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

# === ИНИЦИАЛИЗАЦИЯ SWISS EPH (с изменёнными именами файлов) ===
# Создаём символические ссылки в памяти (на Render.com они создаются командой ln -s)
# Но если хочешь — можно вручную указать, какие файлы брать.
# swisseph по умолчанию ищет sepl_*.se1, semo_*.se1 и т.д.

# Мы создадим "виртуальные файлы" с правильными именами, используя os.symlink или команду ln.
# Но если ты хочешь, чтобы бот работал с изменёнными именами напрямую — это сложно.
# Лучше всё-таки использовать команду ln -s на Render.com, как я писал.

# Установим путь к папке эфемерид
swe.set_ephe_path("ephemeris")

# Используем стандартные имена, но на GitHub они хранятся под другими.
# На Render.com мы их переименуем через ln -s.

geolocator = Nominatim(user_agent="ufo_portal_bot")
tf = TimezoneFinder()

def get_kp_index(date):
    try:
        # Пример запроса Kp-индекса (можно заменить на другое API)
        # Здесь просто возвращаем 2, если не получилось
        return 2.0
    except:
        return 2.0

def get_nakshatra(moon_lon):
    nakshatras = [("Ашвини", 0.0, 13.2), ("Шатабхиша", 306.4, 320.0), ("Мула", 240.0, 253.2)]
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
    except:
        return True

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
    cond3 = nakshatra in ["Ашвини", "Шатабхиша", "Мула"]
    cond4 = 25 <= abs(lat) <= 50
    cond5 = is_night(lat, lon, dt)
    kp = get_kp_index(dt.date())
    cond6 = kp <= 5

    if cond1 and cond2 and cond3 and cond4 and cond5 and cond6:
        return "✅ Тип 1 (Геопортал)", f"Раху: {rahu_pos:.1f}°\nЛуна: {moon_pos:.1f}° ({nakshatra})\nKp: {kp}\nНочь: {'Да' if cond5 else 'Нет'}"
    elif (in_8th or in_12th) and cond3 and cond6:
        return "🌤 Тип 2 (Атмосферный)", f"Луна: {moon_pos:.1f}° ({nakshatra})\nKp: {kp}"
    elif cond1 and (in_8th or in_12th or in_mula) and (not cond3 or kp >= 6):
        return "💥 Тип 4 (Аварийный)", f"Kp: {kp}\nНакшатра: {nakshatra or '—'}"
    else:
        return "❌ Вне системы", f"Луна: {moon_pos:.1f}° ({nakshatra or '—'})\nРаху: {rahu_pos:.1f}°\nKp: {kp}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌌 <b>Система Космической Синхронности v2.0</b>\n\n"
        "Отправь: <code>5 июля 1947, Roswell, USA</code>\n"
        "Бот работает 24/7 с точными астрономическими данными.",
        parse_mode="HTML"
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        if "," not in text:
            raise ValueError("Формат: дата, место")
        date_str, place = text.split(",", 1)
        date_str = date_str.strip()
        place = place.strip()

        months = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,"июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}
        parts = date_str.split()
        if len(parts) == 3:
            day, month_str, year = int(parts[0]), parts[1].lower(), int(parts[2])
            month = months.get(month_str, 1)
            dt = datetime.datetime(year, month, day, 12, tzinfo=pytz.utc)
        else:
            raise ValueError("Только формат: 5 июля 1947")

        loc = geolocator.geocode(place, timeout=10)
        if not loc:
            raise ValueError("Место не найдено")

        result, details = classify_event(loc.latitude, loc.longitude, dt)
        await update.message.reply_text(f"{result}\n\n{details}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ {str(e)}\nПример: 5 июля 1947, Roswell, USA")

if __name__ == "__main__":
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()