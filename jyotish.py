import swisseph as swe
import logging
from datetime import datetime
import pytz

# Настройка логирования
logger = logging.getLogger(__name__)

def calculate_astrology(lat, lon, dt):
    """
    Выполняет точные астрологические расчёты для заданных координат и даты
    """
    # Нормализуем время в UTC
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    else:
        dt = dt.astimezone(pytz.utc)
    
    # Получаем юлианскую дату
    jd = swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute/60.0)
    
    # Рассчитываем положения планет
    sun_pos = swe.calc_ut(jd, swe.SUN)[0][0] % 360
    moon_pos = swe.calc_ut(jd, swe.MOON)[0][0] % 360
    rahu_pos = swe.calc_ut(jd, swe.MEAN_NODE)[0][0] % 360
    
    # Определяем накшатру
    nakshatra = get_nakshatra(moon_pos)
    
    # Рассчитываем дома по системе Кришнамурти (KP) — используем Плацидус как основу
    houses = get_houses_kp(lat, lon, jd)
    
    # Определяем текущую дашу по Вимшоттари
    dasha_planet, dasha_years = get_dasha_period_vimshottari(moon_pos)
    
    # Определяем дом Луны
    moon_house = get_moon_house(moon_pos, houses)
    
    return {
        "sun": sun_pos,
        "moon": moon_pos,
        "rahu": rahu_pos,
        "nakshatra": nakshatra,
        "houses": houses,
        "dasha": {
            "planet": dasha_planet,
            "years": dasha_years
        },
        "moon_house": moon_house,
        "moon_sign": get_zodiac_sign(moon_pos)
    }

def get_nakshatra(moon_lon):
    """Определяет накшатру по положению Луны"""
    nakshatras = [
        ("Ашвини", 0.0, 13.2),
        ("Бхарани", 13.2, 26.4),
        ("Криттика", 26.4, 39.6),
        ("Рохини", 39.6, 52.8),
        ("Мригашира", 52.8, 66.0),
        ("Ардра", 66.0, 79.2),
        ("Пунарвасу", 79.2, 92.4),
        ("Пушья", 92.4, 105.6),
        ("Ашлеша", 105.6, 118.8),
        ("Магха", 118.8, 132.0),
        ("Пурва Фалгуни", 132.0, 145.2),
        ("Уттара Фалгуни", 145.2, 158.4),
        ("Хаста", 158.4, 171.6),
        ("Читра", 171.6, 184.8),
        ("Свади", 184.8, 198.0),
        ("Вишакха", 198.0, 211.2),
        ("Анурадха", 211.2, 224.4),
        ("Джйештха", 224.4, 237.6),
        ("Мула", 237.6, 250.8),
        ("Пурва Ашадха", 250.8, 264.0),
        ("Уттара Ашадха", 264.0, 277.2),
        ("Шравана", 277.2, 290.4),
        ("Дхаништха", 290.4, 303.6),
        ("Шатабхиша", 303.6, 316.8),
        ("Пурва Бхадрапада", 316.8, 330.0),
        ("Уттара Бхадрапада", 330.0, 343.2),
        ("Ревати", 343.2, 356.4)
    ]
    
    for name, start, end in nakshatras:
        if start <= moon_lon < end:
            return name
    logger.warning(f"Накшатра не найдена для {moon_lon}°")
    return "Неизвестно"

def get_zodiac_sign(pos):
    """Определяет знак Зодиака"""
    signs = [
        "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
        "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
    ]
    return signs[int(pos // 30)]

def get_moon_house(moon_pos, houses):
    """Определяет дом Луны по системе Кришнамурти"""
    try:
        for i in range(12):
            start = houses[i]
            end = houses[(i + 1) % 12]
            
            if start < end:
                if start <= moon_pos < end:
                    return i + 1
            else:
                # Пересечение 0°
                if moon_pos >= start or moon_pos < end:
                    return i + 1
        logger.warning(f"Дом Луны не определён: {moon_pos}°, дома: {houses}")
        return None
    except Exception as e:
        logger.error(f"Ошибка определения дома Луны: {e}")
        return None

def get_houses_kp(lat, lon, jd):
    """
    Рассчитывает дома по системе Кришнамурти (KP)
    
    Система Кришнамурти использует систему домов Плацидус (Placidus) 
    с дополнительными подразделами (Vargas) для точных расчётов.
    """
    try:
        # Используем систему домов Плацидус (b'P') — стандартная в KP
        house_cusps, ascmc = swe.houses(jd, lat, lon, b'P')
        
        # Возвращаем только первые 12 куспидов (домов)
        return house_cusps[:12]
    except Exception as e:
        logger.error(f"Ошибка расчёта домов KP: {e}")
        return get_houses_fallback(lat, lon, jd)

def get_houses_fallback(lat, lon, jd):
    """
    Резервный метод расчёта домов, если основной не сработал
    """
    try:
        # Получаем положение Асцендента
        asce = swe.calc_ut(jd, swe.ASC)[0][0] % 360
        
        # Рассчитываем 12 домов по равным 30°
        houses = []
        for i in range(12):
            house_start = (asce + i * 30) % 360
            houses.append(house_start)
        
        return houses
    except Exception as e:
        logger.error(f"Ошибка резервного расчёта домов: {e}")
        return [i * 30 for i in range(12)]  # Резерв: равные дома

def get_dasha_period_vimshottari(moon_lon):
    """
    Определяет текущую даша-период по системе Вимшоттари
    
    Вимшоттари даша — основная в системе Кришнамурти.
    Даша зависит от положения Луны в накшатре.
    """
    # Планеты и их продолжительность в Вимшоттари даше
    dasha_planets = [
        "Кету", "Венера", "Солнце", "Луна", "Марс", 
        "Раху", "Юпитер", "Сатурн", "Меркурий"
    ]
    dasha_durations = [7, 20, 6, 10, 7, 18, 19, 20, 17]
    
    # Определяем накшатру Луны
    nakshatra_name = get_nakshatra(moon_lon)
    
    # Сопоставляем накшатру с планетой даша
    nakshatra_to_planet = {
        "Ашвини": "Кету",
        "Бхарани": "Венера",
        "Криттика": "Солнце",
        "Рохини": "Луна",
        "Мригашира": "Марс",
        "Ардра": "Раху",
        "Пунарвасу": "Юпитер",
        "Пушья": "Сатурн",
        "Ашлеша": "Меркурий",
        "Магха": "Кету",
        "Пурва Фалгуни": "Венера",
        "Уттара Фалгуни": "Солнце",
        "Хаста": "Луна",
        "Читра": "Марс",
        "Свади": "Раху",
        "Вишакха": "Юпитер",
        "Анурадха": "Сатурн",
        "Джйештха": "Меркурий",
        "Мула": "Кету",
        "Пурва Ашадха": "Венера",
        "Уттара Ашадха": "Солнце",
        "Шравана": "Луна",
        "Дхаништха": "Марс",
        "Шатабхиша": "Раху",
        "Пурва Бхадрапада": "Юпитер",
        "Уттара Бхадрапада": "Сатурн",
        "Ревати": "Меркурий"
    }
    
    planet = nakshatra_to_planet.get(nakshatra_name, "Кету")  # Дефолт — Кету
    
    # Находим индекс планеты
    try:
        idx = dasha_planets.index(planet)
        return planet, dasha_durations[idx]
    except ValueError:
        logger.warning(f"Планета {planet} не найдена в списке даш. Используем Кету.")
        return "Кету", 7
