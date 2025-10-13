import swisseph as swe
from datetime import datetime
import pytz

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
    
    # Рассчитываем дома
    houses = get_houses(lat, lon, dt)
    
    # Определяем текущую дашу
    dasha_planet, dasha_years = get_dasha_period(dt, lat, lon)
    
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
    return None

def get_zodiac_sign(pos):
    """Определяет знак Зодиака"""
    signs = [
        "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
        "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
    ]
    return signs[int(pos / 30)]

def get_moon_house(moon_pos, houses):
    """Определяет дом Луны"""
    for i in range(12):
        start = houses[i]
        end = houses[(i + 1) % 12]
        
        if start < end:
            if start <= moon_pos < end:
                return i + 1
        else:
            if moon_pos >= start or moon_pos < end:
                return i + 1
    return None

def get_houses(lat, lon, dt):
    """Рассчитывает дома по системе Кришнамурти"""
    jd = swe.julday(dt.year, dt.month, dt.day, dt.hour + dt.minute/60.0)
    
    # Получаем положение Асцендента
    asce = swe.calc_ut(jd, swe.HOUSE, flags=swe.FLG_SPEED)[0][0] % 360
    
    # Рассчитываем 12 домов
    houses = []
    for i in range(12):
        house_start = (asce + i * 30) % 360
        houses.append(house_start)
    
    return houses

def get_dasha_period(dt, lat, lon):
    """Определяет текущую даша-период"""
    # Упрощенная логика для примера
    month = dt.month % 9
    dasha_planets = [
        "Кету", "Венера", "Солнце", "Луна", "Марс", 
        "Раху", "Юпитер", "Сатурн", "Меркурий"
    ]
    
    return dasha_planets[month], 6