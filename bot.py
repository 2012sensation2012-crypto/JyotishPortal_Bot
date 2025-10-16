import os
import datetime
import pytz
import logging
import sys
import requests
from astral import LocationInfo
from astral.sun import sun
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import swisseph as swe
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

from functools import lru_cache
from collections import defaultdict
from flask import Flask, jsonify

# Добавлен импорт для jyotish
from jyotish import calculate_astrology

# === НАСТРОЙКА ЛОГИРОВАНИЯ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# === ИНИЦИАЛИЗАЦИЯ SWISS EPH ===
ephemeris_path = os.path.join(os.path.dirname(__file__), "ephemeris")
swe.set_ephe_path(ephemeris_path)

geolocator = Nominatim(user_agent="jyotishportal_bot")
tf = TimezoneFinder()

# Кэш координат городов
CITY_COORDS = {}
RUSSIAN_CITIES = [
    "Абакан", "Анадырь", "Архангельск", "Астрахань", "Барнаул", "Белгород",
    "Биробиджан", "Благовещенск", "Братск", "Брянск", "Владивосток", "Владикавказ",
    "Владимир", "Волгоград", "Вологда", "Воркута", "Воронеж", "Горно-Алтайск",
    "Грозный", "Екатеринбург", "Иваново", "Ижевск", "Иркутск", "Йошкар-Ола",
    "Казань", "Калининград", "Калуга", "Кемерово", "Киров", "Кишинёв",
    "Комсомольск-на-Амуре", "Кострома", "Краснодар", "Красноярск", "Курган", "Курск",
    "Кызыл", "Ленск", "Липецк", "Магадан", "Майкоп", "Махачкала", "Мещовск",
    "Минеральные Воды", "Мирный (Якутия)", "Москва", "Мурманск", "Набережные Челны",
    "Назрань", "Нальчик", "Нерюнгри", "Нижневартовск", "Нижний Новгород", "Новгород",
    "Новокузнецк", "Новосибирск", "Новый Уренгой", "Норильск", "Омск", "Оренбург",
    "Орёл", "Пенза", "Пермь", "Петрозаводск", "Петропавловск-Камчатский", "Псков",
    "Ростов-на-Дону", "Рязань", "Салехард", "Самара", "Санкт-Петербург", "Саранск",
    "Саратов", "Севастополь", "Симферополь", "Смоленск", "Сочи", "Ставрополь",
    "Станция Восток", "Станция Мирный", "Сургут", "Сыктывкар", "Тамбов", "Тверь",
    "Тикси", "Тольятти", "Томск", "Тула", "Тюмень", "Улан-Удэ", "Ульяновск", "Уфа",
    "Хабаровск", "Ханты-Мансийск", "Чебоксары", "Челябинск", "Череповец", "Черкесск",
    "Чита", "Элиста", "Южно-Сахалинск", "Якутск", "Ярославль"
]

# Предзагрузка координат
for city in RUSSIAN_CITIES:
    try:
        loc = geolocator.geocode(city, timeout=5)
        if loc:
            CITY_COORDS[city] = (loc.latitude, loc.longitude)
    except Exception as e:
        logger.warning(f"Не удалось загрузить координаты для {city}: {e}")

# Создаём Flask-сервер
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is alive! 🛸"

@flask_app.route('/health')
def health_check():
    return jsonify({"status": "ok", "service": "JyotishPortal_Bot"})

# Кэш для Kp-индекса (на 12 часов)
kp_cache = defaultdict(lambda: (None, 0))

def get_kp_index(date):
    current_time = datetime.datetime.now().timestamp()
    if date in kp_cache and current_time - kp_cache[date][1] < 43200:
        cached_value, _ = kp_cache[date]
        if cached_value is not None:
            return cached_value

    try:
        if date.year < 2000:
            return 2.0

        date_str = date.strftime("%Y%m%d")
        # 🔥 ИСПРАВЛЕНО: убран пробел в URL!
        url = f"https://xras.ru/txt/kp_BPE3_{date_str}.json"

        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logger.warning(f"xras.ru returned {response.status_code} for {date_str}")
            return 2.0

        data = response.json()
        target_date_str = date.strftime("%Y-%m-%d")

        for day_data in data.get("data", []):
            if day_data.get("time") == target_date_str:
                kp_values = []
                for key, val in day_data.items():
                    if key.startswith("h") and len(key) == 3:
                        if val == "null":
                            continue
                        try:
                            kp_val = float(val)
                            if 0 <= kp_val <= 9:
                                kp_values.append(kp_val)
                        except:
                            continue
                if kp_values:
                    avg_kp = sum(kp_values) / len(kp_values)
                    kp_cache[date] = (avg_kp, current_time)
                    return avg_kp

        logger.warning(f"Kp не найден для {target_date_str}")
        kp_cache[date] = (2.0, current_time)
        return 2.0

    except Exception as e:
        logger.error(f"Ошибка Kp: {e}")
        kp_cache[date] = (2.0, current_time)
        return 2.0

def is_night(lat, lon, dt):
    try:
        tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
        local_tz = pytz.timezone(tz_str)
        local_dt = dt.astimezone(local_tz)
        city = LocationInfo("", "", tz_str, lat, lon)
        s = sun(city.observer, date=local_dt.date(), elevation=0)
        sunrise = s.get('sunrise')
        sunset = s.get('sunset')
        if sunrise is None or sunset is None:
            return True
        return local_dt < sunrise or local_dt > sunset
    except:
        return True

@lru_cache(maxsize=365)
def get_event_analysis(lat, lon, dt):
    astro_data = calculate_astrology(lat, lon, dt)
    moon_pos = astro_data["moon"]
    rahu_pos = astro_data["rahu"]
    nakshatra = astro_data["nakshatra"]
    moon_house = astro_data["moon_house"]
    sun_pos = astro_data["sun"]
    angle = (moon_pos - sun_pos) % 360
    lon_360 = lon if lon >= 0 else 360 + lon
    rahu_diff = min(abs(lon_360 - rahu_pos), abs(lon_360 - rahu_pos + 360), abs(lon_360 - rahu_pos - 360))
    cond1 = rahu_diff <= 3
    in_8th = 210 <= angle <= 240
    in_12th = 330 <= angle <= 360
    in_mula = nakshatra == "Мула"
    cond2 = in_8th or in_12th or in_mula
    cond3 = nakshatra in ["Ашвини", "Шатабхиша", "Мула", "Уттара Бхадрапада", 
                          "Пурва Ашадха", "Уттара Ашадха", "Шравана", 
                          "Пурва Фалгуни", "Уттара Фалгуни"]
    cond4 = 25 <= abs(lat) <= 50
    cond5 = is_night(lat, lon, dt)
    kp = get_kp_index(dt.date())
    cond6 = kp <= 5

    if (cond1 or cond3 or cond5) and cond2 and cond4 and cond6:
        return "✅ Тип 1 (Геопортал)"
    elif (in_8th or in_12th) and cond3 and cond6:
        return "🌤 Тип 2 (Атмосферный)"
    elif cond1 and (in_8th or in_12th or in_mula) and kp >= 6:
        return "💥 Тип 4 (Аварийный)"
    elif cond6 and cond5 and (cond1 or cond3):
        return "👁️ Тип 5 (Наблюдательный)"
    elif cond5 and cond6 and (cond1 or cond3) and is_historical_contact(lat, lon, dt):
        return "👽 Тип 6 (Контактный)"
    else:
        return "❌ Вне системы"

def is_historical_contact(lat, lon, dt):
    historical_events = [
        {"lat": 33.3943, "lon": -104.5230, "date": "1947-07-05"},
        {"lat": 52.2392, "lon": -2.6190, "date": "1980-12-26"},
        {"lat": -33.9000, "lon": 18.4200, "date": "1994-01-21"}
    ]
    event_date = dt.strftime("%Y-%m-%d")
    for event in historical_events:
        if abs(event["lat"] - lat) < 0.1 and abs(event["lon"] - lon) < 0.1 and event["date"] == event_date:
            return True
    return False

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def build_city_keyboard(offset=0, limit=10):
    buttons = []
    cities = RUSSIAN_CITIES[offset:offset+limit]
    for i in range(0, len(cities), 2):
        row = [
            InlineKeyboardButton(cities[i], callback_data=f"city:{cities[i]}")
        ]
        if i+1 < len(cities):
            row.append(InlineKeyboardButton(cities[i+1], callback_data=f"city:{cities[i+1]}"))
        buttons.append(row)
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"cities:{max(0, offset-10)}"))
    if offset + limit < len(RUSSIAN_CITIES):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"cities:{offset+10}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔚 Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def build_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Геопортал ✅", callback_data="type:1")],
        [InlineKeyboardButton("Атмосферный 🌤", callback_data="type:2")],
        [InlineKeyboardButton("Аварийный 💥", callback_data="type:4")],
        [InlineKeyboardButton("🔚 Отмена", callback_data="cancel")]
    ])

def build_year_keyboard():
    current_year = datetime.datetime.now().year
    years = list(range(current_year - 3, current_year + 4))
    buttons = []
    for i in range(0, len(years), 3):
        row = [InlineKeyboardButton(str(y), callback_data=f"year:{y}") for y in years[i:i+3]]
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔚 Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def build_search_mode_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 По одному месяцу", callback_data="mode:single")],
        [InlineKeyboardButton("📆 По трём месяцам (квартал)", callback_data="mode:quarter")],
        [InlineKeyboardButton("🔚 Отмена", callback_data="cancel")]
    ])

def build_single_month_keyboard():
    months = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    buttons = []
    for i in range(0, 12, 2):
        buttons.append([
            InlineKeyboardButton(months[i], callback_data=f"month:{i+1}"),
            InlineKeyboardButton(months[i+1], callback_data=f"month:{i+2}")
        ])
    buttons.append([InlineKeyboardButton("🔚 Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def build_quarter_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Янв–Мар", callback_data="quarter:1")],
        [InlineKeyboardButton("Апр–Июн", callback_data="quarter:2")],
        [InlineKeyboardButton("Июл–Сен", callback_data="quarter:3")],
        [InlineKeyboardButton("Окт–Дек", callback_data="quarter:4")],
        [InlineKeyboardButton("🔚 Отмена", callback_data="cancel")]
    ])

def build_results_keyboard(results, page=0, per_page=10, mode="single", current_month=None, current_quarter=None, year=None):
    total = len(results)
    start = page * per_page
    end = min(start + per_page, total)
    buttons = []
    if start > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page:{page-1}"))
    if end < total:
        buttons.append(InlineKeyboardButton("➡️ Вперёд", callback_data=f"page:{page+1}"))
    if mode == "single":
        if current_month == 12:
            next_month = 1
            next_year = year + 1
        else:
            next_month = current_month + 1
            next_year = year
        buttons.append(InlineKeyboardButton("🔄 След. месяц", callback_data=f"next_month:{next_year}:{next_month}"))
    elif mode == "quarter":
        next_quarter = current_quarter + 1 if current_quarter < 4 else 1
        next_year = year + 1 if current_quarter == 4 else year
        buttons.append(InlineKeyboardButton("🔄 След. квартал", callback_data=f"next_quarter:{next_year}:{next_quarter}"))
    buttons.append(InlineKeyboardButton("🔚 Завершить", callback_data="cancel"))
    return InlineKeyboardMarkup([buttons] if buttons else [[InlineKeyboardButton("🔚 Завершить", callback_data="cancel")]])

async def analyze_period(city, portal_type, year, months):
    coords = CITY_COORDS.get(city)
    if not coords:
        raise Exception("Координаты города не найдены")
    lat, lon = coords
    results = []
    for month in months:
        for day in range(1, 32):
            try:
                dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)
                event_type = get_event_analysis(lat, lon, dt)
                if (portal_type == 1 and "Тип 1" in event_type) or \
                   (portal_type == 2 and "Тип 2" in event_type) or \
                   (portal_type == 4 and "Тип 4" in event_type):
                    results.append(f"{day:02d}.{month:02d}.{year} — {event_type}")
            except:
                continue
    return results

# === ОСНОВНЫЕ ОБРАБОТЧИКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌍 <b>Система анализа порталов</b>\nВыберите город:",
        reply_markup=build_city_keyboard(),
        parse_mode="HTML"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_data = context.user_data

    if data == "cancel":
        await query.edit_message_text("❌ Операция завершена. Отправьте /start для нового поиска.")
        return

    if data.startswith("cities:"):
        offset = int(data.split(":")[1])
        await query.edit_message_text(
            "Выберите город:",
            reply_markup=build_city_keyboard(offset)
        )
        return

    if data.startswith("city:"):
        city = data.split(":", 1)[1]
        user_data.update({"city": city, "state": "select_type"})
        await query.edit_message_text(
            f"Выбран город: <b>{city}</b>\nВыберите тип портала:",
            reply_markup=build_type_keyboard(),
            parse_mode="HTML"
        )
        return

    if data.startswith("type:"):
        portal_type = int(data.split(":")[1])
        user_data["portal_type"] = portal_type
        await query.edit_message_text(
            "Выберите режим поиска:",
            reply_markup=build_search_mode_keyboard()
        )
        return

    if data.startswith("mode:"):
        mode = data.split(":")[1]
        user_data["search_mode"] = mode
        if mode == "single":
            await query.edit_message_text("Выберите месяц:", reply_markup=build_single_month_keyboard())
        else:
            await query.edit_message_text("Выберите квартал:", reply_markup=build_quarter_keyboard())
        return

    if data.startswith("year:"):
        year = int(data.split(":")[1])
        user_data["year"] = year
        mode = user_data.get("search_mode")
        if mode == "single":
            await query.edit_message_text("Выберите месяц:", reply_markup=build_single_month_keyboard())
        else:
            await query.edit_message_text("Выберите квартал:", reply_markup=build_quarter_keyboard())
        return

    if data.startswith("month:"):
        month = int(data.split(":")[1])
        user_data["month"] = month
        city = user_data["city"]
        portal_type = user_data["portal_type"]
        year = user_data.get("year")
        if not year:
            await query.edit_message_text("Выберите год:", reply_markup=build_year_keyboard())
            return
        try:
            results = await analyze_period(city, portal_type, year, [month])
            user_data.update({"results": results, "page": 0, "mode": "single"})
            await show_results(query, user_data, mode="single", current_month=month, year=year)
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        return

    if data.startswith("quarter:"):
        quarter = int(data.split(":")[1])
        quarters = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}
        months = quarters[quarter]
        user_data["quarter"] = quarter
        user_data["months"] = months
        city = user_data["city"]
        portal_type = user_data["portal_type"]
        year = user_data.get("year")
        if not year:
            await query.edit_message_text("Выберите год:", reply_markup=build_year_keyboard())
            return
        try:
            results = await analyze_period(city, portal_type, year, months)
            user_data.update({"results": results, "page": 0, "mode": "quarter"})
            await show_results(query, user_data, mode="quarter", current_quarter=quarter, year=year)
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        return

    if data.startswith("page:"):
        page = int(data.split(":")[1])
        user_data["page"] = max(0, page)
        mode = user_data.get("mode", "single")
        current_month = user_data.get("month")
        current_quarter = user_data.get("quarter")
        year = user_data.get("year")
        await show_results(query, user_data, mode=mode, current_month=current_month, current_quarter=current_quarter, year=year)
        return

    if data.startswith("next_month:"):
        parts = data.split(":")
        year = int(parts[1])
        month = int(parts[2])
        user_data.update({"year": year, "month": month})
        city = user_data["city"]
        portal_type = user_data["portal_type"]
        try:
            results = await analyze_period(city, portal_type, year, [month])
            user_data.update({"results": results, "page": 0, "mode": "single"})
            await show_results(query, user_data, mode="single", current_month=month, year=year)
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        return

    if data.startswith("next_quarter:"):
        parts = data.split(":")
        year = int(parts[1])
        quarter = int(parts[2])
        quarters = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}
        months = quarters[quarter]
        user_data.update({"year": year, "quarter": quarter, "months": months})
        city = user_data["city"]
        portal_type = user_data["portal_type"]
        try:
            results = await analyze_period(city, portal_type, year, months)
            user_data.update({"results": results, "page": 0, "mode": "quarter"})
            await show_results(query, user_data, mode="quarter", current_quarter=quarter, year=year)
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        return

async def show_results(query, user_data, mode, current_month=None, current_quarter=None, year=None):
    results = user_data.get("results", [])
    page = user_data.get("page", 0)
    per_page = 10
    start = page * per_page
    end = start + per_page
    chunk = results[start:end]

    if results:
        text = f"📅 Результаты ({start+1}–{min(end, len(results))} из {len(results)}):\n\n" + "\n".join(chunk)
    else:
        if mode == "single":
            text = f"❌ Порталы не найдены в {current_month}.{year}."
        else:
            q_names = {1: "Янв–Мар", 2: "Апр–Июн", 3: "Июл–Сен", 4: "Окт–Дек"}
            text = f"❌ Порталы не найдены в {q_names.get(current_quarter, 'квартале')} {year}."

    reply_markup = build_results_keyboard(
        results, page=page, mode=mode,
        current_month=current_month, current_quarter=current_quarter, year=year
    )
    await query.edit_message_text(text, reply_markup=reply_markup)

# === РУЧНОЙ ПОИСК (оставлен как есть) ===
async def manual_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (оставь свою реализацию без изменений)
    pass

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        "1️⃣ Отправьте /start\n"
        "2️⃣ Следуйте кнопкам\n"
        "3️⃣ Используйте ручной поиск: <code>5 июля 2000, Roswell, USA</code>\n\n"
        "Данные Kp-индекса доступны с 2000 года.",
        parse_mode="HTML"
    )

# === ЗАПУСК ===
if __name__ == "__main__":
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Regex(r'\d+\s+\w+,\s+[\w\s]+'), manual_search))

    # Flask + heartbeat (оставь как есть)
    from threading import Thread
    def run_flask():
        flask_app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
    Thread(target=run_flask, daemon=True).start()

    def run_heartbeat():
        import asyncio, time
        while True:
            time.sleep(300)
            print("heartbeat")
    Thread(target=run_heartbeat, daemon=True).start()

    logger.info("🚀 Бот запущен (INLINE-РЕЖИМ).")
    app.run_polling()