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
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
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
    """Получает реальный Kp-индекс из API NOAA с улучшенной обработкой ошибок"""
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
    try:
        tz_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
        local_tz = pytz.timezone(tz_str)
        local_dt = dt.astimezone(local_tz)
        city = LocationInfo("", "", tz_str, lat, lon)

        s = sun(city.observer, date=local_dt.date(), observer_elevation=0)

        dawn = s.get("dawn", None)
        dusk = s.get("dusk", None)

        if dawn is None or dusk is None:
            sunrise = s.get("sunrise", None)
            sunset = s.get("sunset", None)
            if sunrise and sunset:
                return local_dt < sunrise or local_dt > sunset
            else:
                return True

        return local_dt < dawn or local_dt > dusk

    except Exception as e:
        logger.warning(f"Ошибка определения ночи: {e}")
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

# === ОБРАБОТЧИКИ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌌 <b>Система порталов v4.1</b>\n\n"
        "Просто отправьте:\n\n"
        "• <b>Год</b> (например, <code>2025</code>) → получите все порталы в этом году\n"
        "• <b>Дату и место</b> (например, <code>5 июля 1947, Roswell, USA</code>) → полный анализ события\n\n"
        "Поддерживаемые форматы:\n"
        "• <code>5 июля 1947, Розуэлл, США</code>\n"
        "• <code>5 июля 1947, 33.3943, -104.5230</code>",
        parse_mode="HTML"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        "1️⃣ Отправьте <b>год</b>:\n"
        "   → <code>2025</code>\n"
        "   → Получите список всех порталов в этом году с указанием точки анализа.\n\n"
        "2️⃣ Отправьте <b>дату и место</b>:\n"
        "   → <code>5 июля 1947, Roswell, USA</code>\n"
        "   → Получите полный джйотиш-анализ события.",
        parse_mode="HTML"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    # Проверяем, является ли текст годом (4 цифры)
    if text.isdigit() and len(text) == 4:
        year = int(text)
        if year > datetime.datetime.now().year:
            await search_future_portals(update, context, year)
        else:
            await search_past_portals(update, context, year)
    else:
        await manual_search(update, context)

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

# === ОБНОВЛЁННЫЕ ФУНКЦИИ ПОИСКА ПО ГОДУ ===

async def search_future_portals(update: Update, context: ContextTypes.DEFAULT_TYPE, year: int):
    await update.message.reply_text(f"⏳ Анализирую {year}...")

    # Попробуем получить пользовательские координаты
    lat, lon = 35.1495, -89.9764  # Стандартные (Мемфис)
    
    try:
        # Здесь можно добавить поддержку пользовательских координат
        # Например, из context.user_data
        user_data = context.user_data
        if 'lat' in user_data and 'lon' in user_data:
            lat = float(user_data['lat'])
            lon = float(user_data['lon'])
    except:
        pass

    country = get_country(lat, lon)

    results = []
    for month in range(1, 13):
        for day in range(1, 32):
            try:
                dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)
                event_type, _ = get_event_analysis(lat, lon, dt)
                if event_type != "❌ Вне системы":
                    results.append(
                        f"{day:02d}.{month:02d}.{year} — {event_type}\n"
                        f"  • {lat:.4f}, {lon:.4f} ({country})"
                    )
            except Exception as e:
                logger.warning(f"Ошибка при анализе {day}.{month}.{year}: {e}")
                continue

    if results:
        full = "\n\n".join(results) + "\n\n<i>(Событие будет замечено людьми)</i>"
        for i in range(0, len(full), 4000):
            await update.message.reply_text(full[i:i+4000], parse_mode="HTML")
    else:
        await update.message.reply_text(f"❌ В {year} году порталы не найдены.")


async def search_past_portals(update: Update, context: ContextTypes.DEFAULT_TYPE, year: int):
    await update.message.reply_text(f"⏳ Анализирую {year}...")

    # Попробуем получить пользовательские координаты
    lat, lon = 35.1495, -89.9764  # Стандартные (Мемфис)
    
    try:
        user_data = context.user_data
        if 'lat' in user_data and 'lon' in user_data:
            lat = float(user_data['lat'])
            lon = float(user_data['lon'])
    except:
        pass

    country = get_country(lat, lon)

    results = []
    for month in range(1, 13):
        for day in range(1, 32):
            try:
                dt = datetime.datetime(year, month, day, 15, tzinfo=pytz.UTC)
                event_type, _ = get_event_analysis(lat, lon, dt)
                if event_type != "❌ Вне системы":
                    results.append(
                        f"{day:02d}.{month:02d}.{year} — {event_type}\n"
                        f"  • {lat:.4f}, {lon:.4f} ({country})"
                    )
            except Exception as e:
                logger.warning(f"Ошибка при анализе {day}.{month}.{year}: {e}")
                continue

    if results:
        full = "\n\n".join(results) + "\n\n<i>(Событие было замечено людьми)</i>"
        for i in range(0, len(full), 4000):
            await update.message.reply_text(full[i:i+4000], parse_mode="HTML")
    else:
        await update.message.reply_text(f"❌ В {year} году порталы не найдены.")

# === ЗАПУСК ===

if __name__ == "__main__":
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем Flask-сервер в фоне
    from threading import Thread
    def run_flask():
        flask_app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
    thread = Thread(target=run_flask)
    thread.daemon = True
    thread.start()

    logger.info("🚀 Бот запущен.")
    app.run_polling()