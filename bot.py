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
from flask import Flask, jsonify

# Добавлен импорт для jyotish
from jyotish import calculate_astrology

# Состояния бота
(STATE_START, STATE_SELECT_TYPE, STATE_ENTER_YEAR) = range(3)

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

def get_kp_index(date):
    """Получает реальный Kp-индекс из API NOAA"""
    current_time = datetime.datetime.now().timestamp()
    
    # Проверяем кэш
    if date in kp_cache and current_time - kp_cache[date][1] < 43200:  # 12 часов
        cached_value, _ = kp_cache[date]
        if cached_value is not None:
            return cached_value
    
    try:
        # Исправленный URL (без пробела в конце)
        response = requests.get(
            "https://services.swpc.noaa.gov/json/planetary-kp.json",
            timeout=10
        )
        
        if response.status_code != 200:
            logger.warning(f"API NOAA недоступен (код {response.status_code})")
            return 2.0  # Возвращаем среднее значение при проблемах с API
        
        data = response.json()
        date_str = date.strftime("%Y-%m-%d")
        
        # Поиск данных для нужной даты
        for entry in data:
            time_tag = entry.get("time_tag", "")
            if time_tag.startswith(date_str):
                kp_str = entry.get("kp", "2.0")
                try:
                    kp = float(kp_str)
                    kp_cache[date] = (kp, current_time)
                    return kp
                except (TypeError, ValueError):
                    continue
        
        # Если данные не найдены, пытаемся использовать данные за вчерашний день (для прошлых дат)
        if date < datetime.datetime.now().date():
            yesterday = date - datetime.timedelta(days=1)
            yesterday_str = yesterday.strftime("%Y-%m-%d")
            
            for entry in data:
                time_tag = entry.get("time_tag", "")
                if time_tag.startswith(yesterday_str):
                    kp_str = entry.get("kp", "2.0")
                    try:
                        kp = float(kp_str)
                        kp_cache[date] = (kp, current_time)
                        return kp
                    except (TypeError, ValueError):
                        continue
        
        # Для будущих дат используем прогноз
        if date > datetime.datetime.now().date():
            for entry in data:
                if "forecast" in entry:
                    # Используем первый прогнозный Kp
                    kp_str = entry["forecast"][0]
                    try:
                        kp = float(kp_str)
                        kp_cache[date] = (kp, current_time)
                        return kp
                    except (TypeError, ValueError):
                        pass
        
        # Если ничего не найдено, возвращаем среднее значение
        kp_cache[date] = (2.0, current_time)
        return 2.0
        
    except Exception as e:
        logger.error(f"Ошибка получения Kp-индекса: {e}")
        kp_cache[date] = (2.0, current_time)
        return 2.0

def is_night(lat, lon, dt):
    """Определяет, является ли время ночью для заданных координат"""
    try:
        tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
        local_tz = pytz.timezone(tz_str)
        local_dt = dt.astimezone(local_tz)
        
        city = LocationInfo("", "", tz_str, lat, lon)
        s = sun(city.observer, date=local_dt.date(), observer_elevation=0)
        
        # Получаем время восхода и заката
        sunrise = s.get('sunrise', None)
        sunset = s.get('sunset', None)
        
        if sunrise is None or sunset is None:
            # Если не можем определить, считаем за ночь (безопасный вариант)
            return True
            
        # Сравниваем текущее время с временем восхода и заката
        return local_dt < sunrise or local_dt > sunset

    except Exception as e:
        logger.error(f"Ошибка определения ночи: {e}")
        # В случае ошибки возвращаем True как наиболее безопасный вариант
        return True

def get_country(lat, lon):
    """Определяет страну по координатам и возвращает на русском."""
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
    # Получаем астрологические данные из jyotish.py
    astro_data = calculate_astrology(lat, lon, dt)
    
    moon_pos = astro_data["moon"]
    rahu_pos = astro_data["rahu"]
    nakshatra = astro_data["nakshatra"]
    moon_house = astro_data["moon_house"]
    houses = astro_data["houses"]
    
    # Рассчитываем положение Луны относительно Солнца
    sun_pos = astro_data["sun"]
    angle = (moon_pos - sun_pos) % 360
    
    # Определяем положение Раху относительно долготы места
    lon_360 = lon if lon >= 0 else 360 + lon
    rahu_diff = min(
        abs(lon_360 - rahu_pos),
        abs(lon_360 - rahu_pos + 360),
        abs(lon_360 - rahu_pos - 360)
    )
    
    # Определяем условия
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
    
    # Улучшенные условия для каждого типа
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
    """Проверяет, были ли исторические контакты в этой точке"""
    historical_events = [
        {"lat": 33.3943, "lon": -104.5230, "date": "1947-07-05"},
        {"lat": 52.2392, "lon": -2.6190, "date": "1980-12-26"},
        {"lat": -33.9000, "lon": 18.4200, "date": "1994-01-21"}
    ]
    
    event_date = dt.strftime("%Y-%m-%d")
    
    for event in historical_events:
        # Проверяем расстояние (в градусах) и дату
        lat_diff = abs(event["lat"] - lat)
        lon_diff = abs(event["lon"] - lon)
        if lat_diff < 0.1 and lon_diff < 0.1 and event["date"] == event_date:
            return True
    return False

# === КОНТИНЕНТАЛЬНЫЕ ЗОНЫ ===
CONTINENTS = {
    "евразия": {
        "name": "Евразия",
        "min_lat": -10,
        "max_lat": 80,
        "min_lon": -20,
        "max_lon": 180,
        "regions": [
            {"name": "Свердловская область", "lat": 56.8380, "lon": 60.6057},
            {"name": "Московская область", "lat": 55.7558, "lon": 37.6173},
            {"name": "Новосибирская область", "lat": 55.0415, "lon": 82.9343},
            {"name": "Краснодарский край", "lat": 45.0355, "lon": 38.9760},
            {"name": "Красноярский край", "lat": 56.0184, "lon": 92.8679},
            {"name": "Хабаровский край", "lat": 48.4943, "lon": 135.0687}
        ]
    },
    "северная америка": {
        "name": "Северная Америка",
        "min_lat": 15,
        "max_lat": 75,
        "min_lon": -170,
        "max_lon": -50,
        "regions": [
            {"name": "Калифорния", "lat": 36.7783, "lon": -119.4179},
            {"name": "Техас", "lat": 31.9686, "lon": -99.9018},
            {"name": "Канада", "lat": 56.1304, "lon": -106.3468},
            {"name": "Мексика", "lat": 23.6345, "lon": -102.5528}
        ]
    },
    "южная америка": {
        "name": "Южная Америка",
        "min_lat": -60,
        "max_lat": 15,
        "min_lon": -80,
        "max_lon": -35,
        "regions": [
            {"name": "Бразилия", "lat": -14.2350, "lon": -51.9253},
            {"name": "Аргентина", "lat": -38.4161, "lon": -63.6167},
            {"name": "Чили", "lat": -35.6751, "lon": -71.5429}
        ]
    },
    "африка": {
        "name": "Африка",
        "min_lat": -35,
        "max_lat": 35,
        "min_lon": -20,
        "max_lon": 50,
        "regions": [
            {"name": "Египет", "lat": 26.8206, "lon": 30.8025},
            {"name": "Кения", "lat": -0.0236, "lon": 37.9062},
            {"name": "ЮАР", "lat": -25.7461, "lon": 28.1876}
        ]
    },
    "австралия": {
        "name": "Австралия",
        "min_lat": -45,
        "max_lat": -10,
        "min_lon": 110,
        "max_lon": 160,
        "regions": [
            {"name": "Сидней", "lat": -33.8688, "lon": 151.2093},
            {"name": "Мельбурн", "lat": -37.8136, "lon": 144.9631},
            {"name": "Перт", "lat": -31.9505, "lon": 115.8605}
        ]
    }
}

# === ОБРАБОТЧИКИ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Создаем кнопки с континентами
    keyboard = [
        [KeyboardButton("Евразия 🌏"), KeyboardButton("Северная Америка 🌎")],
        [KeyboardButton("Южная Америка 🌍"), KeyboardButton("Африка 🌍")],
        [KeyboardButton("Австралия 🌏")]
    ]
    
    reply_markup = ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True,
        one_time_keyboard=False
    )
    
    await update.message.reply_text(
        "🌍 <b>Система анализа порталов</b>\n\n"
        "Выберите континент для анализа:\n\n"
        "• Евразия\n"
        "• Северная Америка\n"
        "• Южная Америка\n"
        "• Африка\n"
        "• Австралия\n\n"
        "Система найдет порталы по вашим критериям.",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    return STATE_START

async def select_continent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower()
    continent = None
    
    # Определяем континент по названию
    for key in CONTINENTS.keys():
        if key in user_input or CONTINENTS[key]["name"].lower() in user_input:
            continent = key
            break
    
    if not continent:
        await update.message.reply_text(
            "❌ Не удалось определить континент. Пожалуйста, выберите из предложенных вариантов.",
            reply_markup=ReplyKeyboardMarkup(
                [
                    [KeyboardButton("Евразия 🌏"), KeyboardButton("Северная Америка 🌎")],
                    [KeyboardButton("Южная Америка 🌍"), KeyboardButton("Африка 🌍")],
                    [KeyboardButton("Австралия 🌏")]
                ],
                resize_keyboard=True
            )
        )
        return STATE_START
    
    # Сохраняем выбранный континент
    context.user_data['continent'] = continent
    
    # Создаем кнопки с типами порталов
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
        f"🔍 Выбран континент: <b>{CONTINENTS[continent]['name']}</b>\n\n"
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
    
    # Определяем тип портала
    if "геопортал" in user_input or "1" in user_input or "✅" in user_input:
        portal_type = 1
    elif "атмосферный" in user_input or "2" in user_input or "🌤" in user_input:
        portal_type = 2
    elif "аварийный" in user_input or "4" in user_input or "💥" in user_input:
        portal_type = 4
    else:
        # Если тип не определен, возвращаем к выбору
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
    
    # Сохраняем тип портала
    context.user_data['portal_type'] = portal_type
    
    # Запрашиваем год
    await update.message.reply_text(
        "📅 Укажите год для анализа (например, 2025):",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return STATE_ENTER_YEAR

async def enter_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        year = int(update.message.text)
        if year < 1900 or year > 2100:
            await update.message.reply_text(
                "❌ Некорректный год. Введите год в диапазоне 1900-2100."
            )
            return STATE_ENTER_YEAR
        
        # Получаем сохраненные данные
        continent = context.user_data.get('continent')
        portal_type = context.user_data.get('portal_type')
        
        if not continent or portal_type is None:
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте заново.")
            return ConversationHandler.END
        
        # Начинаем анализ
        await update.message.reply_text(
            f"⏳ Начинаю анализ {year} года для {CONTINENTS[continent]['name']}...\n\n"
            "Это может занять несколько минут."
        )
        
        # Выполняем анализ
        results = []
        for region in CONTINENTS[continent]["regions"]:
            for month in range(1, 13):
                for day in range(1, 32):
                    try:
                        dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)
                        event_type, _ = get_event_analysis(region["lat"], region["lon"], dt)
                        
                        # Проверяем тип портала
                        if (portal_type == 1 and "Тип 1" in event_type) or \
                           (portal_type == 2 and "Тип 2" in event_type) or \
                           (portal_type == 4 and "Тип 4" in event_type):
                            
                            results.append(
                                f"{day:02d}.{month:02d}.{year} — {event_type}\n"
                                f"• Координаты: {region['lat']:.4f}, {region['lon']:.4f}\n"
                                f"• {region['name']} ({CONTINENTS[continent]['name']})"
                            )
                    except:
                        continue
        
        # Формируем и отправляем результаты
        if results:
            full = "\n\n".join(results[:50])  # Ограничение на 50 результатов
            if len(results) > 50:
                full += "\n\n<i>(Показаны первые 50 результатов)</i>"
            await update.message.reply_text(full, parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Порталы не найдены.")
        
        # Завершаем диалог
        return ConversationHandler.END
    
    except ValueError:
        await update.message.reply_text("❌ Введите корректный год (например, 2025).")
        return STATE_ENTER_YEAR

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Операция отменена. Чтобы начать заново, отправьте /start",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# === НОВАЯ ФУНКЦИЯ: РУЧНОЙ ПОИСК ===
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

        place_synonyms = {
            # ... (весь твой словарь place_synonyms остаётся без изменений)
            "Розуэлл": "Roswell",
            "США": "USA",
            "Нью-Йорк": "New York",
            "Лос-Анджелес": "Los Angeles",
            "Чикаго": "Chicago",
            "Хьюстон": "Houston",
            "Финикс": "Phoenix",
            "Филадельфия": "Philadelphia",
            "Сан-Антонио": "San Antonio",
            "Сан-Дiego": "San Diego",
            "Даллас": "Dallas",
            "Сан-Хосе": "San Jose",
            "Остин": "Austin",
            "Джексонвилл": "Jacksonville",
            "Форт-Уэрт": "Fort Worth",
            "Коламбус": "Columbus",
            "Индианаполис": "Indianapolis",
            "Шарлотт": "Charlotte",
            "Сан-Франциско": "San Francisco",
            "Сиэтл": "Seattle",
            "Денвер": "Denver",
            "Вашингтон": "Washington",
            "Бостон": "Boston",
            "Эл-Пасо": "El Paso",
            "Детройт": "Detroit",
            "Мемфис": "Memphis",
            "Портленд": "Portland",
            "Лас-Вегас": "Las Vegas",
            "Милуоки": "Milwaukee",
            "Альбукерке": "Albuquerque",
            "Тусон": "Tucson",
            "Фресно": "Fresno",
            "Сакраменто": "Sacramento",
            "Лонг-Бич": "Long Beach",
            "Канзас-Сити": "Kansas City",
            "Меса": "Mesa",
            "Атланта": "Atlanta",
            "Майами": "Miami",
            "Оклахома-Сити": "Oklahoma City",
            "Нэшвилл": "Nashville",
            "Луисвилл": "Louisville",
            "Балтимор": "Baltimore",
            "Торонто": "Toronto",
            "Монреаль": "Montreal",
            "Калгари": "Calgary",
            "Оттава": "Ottawa",
            "Эдмонтон": "Edmonton",
            "Миссиссага": "Mississauga",
            "Виннипег": "Winnipeg",
            "Ванкувер": "Vancouver",
            "Брамптон": "Brampton",
            "Гамильтон": "Hamilton",
            "Мехико": "Mexico City",
            "Гвадалахара": "Guadalajara",
            "Монтеррей": "Monterrey",
            "Пуэбла": "Puebla",
            "Тиуана": "Tijuana",
            "Леон": "Leon",
            "Хуарес": "Juarez",
            "Сан-Луис-Потоси": "San Luis Potosi",
            "Мерида": "Merida",
            "Канкун": "Cancun",
            "Лондон": "London",
            "Париж": "Paris",
            "Берлин": "Berlin",
            "Мадрид": "Madrid",
            "Рим": "Rome",
            "Амстердам": "Amsterdam",
            "Брюссель": "Brussels",
            "Вена": "Vienna",
            "Будапешт": "Budapest",
            "Варшава": "Warsaw",
            "Прага": "Prague",
            "Копенгаген": "Copenhagen",
            "Стокгольм": "Stockholm",
            "Осло": "Oslo",
            "Хельсинки": "Helsinki",
            "Дублин": "Dublin",
            "Лиссабон": "Lisbon",
            "Афины": "Athens",
            "Бухарест": "Bucharest",
            "София": "Sofia",
            "Загреб": "Zagreb",
            "Белград": "Belgrade",
            "Киев": "Kyiv",
            "Минск": "Minsk",
            "Москва": "Moscow",
            "Санкт-Петербург": "Saint Petersburg",
            "Новосибирск": "Novosibirsk",
            "Екатеринбург": "Yekaterinburg",
            "Казань": "Kazan",
            "Нижний Новгород": "Nizhny Novgorod",
            "Челябинск": "Chelyabinsk",
            "Самара": "Samara",
            "Омск": "Omsk",
            "Ростов-на-Дону": "Rostov-on-Don",
            "Уфа": "Ufa",
            "Красноярск": "Krasnoyarsk",
            "Воронеж": "Voronezh",
            "Пермь": "Perm",
            "Волгоград": "Volgograd",
            "Токио": "Tokyo",
            "Дели": "Delhi",
            "Шанхай": "Shanghai",
            "Пекин": "Beijing",
            "Мумбаи": "Mumbai",
            "Осака": "Osaka",
            "Сеул": "Seoul",
            "Стамбул": "Istanbul",
            "Тегеран": "Tehran",
            "Бангкок": "Bangkok",
            "Куала-Лумпур": "Kuala Lumpur",
            "Манила": "Manila",
            "Джакарта": "Jakarta",
            "Сингапур": "Singapore",
            "Ханой": "Hanoi",
            "Дубай": "Dubai",
            "Эр-Рияд": "Riyadh",
            "Каир": "Cairo",
            "Йоханнесбург": "Johannesburg",
            "Найроби": "Nairobi",
            "Кейптаун": "Cape Town",
            "Лагос": "Lagos",
            "Аддис-Абеба": "Addis Ababa",
            "Триполи": "Tripoli",
            "Алжир": "Algiers",
            "Касабланка": "Casablanca",
            "Тунис": "Tunis",
            "Дакар": "Dakar",
            "Аккра": "Accra",
            "Луанда": "Luanda",
            "Хараре": "Harare",
            "Лусака": "Lusaka",
            "Мапуту": "Maputo",
            "Антананариву": "Antananarivo",
            "Порт-Луи": "Port Louis",
            "Морони": "Moroni",
            "Виктория": "Victoria",
            "Рендлешем": "Rendlesham",
            "Канада": "Canada",
            "Мексика": "Mexico",
            "Бразилия": "Brazil",
            "Аргентина": "Argentina",
            "Чили": "Chile",
            "Перу": "Peru",
            "Колумбия": "Colombia",
            "Венесуэла": "Venezuela",
            "Австралия": "Australia",
            "Новая Зеландия": "New Zealand",
            "Великобритания": "United Kingdom",
            "Франция": "France",
            "Германия": "Germany",
            "Италия": "Italy",
            "Испания": "Spain",
            "Россия": "Russia",
            "Украина": "Ukraine",
            "Бельгия": "Belgium",
            "Нидерланды": "Netherlands",
            "Португалия": "Portugal",
            "Швеция": "Sweden",
            "Норвегия": "Norway",
            "Финляндия": "Finland",
            "Австрия": "Austria",
            "Швейцария": "Switzerland",
            "Япония": "Japan",
            "Южная Корея": "South Korea",
            "Китай": "China",
            "Индия": "India",
            "Израиль": "Israel",
            "Турция": "Turkey"
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
            "• <code>2025</code>\n"
            "• <code>5 июля 1947, Roswell, USA</code>\n"
            "• <code>5 июля 1947, 33.3943, -104.5230</code>",
            parse_mode="HTML"
        )

# === ЗАПУСК ===

if __name__ == "__main__":
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчик диалога
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_continent)],
            STATE_SELECT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_type)],
            STATE_ENTER_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_year)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("help", help_command))
    
    # Добавляем обработчик свободного ввода (работает вне диалога)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'\d+\s+\w+,\s+[\w\s]+'), manual_search))
    
    # Запускаем Flask-сервер в фоне
    from threading import Thread
    def run_flask():
        flask_app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
    thread = Thread(target=run_flask)
    thread.daemon = True
    thread.start()

    logger.info("🚀 Бот запущен.")
    app.run_polling()