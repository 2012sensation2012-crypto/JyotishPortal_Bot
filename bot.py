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

        # Словарь синонимов для геокодера
        place_synonyms = {
            # США
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
            # Канада
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
            # Мексика
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
            # Европа
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
            # Азия
            "Токио": "Tokyo",
            "Дели": "Delhi",
            "Шанхай": "Shanghai",
            "Пекин": "Beijing",
            "Мумбаи": "Mumbai",
            "Осака": "Osaka",
            "Каракас": "Caracas",
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
            "Малабо": "Malabo",
            "Яунде": "Yaounde",
            "Браззавиль": "Brazzaville",
            "Киншаса": "Kinshasa",
            "Либревиль": "Libreville",
            "Банги": "Bangui",
            "Нджамена": "N'Djamena",
            "Хартум": "Khartoum",
            "Джибути": "Djibouti",
            "Могадишо": "Mogadishu",
            "Найроби": "Nairobi",
            "Кампала": "Kampala",
            "Кигали": "Kigali",
            "Бужумбура": "Bujumbura",
            "Гитега": "Gitega",
            "Лилонгве": "Lilongwe",
            "Блантайр": "Blantyre",
            "Лусака": "Lusaka",
            "Хараре": "Harare",
            "Булавайо": "Bulawayo",
            "Мапуту": "Maputo",
            "Бейра": "Beira",
            "Нампula": "Nampula",
            "Антананариву": "Antananarivo",
            "Туамасина": "Toamasina",
            "Фианаранцуа": "Fianarantsoa",
            "Махадзанга": "Mahajanga",
            "Анциранана": "Antsiranana",
            "Порт-Луи": "Port Louis",
            "Кюре": "Curepipe",
            "Вакоас": "Vacoas",
            "Морони": "Moroni",
            "Мутсамуду": "Mutsamudu",
            "Фомбони": "Fomboni",
            "Виктория": "Victoria",
            "Праслин": "Praslin",
            "Ла-Диг": "La Digue",
            "Малабо": "Malabo",
            "Бата": "Bata",
            "Эбеибин": "Ebebiyin",
            "Яунде": "Yaounde",
            "Дуала": "Douala",
            "Гаруа": "Garoua",
            "Баменда": "Bamenda",
            "Нгаундере": "Ngaoundere",
            "Берберати": "Berberati",
            "Банги": "Bangui",
            "Бимбо": "Bimbo",
            "Мбаики": "Mbaiki",
            "Канга": "Kaga-Bandoro",
            "Бриака": "Bria",
            "Босангоа": "Bossangoa",
            "Нджамена": "N'Djamena",
            "Мунду": "Moundou",
            "Сарх": "Sarh",
            "Абеше": "Abéché",
            "Хартум": "Khartoum",
            "Омдурман": "Omdurman",
            "Порт-Судан": "Port Sudan",
            "Кассала": "Kassala",
            "Джибути": "Djibouti",
            "Али-Сабие": "Ali Sabieh",
            "Дихил": "Dikhil",
            "Таджурра": "Tadjoura",
            "Обок": "Obock",
            "Могадишо": "Mogadishu",
            "Харгейса": "Hargeisa",
            "Босасо": "Bosaso",
            "Кисмайо": "Kismayo",
            "Байдабо": "Baidoa",
            "Гароуэ": "Garoowe",
            "Дхусамареб": "Dhusamareb",
            "Лас-Анод": "Las Anod",
            "Найроби": "Nairobi",
            "Момбаса": "Mombasa",
            "Накуру": "Nakuru",
            "Кисуму": "Kisumu",
            "Элдорет": "Eldoret",
            "Кампала": "Kampala",
            "Гулу": "Gulu",
            "Мбарара": "Mbarara",
            "Кигали": "Kigali",
            "Бужумбура": "Bujumbura",
            "Гитега": "Gitega",
            "Ньянгве": "Nyanza",
            "Румонги": "Rumonge",
            "Лилонгве": "Lilongwe",
            "Блантайр": "Blantyre",
            "Зомба": "Zomba",
            "Мзузу": "Mzuzu",
            "Хараре": "Harare",
            "Булавайо": "Bulawayo",
            "Мутаре": "Mutare",
            "Гверу": "Gweru",
            "Квекве": "Kwekwe",
            "Чинхой": "Chinhoyi",
            "Масвинго": "Masvingo",
            "Марондер": "Marondera",
            "Мапуту": "Maputo",
            "Бейра": "Beira",
            "Нампula": "Nampula",
            "Келимане": "Quelimane",
            "Тете": "Tete",
            "Шимойо": "Xai-Xai",
            "Лихенга": "Lichinga",
            "Пемба": "Pemba",
            "Илха-де-Мозамбик": "Ilha de Moçambique",
            "Антананариву": "Antananarivo",
            "Туамасина": "Toamasina",
            "Фианаранцуа": "Fianarantsoa",
            "Махадзанга": "Mahajanga",
            "Анциранана": "Antsiranana",
            "Тулиара": "Toliara",
            "Анцохи": "Antsohihy",
            "Безанга": "Besalampy",
            "Махаджанга": "Mahajanga",
            "Нози-Бе": "Nosy Be",
            "Порт-Луи": "Port Louis",
            "Кюре": "Curepipe",
            "Вакоас": "Vacoas",
            "Флак": "Flacq",
            "Мокa": "Moka",
            "Плейн-де-Кокос": "Plaine des Papayes",
            "Морони": "Moroni",
            "Мутсамуду": "Mutsamudu",
            "Фомбони": "Fomboni",
            "Домони": "Domoni",
            "Виктория": "Victoria",
            "Праслин": "Praslin",
            "Ла-Диг": "La Digue",
            "Малабо": "Malabo",
            "Бата": "Bata",
            "Эбеибин": "Ebebiyin",
            "Мбini": "Mbini",
            "Луба": "Luba",
            "Яунде": "Yaounde",
            "Дуала": "Douala",
            "Гаруа": "Garoua",
            "Баменда": "Bamenda",
            "Нгаундере": "Ngaoundere",
            "Берберати": "Berberati",
            "Банги": "Bangui",
            "Бимбо": "Bimbo",
            "Мбаики": "Mbaiki",
            "Канга": "Kaga-Bandoro",
            "Бриака": "Bria",
            "Босангоа": "Bossangoa",
            "Нджамена": "N'Djamena",
            "Мунду": "Moundou",
            "Сарх": "Sarh",
            "Абеше": "Abéché",
            "Хартум": "Khartoum",
            "Омдурман": "Omdurman",
            "Порт-Судан": "Port Sudan",
            "Кассала": "Kassala",
            "Джибути": "Djibouti",
            "Али-Сабие": "Ali Sabieh",
            "Дихил": "Dikhil",
            "Таджурра": "Tadjoura",
            "Обок": "Obock",
            "Могадишо": "Mogadishu",
            "Харгейса": "Hargeisa",
            "Босасо": "Bosaso",
            "Кисмайо": "Kismayo",
            "Байдабо": "Baidoa",
            "Гароуэ": "Garoowe",
            "Дхусамареб": "Dhusamareb",
            "Лас-Анод": "Las Anod",
            "Найроби": "Nairobi",
            "Момбаса": "Mombasa",
            "Накуру": "Nakuru",
            "Кисуму": "Kisumu",
            "Элдорет": "Eldoret",
            "Кампала": "Kampala",
            "Гулу": "Gulu",
            "Мбарара": "Mbarara",
            "Кигали": "Kigali",
            "Бужумбура": "Bujumbura",
            "Гитега": "Gitega",
            "Ньянгве": "Nyanza",
            "Румонги": "Rumonge",
            "Лилонгве": "Lilongwe",
            "Блантайр": "Blantyre",
            "Зомба": "Zomba",
            "Мзузу": "Mzuzu",
            "Хараре": "Harare",
            "Булавайо": "Bulawayo",
            "Мутаре": "Mutare",
            "Гверу": "Gweru",
            "Квекве": "Kwekwe",
            "Чинхой": "Chinhoyi",
            "Масвинго": "Masvingo",
            "Марондер": "Marondera",
            "Мапуту": "Maputo",
            "Бейра": "Beira",
            "Нампula": "Nampula",
            "Келимане": "Quelimane",
            "Тете": "Tete",
            "Шимойо": "Xai-Xai",
            "Лихенга": "Lichinga",
            "Пемба": "Pemba",
            "Илха-де-Мозамбик": "Ilha de Moçambique",
            "Антананариву": "Antananarivo",
            "Туамасина": "Toamasina",
            "Фианаранцуа": "Fianarantsoa",
            "Махадзанга": "Mahajanga",
            "Анциранана": "Antsiranana",
            "Тулиара": "Toliara",
            "Анцохи": "Antsohihy",
            "Безанга": "Besalampy",
            "Махаджанга": "Mahajanga",
            "Нози-Бе": "Nosy Be",
            "Порт-Луи": "Port Louis",
            "Кюре": "Curepipe",
            "Вакоас": "Vacoas",
            "Флак": "Flacq",
            "Мокa": "Moka",
            "Плейн-де-Кокос": "Plaine des Papayes",
            "Морони": "Moroni",
            "Мутсамуду": "Mutsamudu",
            "Фомбони": "Fomboni",
            "Домони": "Domoni",
            "Виктория": "Victoria",
            "Праслин": "Praslin",
            "Ла-Диг": "La Digue"
        }

        # Заменяем русские названия на английские
        for key, value in place_synonyms.items():
            rest = rest.replace(key, value)

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