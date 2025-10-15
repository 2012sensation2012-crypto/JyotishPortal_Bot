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
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    ContextTypes, 
    filters,
    ConversationHandler
)

from functools import lru_cache
from collections import defaultdict
from flask import Flask, jsonify, request

# Добавлен импорт для jyotish
# Убедись, что файл jyotish.py лежит в том же каталоге
try:
    from jyotish import calculate_astrology
except Exception as e:
    logging.error(f"Не удалось импортировать jyotish: {e}")
    def calculate_astrology(lat, lon, dt):
        return {
            "moon": 0, "rahu": 0, "nakshatra": "—", "moon_house": 1,
            "houses": [], "sun": 0, "moon_sign": "—"
        }

# Состояния бота
(STATE_START, STATE_SELECT_CITY, STATE_SELECT_TYPE, STATE_ENTER_YEAR, STATE_SELECT_MONTH_BLOCK, STATE_ENTER_MONTH, STATE_SHOW_RESULTS) = range(7)

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
if os.path.exists(ephemeris_path):
    swe.set_ephe_path(ephemeris_path)
else:
    logger.warning("Папка ephemeris не найдена. swisseph может не работать.")

geolocator = Nominatim(user_agent="ufo_portal_bot")
tf = TimezoneFinder()

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

def get_region_code(lat, lon):
    regions = [
        {"code": "MSK1", "lat": 55.7558, "lon": 37.6173, "name": "Москва"},
        {"code": "SPB1", "lat": 59.9343, "lon": 30.3351, "name": "Санкт-Петербург"},
        {"code": "NSK1", "lat": 55.0415, "lon": 82.9343, "name": "Новосибирск"},
        {"code": "EKB1", "lat": 56.8380, "lon": 60.6057, "name": "Екатеринбург"},
        {"code": "NNG1", "lat": 56.8584, "lon": 60.6077, "name": "Нижний Новгород"},
        {"code": "KZN1", "lat": 55.7961, "lon": 49.1063, "name": "Казань"},
        {"code": "KRD1", "lat": 45.0355, "lon": 38.9760, "name": "Краснодар"},
        {"code": "KRK1", "lat": 56.0184, "lon": 92.8679, "name": "Красноярск"},
        {"code": "VVO1", "lat": 48.4943, "lon": 135.0687, "name": "Владивосток"}
    ]
    
    min_distance = float('inf')
    best_match = "MSK1"
    
    for region in regions:
        d_lat = abs(region["lat"] - lat)
        d_lon = abs(region["lon"] - lon)
        distance = d_lat + d_lon
        
        if distance < min_distance:
            min_distance = distance
            best_match = region["code"]
    
    return best_match

def get_kp_index(date):
    current_time = datetime.datetime.now().timestamp()
    
    if date in kp_cache and current_time - kp_cache[date][1] < 43200:
        cached_value, _ = kp_cache[date]
        if cached_value is not None:
            return cached_value
    
    try:
        region_code = "BPE3"
        
        if date.year < 2000:
            return 2.0
        
        date_str = date.strftime("%Y%m%d")
        url = f"https://xras.ru/txt/kp_{region_code}_{date_str}.json"  # исправлено: убран пробел
        
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

        logger.warning(f"Kp-индекс не найден для {target_date_str}")
        kp_cache[date] = (2.0, current_time)
        return 2.0

    except Exception as e:
        logger.error(f"Ошибка получения Kp-индекса: {e}")
        kp_cache[date] = (2.0, current_time)
        return 2.0

def is_night(lat, lon, dt):
    try:
        tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
        local_tz = pytz.timezone(tz_str)
        local_dt = dt.astimezone(local_tz)
        
        city = LocationInfo("", "", tz_str, lat, lon)
        s = sun(city.observer, date=local_dt.date(), elevation=0)
        
        sunrise = s.get('sunrise', None)
        sunset = s.get('sunset', None)
        
        if sunrise is None or sunset is None:
            return True
            
        return local_dt < sunrise or local_dt > sunset

    except Exception as e:
        logger.error(f"Ошибка определения ночи: {e}")
        return True

def get_country(lat, lon):
    try:
        location = geolocator.reverse(f"{lat}, {lon}", language='en', timeout=5)
        if location and 'address' in location.raw:
            country = location.raw['address'].get('country', '—')
            country_map = {
                "United States": "США",
                "Russia": "Россия",
                "United Kingdom": "Великобритания",
                "Germany": "Германия",
                "France": "Франция",
                "Canada": "Канада",
                "Mexico": "Мексика",
                "Japan": "Япония",
                "China": "Китай",
                "India": "Индия",
                "Brazil": "Бразилия",
                "Australia": "Австралия",
                "Ukraine": "Украина",
                "Turkey": "Турция",
                "Italy": "Италия",
                "Spain": "Испания"
            }
            return country_map.get(country, country)
    except Exception as e:
        logger.warning(f"Не удалось определить страну: {e}")
    return "—"

@lru_cache(maxsize=365)
def get_event_analysis(lat, lon, dt):
    astro_data = calculate_astrology(lat, lon, dt)
    
    moon_pos = astro_data["moon"]
    rahu_pos = astro_data["rahu"]
    nakshatra = astro_data["nakshatra"]
    moon_house = astro_data["moon_house"]
    houses = astro_data["houses"]
    sun_pos = astro_data["sun"]
    angle = (moon_pos - sun_pos) % 360
    
    lon_360 = lon if lon >= 0 else 360 + lon
    rahu_diff = min(
        abs(lon_360 - rahu_pos),
        abs(lon_360 - rahu_pos + 360),
        abs(lon_360 - rahu_pos - 360)
    )
    
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
        event_type = "✅ Тип 1 (Геопортал)"
    elif (in_8th or in_12th) and cond3 and cond6:
        event_type = "🌤 Тип 2 (Атмосферный)"
    elif cond1 and (in_8th or in_12th or in_mula) and kp >= 6:
        event_type = "💥 Тип 4 (Аварийный)"
    elif cond6 and cond5 and (cond1 or cond3):
        event_type = "👁️ Тип 5 (Наблюдательный)"
    elif cond5 and cond6 and (cond1 or cond3) and is_historical_contact(lat, lon, dt):
        event_type = "👽 Тип 6 (Контактный)"
    else:
        event_type = "❌ Вне системы"

    details = (
        f"• Координаты: {lat:.4f}, {lon:.4f}\n"
        f"• Накшатра: {nakshatra or '—'}\n"
        f"• Дом Луны: {moon_house or '—'}\n"
        f"• Знак Луны: {astro_data['moon_sign']}\n"
        f"• Раху от долготы: {rahu_diff:.1f}°\n"
        f"• Kp-индекс: {kp}\n"
        f"• Ночь: {'Да' if cond5 else 'Нет'}"
    )
    return event_type, details

def is_historical_contact(lat, lon, dt):
    historical_events = [
        {"lat": 33.3943, "lon": -104.5230, "date": "1947-07-05"},
        {"lat": 52.2392, "lon": -2.6190, "date": "1980-12-26"},
        {"lat": -33.9000, "lon": 18.4200, "date": "1994-01-21"}
    ]
    
    event_date = dt.strftime("%Y-%m-%d")
    
    for event in historical_events:
        lat_diff = abs(event["lat"] - lat)
        lon_diff = abs(event["lon"] - lon)
        if lat_diff < 0.1 and lon_diff < 0.1 and event["date"] == event_date:
            return True
    return False

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

# === ОБРАБОТЧИКИ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for i in range(0, min(50, len(RUSSIAN_CITIES)), 2):
        row = []
        row.append(KeyboardButton(RUSSIAN_CITIES[i]))
        if i+1 < len(RUSSIAN_CITIES):
            row.append(KeyboardButton(RUSSIAN_CITIES[i+1]))
        keyboard.append(row)
    
    reply_markup = ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True,
        one_time_keyboard=False
    )
    
    await update.message.reply_text(
        "🌍 <b>Система анализа порталов</b>\n\n"
        "Выберите город России для анализа:\n\n"
        "Данные Kp-индекса доступны с 2000 года.\n"
        "Система найдет порталы по вашим критериям.\n\n"
        "Всего доступно городов: " + str(len(RUSSIAN_CITIES)),
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    return STATE_SELECT_CITY

async def select_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    
    if user_input not in RUSSIAN_CITIES:
        keyboard = []
        for i in range(0, min(50, len(RUSSIAN_CITIES)), 2):
            row = []
            row.append(KeyboardButton(RUSSIAN_CITIES[i]))
            if i+1 < len(RUSSIAN_CITIES):
                row.append(KeyboardButton(RUSSIAN_CITIES[i+1]))
            keyboard.append(row)
        
        reply_markup = ReplyKeyboardMarkup(
            keyboard, 
            resize_keyboard=True,
            one_time_keyboard=False
        )
        
        await update.message.reply_text(
            "❌ Не удалось определить город. Пожалуйста, выберите из предложенных вариантов.",
            reply_markup=reply_markup
        )
        return STATE_SELECT_CITY
    
    context.user_data['city'] = user_input
    
    keyboard = [
        [KeyboardButton("Геопортал ✅"), KeyboardButton("Атмосферный 🌤")],
        [KeyboardButton("Аварийный 💥")]
    ]
    
    reply_markup = ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True,
        one_time_keyboard=False
    )
    
    await update.message.reply_text(
        f"🔍 Выбран город: <b>{user_input}</b>\n\n"
        "Теперь выберите тип портала для поиска:\n\n"
        "• Геопортал (Тип 1)\n"
        "• Атмосферный (Тип 2)\n"
        "• Аварийный (Тип 4)\n\n"
        "Система покажет только указанный тип портала.",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    return STATE_SELECT_TYPE

async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower()
    portal_type = None
    
    if "геопортал" in user_input or "1" in user_input or "✅" in user_input:
        portal_type = 1
    elif "атмосферный" in user_input or "2" in user_input or "🌤" in user_input:
        portal_type = 2
    elif "аварийный" in user_input or "4" in user_input or "💥" in user_input:
        portal_type = 4
    else:
        await update.message.reply_text(
            "❌ Не удалось определить тип портала. Пожалуйста, выберите из предложенных вариантов.",
            reply_markup=ReplyKeyboardMarkup(
                [
                    [KeyboardButton("Геопортал ✅"), KeyboardButton("Атмосферный 🌤")],
                    [KeyboardButton("Аварийный 💥")]
                ],
                resize_keyboard=True
            )
        )
        return STATE_SELECT_TYPE
    
    context.user_data['portal_type'] = portal_type
    
    await update.message.reply_text(
        "📅 Укажите год для анализа (только с 2000 года):\n\n"
        "Данные Kp-индекса доступны только с 2000 года.",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return STATE_ENTER_YEAR

async def enter_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        year = int(update.message.text)
        if year < 2000:
            await update.message.reply_text(
                "❌ Некорректный год. Данные Kp-индекса доступны только с 2000 года.\n"
                "Введите год в диапазоне 2000-2100."
            )
            return STATE_ENTER_YEAR
        
        if year > 2100:
            await update.message.reply_text(
                "❌ Некорректный год. Введите год в диапазоне 2000-2100."
            )
            return STATE_ENTER_YEAR
        
        context.user_data['year'] = year
        
        keyboard = [
            [KeyboardButton("🗓 Январь–Июнь")],
            [KeyboardButton("🗓 Июль–Декабрь")]
        ]
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"📅 Год {year} выбран.\n"
            "Выберите блок месяцев:",
            reply_markup=reply_markup
        )
        
        return STATE_SELECT_MONTH_BLOCK
    
    except ValueError:
        await update.message.reply_text("❌ Введите корректный год (например, 2025).")
        return STATE_ENTER_YEAR

async def select_month_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    
    if "Январь–Июнь" in user_input:
        months = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь"]
        month_nums = [1, 2, 3, 4, 5, 6]
    elif "Июль–Декабрь" in user_input:
        months = ["Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
        month_nums = [7, 8, 9, 10, 11, 12]
    else:
        await update.message.reply_text("❌ Не удалось определить блок. Пожалуйста, выберите из предложенных вариантов.")
        return STATE_SELECT_MONTH_BLOCK
    
    keyboard = []
    for i in range(0, len(months), 2):
        row = []
        row.append(KeyboardButton(months[i]))
        if i+1 < len(months):
            row.append(KeyboardButton(months[i+1]))
        keyboard.append(row)
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "📅 Выберите месяц:",
        reply_markup=reply_markup
    )
    
    context.user_data['month_options'] = dict(zip(months, month_nums))
    return STATE_ENTER_MONTH

async def enter_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🔒 Добавлена проверка целостности данных
    if not context.user_data.get('city') or context.user_data.get('portal_type') is None or not context.user_data.get('year'):
        await update.message.reply_text("❌ Произошла ошибка. Пожалуйста, начните заново с /start.")
        return ConversationHandler.END

    try:
        month_name = update.message.text
        month_nums = context.user_data.get('month_options', {})
        month = month_nums.get(month_name)
        
        if month is None:
            await update.message.reply_text("❌ Некорректный месяц. Пожалуйста, выберите из предложенных вариантов.")
            return STATE_ENTER_MONTH
        
        context.user_data['month'] = month
        
        city = context.user_data.get('city')
        portal_type = context.user_data.get('portal_type')
        year = context.user_data.get('year')
        
        try:
            loc = geolocator.geocode(city, timeout=10)
            if not loc:
                await update.message.reply_text("❌ Не удалось определить координаты города.")
                return ConversationHandler.END
            lat, lon = loc.latitude, loc.longitude
        except Exception as e:
            logger.error(f"Ошибка получения координат для {city}: {e}")
            await update.message.reply_text("❌ Не удалось определить координаты города.")
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"⏳ Начинаю анализ месяца {month}.{year} для {city}...\n\n"
            "Это может занять несколько минут."
        )
        
        results = []
        for day in range(1, 32):
            try:
                dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)
                event_type, _ = get_event_analysis(lat, lon, dt)
                
                if (portal_type == 1 and "Тип 1" in event_type) or \
                   (portal_type == 2 and "Тип 2" in event_type) or \
                   (portal_type == 4 and "Тип 4" in event_type):
                    results.append(f"{day:02d}.{month:02d}.{year} — {event_type}")
            except:
                continue
        
        context.user_data['results'] = results
        context.user_data['current_page'] = 0
        
        await show_results(update, context)
        return STATE_SHOW_RESULTS
    
    except Exception as e:
        logger.error(f"Ошибка в enter_month: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова.")
        return ConversationHandler.END

async def show_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = context.user_data.get('results', [])
    page = context.user_data.get('current_page', 0)
    per_page = 10
    
    if not results:
        await update.message.reply_text("❌ Порталы не найдены.")
        return STATE_ENTER_YEAR
    
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_results = results[start_idx:end_idx]
    
    full = "\n".join(page_results)
    
    keyboard = []
    if start_idx > 0:
        keyboard.append([KeyboardButton("⬅️ Предыдущие дни")])
    if end_idx < len(results):
        keyboard.append([KeyboardButton("➡️ Следующие дни")])
    keyboard.append([KeyboardButton("🔄 Следующий месяц")])
    keyboard.append([KeyboardButton("🔚 Завершить")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"📅 Результаты ({start_idx + 1}–{min(end_idx, len(results))} из {len(results)}):\n\n{full}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def next_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_page'] += 1
    await show_results(update, context)
    return STATE_SHOW_RESULTS

async def prev_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_page'] -= 1
    await show_results(update, context)
    return STATE_SHOW_RESULTS

async def next_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_month = context.user_data.get('month')
    current_year = context.user_data.get('year')

    if current_month is None or current_year is None:
        await update.message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, начните заново с /start.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    if current_month == 12:
        new_month = 1
        new_year = current_year + 1
    else:
        new_month = current_month + 1
        new_year = current_year

    context.user_data['month'] = new_month
    context.user_data['year'] = new_year

    await enter_month(update, context)
    return STATE_ENTER_MONTH

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Операция отменена. Чтобы начать заново, отправьте /start",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def manual_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info(f"Получен запрос: {update.message.text}")
        text = update.message.text.strip()
        if "," not in text:
            raise ValueError("Формат: дата, место или координаты")

        parts = text.split(",", 1)
        if len(parts) < 2:
            raise ValueError("Нужно: дата, место")

        date_str = parts[0].strip()
        rest = parts[1].strip()

        months = {
            "января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
            "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12,
            "january":1, "february":2, "march":3, "april":4, "may":5, "june":6,
            "july":7, "august":8, "september":9, "october":10, "november":11, "december":12,
            "jan":1, "feb":2, "mar":3, "apr":4, "may":5, "jun":6,
            "jul":7, "aug":8, "sep":9, "oct":10, "nov":11, "dec":12
        }
        date_parts = date_str.split()
        if len(date_parts) == 3:
            day = int(date_parts[0])
            month_str = date_parts[1].lower().rstrip('.')
            year = int(date_parts[2])
            month = months.get(month_str, 1)
            if month == 1 and month_str not in months:
                raise ValueError("Неизвестный месяц")
            dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)
        else:
            raise ValueError("Формат: 5 июля 1947")

        if year < 2000:
            await update.message.reply_text(
                "❌ Данные Kp-индекса доступны только с 2000 года.\n"
                "Пожалуйста, укажите год после 2000."
            )
            return

        place_synonyms = {
            "Розуэлл": "Roswell",
            "США": "USA",
            "Москва": "Moscow",
            "Санкт-Петербург": "Saint Petersburg",
            # ... (остальное можно оставить или убрать для упрощения)
        }

        for key, value in place_synonyms.items():
            rest = rest.replace(key, value)

        try:
            coords = [float(x.strip()) for x in rest.split(",")]
            if len(coords) != 2:
                raise ValueError()
            lat, lon = coords
        except:
            loc = geolocator.geocode(rest, timeout=10)
            if not loc:
                raise ValueError("Место не найдено")
            lat, lon = loc.latitude, loc.longitude

        event_type, details = get_event_analysis(lat, lon, dt)
        logger.info(f"Результат: {event_type}")
        await update.message.reply_text(f"{event_type}\n{details}", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в manual_search: {e}")
        await update.message.reply_text(
            f"⚠️ {str(e)}\n\nПримеры:\n"
            "• <code>5 июля 2000, Roswell, USA</code>\n"
            "• <code>5 июля 2000, 33.3943, -104.5230</code>",
            parse_mode="HTML"
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        "1️⃣ Отправьте <b>год</b>:\n"
        "   → <code>2025</code>\n"
        "   → Получите список всех порталов в этом году с указанием точки анализа.\n\n"
        "2️⃣ Отправьте <b>дату и место</b>:\n"
        "   → <code>5 июля 2000, Roswell, USA</code>\n"
        "   → Получите полный джйотиш-анализ события.\n\n"
        "3️⃣ Используйте команды:\n"
        "   → <code>/start</code> — начать работу\n"
        "   → <code>/help</code> — эта справка\n\n"
        "Данные Kp-индекса доступны только с 2000 года.",
        parse_mode="HTML"
    )

# === ЗАПУСК (ВЕБХУКИ ДЛЯ REPLIT) ===

if __name__ == "__main__":
    import time
    from threading import Thread
    from dotenv import load_dotenv
    load_dotenv()  # загружает .env

    TOKEN = os.environ["TELEGRAM_TOKEN"]
    WEBHOOK_URL = f"https://{os.environ.get('REPL_SLUG')}.{os.environ.get('REPL_OWNER')}.repl.co"

    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_SELECT_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_city)],
            STATE_SELECT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_type)],
            STATE_ENTER_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_year)],
            STATE_SELECT_MONTH_BLOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_month_block)],
            STATE_ENTER_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_month)],
            STATE_SHOW_RESULTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"➡️"), next_days),
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"⬅️"), prev_days),
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"🔄"), next_month),
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"🔚"), cancel)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'\d+\s+\w+,\s+[\w\s]+'), manual_search))

    @flask_app.route(f'/{TOKEN}', methods=['POST'])
    def webhook():
        update = Update.de_json(request.get_json(force=True), app.bot)
        app.update_queue.put_nowait(update)
        return 'OK'

    def set_webhook():
        time.sleep(2)
        app.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}")
        logger.info(f"✅ Вебхук установлен на {WEBHOOK_URL}/{TOKEN}")

    Thread(target=set_webhook).start()
    flask_app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))