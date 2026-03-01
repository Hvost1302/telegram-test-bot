import asyncio
import logging
import os
import requests
import re
import ephem
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ================== НАСТРОЙКА ==================
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
WEBHOOK_PATH = "/webhook"
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

if not RENDER_URL:
    RENDER_URL = "https://telegram-test-bot.onrender.com"  # ЗАМЕНИТЕ НА ВАШ URL
    logging.warning(f"⚠️ Использую запасной URL: {RENDER_URL}")

WEBHOOK_URL = RENDER_URL + WEBHOOK_PATH

if not BOT_TOKEN or not WEATHER_API_KEY:
    raise ValueError("❌ Токены не заданы в переменных окружения!")

# ================== ИНИЦИАЛИЗАЦИЯ ==================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================== СОСТОЯНИЯ FSM ==================
class WeatherStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_type = State()      # Новое состояние: выбор типа
    waiting_for_days = State()

# ================== ФУНКЦИИ ПОГОДЫ ==================
async def get_current_weather(city: str) -> str:
    """Получает текущую погоду с восходом/закатом и фазой луны"""
    try:
        logging.info(f"🌤 [get_current_weather] Запрос погоды для города: {city}")
        
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if data.get("cod") != 200:
            logging.error(f"❌ Ошибка API OpenWeatherMap: {data}")
            return None
        
        logging.info(f"✅ [get_current_weather] Данные получены для {data['name']}")
        
        # Получаем координаты для UV
        lat = data['coord']['lat']
        lon = data['coord']['lon']
        
        # Получаем UV-индекс
        uv_data = await get_uv_index(lat, lon)
        
        # ========== ВАЖНО: ПОЛУЧАЕМ ФАЗУ ЛУНЫ ==========
        logging.info("🌙 [get_current_weather] Вызываем get_moon_phase()")
        moon_phase = await get_moon_phase()
        logging.info(f"🌙 [get_current_weather] Результат get_moon_phase(): {moon_phase}")
        # ==============================================
        
        # Параметры ветра
        wind_deg = data.get("wind", {}).get("deg", 0)
        wind_dir = wind_direction_to_text(wind_deg)
        wind_arrow = wind_direction_to_arrow(wind_deg)
        wind_speed = data["wind"]["speed"]
        
        # Время восхода и заката
        timezone_offset = data.get('timezone', 0)
        sunrise_time = format_unix_time(data['sys']['sunrise'], timezone_offset)
        sunset_time = format_unix_time(data['sys']['sunset'], timezone_offset)
        
        # Совет по одежде
        weather_desc = data['weather'][0]['description']
        temp = data['main']['temp']
        clothing_advice = get_clothing_advice(temp, weather_desc, wind_speed)
        
        # Температура воды
        water_temp = await get_water_temperature(city)
        
        # Формируем сообщение
        weather_text = (
            f"🌤 *Текущая погода в {data['name']}*\n\n"
            f"🌡 Температура: {temp:.1f}°C (ощущается как {data['main']['feels_like']:.1f}°C)\n"
            f"📝 Описание: {weather_desc.capitalize()}\n"
            f"💧 Влажность: {data['main']['humidity']}%\n"
            f"🌬 Ветер: {wind_speed} м/с, {wind_arrow} {wind_dir}\n"
            f"📊 Давление: {data['main']['pressure']} гПа\n"
            f"🌅 Восход: {sunrise_time}\n"
            f"🌇 Закат: {sunset_time}\n"
        )
        
        # Добавляем фазу луны (ТОЛЬКО если она не None)
        if moon_phase:
            weather_text += f"{moon_phase}\n"
            logging.info(f"✅ Фаза луны добавлена в ответ: {moon_phase}")
        else:
            logging.warning("⚠️ Фаза луны не получена, пропускаем")
        
        weather_text += "\n"
        
        # Добавляем температуру воды
        if water_temp:
            weather_text += f"🌊 *Температура воды:* {water_temp:.1f}°C\n"
        
        # Добавляем UV-индекс
        if uv_data:
            weather_text += (
                f"☀️ *UV-индекс:* {uv_data['value']} ({uv_data['level']})\n"
                f"💡 *Совет:* {uv_data['advice']}\n\n"
            )
        
        weather_text += f"👔 *Совет по одежде:*\n{clothing_advice}"
        
        logging.info("✅ [get_current_weather] Сообщение сформировано успешно")
        return weather_text
        
    except Exception as e:
        logging.error(f"❌ Ошибка в get_current_weather: {e}")
        import traceback
        traceback.print_exc()
        return None

async def get_weather_forecast(city: str, days: int) -> str:
    """Получает прогноз на указанное количество дней"""
    try:
        geo_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}"
        geo_response = requests.get(geo_url, timeout=10)
        geo_data = geo_response.json()

        if geo_data.get("cod") != 200:
            return None

        lat = geo_data["coord"]["lat"]
        lon = geo_data["coord"]["lon"]
        city_name = geo_data["name"]
        timezone_offset = geo_data.get('timezone', 0)  # для корректировки времени

        # Используем One Call API для получения прогноза с фазами луны [citation:10]
        onecall_url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&exclude=current,minutely,hourly&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        onecall_response = requests.get(onecall_url, timeout=10)
        
        if onecall_response.status_code != 200:
            # Fallback на старый метод
            return await get_weather_forecast_fallback(city, days)
        
        forecast_data = onecall_response.json()
        
        if 'daily' not in forecast_data:
            return None
        
        # Формируем прогноз
        forecast_text = f"🌍 *Прогноз погоды для {city_name} на {days} дн.*\n\n"
        days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        
        for i in range(min(days, len(forecast_data['daily']))):
            day = forecast_data['daily'][i]
            date = datetime.fromtimestamp(day['dt']).date()
            
            # Температура
            temp = day['temp']
            temp_min = temp['min']
            temp_max = temp['max']
            avg_temp = (temp_min + temp_max) / 2
            
            # Ветер
            wind_speed = day.get('wind_speed', 0)
            wind_deg = day.get('wind_deg', 0)
            wind_dir = wind_direction_to_text(wind_deg)
            wind_arrow = wind_direction_to_arrow(wind_deg)
            
            # Описание погоды
            description = day['weather'][0]['description'].capitalize()
            
            # Влажность
            humidity = day.get('humidity', 0)
            
            # Вероятность осадков
            pop = day.get('pop', 0) * 100
            
            # Фаза луны [citation:6][citation:10]
            moon_phase = day.get('moon_phase', 0)
            moon_phase_name = get_moon_phase_name(moon_phase)
            
            # Восход и закат
            sunrise = format_unix_time(day['sunrise'], timezone_offset)
            sunset = format_unix_time(day['sunset'], timezone_offset)
            
            # Восход и заход луны [citation:10]
            moonrise = format_unix_time(day.get('moonrise', 0), timezone_offset) if day.get('moonrise') else "—"
            moonset = format_unix_time(day.get('moonset', 0), timezone_offset) if day.get('moonset') else "—"
            
            date_str = date.strftime("%d.%m")
            day_name = days_ru[date.weekday()]
            
            forecast_text += f"📅 *{date_str} ({day_name})*\n"
            forecast_text += f"🌡 {temp_min:.0f}…{temp_max:.0f}°C (ср. {avg_temp:.1f}°C)\n"
            forecast_text += f"☁️ {description}\n"
            forecast_text += f"💧 Влажность: {humidity}%\n"
            forecast_text += f"🌬 Ветер: {wind_speed:.1f} м/с {wind_arrow} {wind_dir}\n"
            forecast_text += f"🌅 {sunrise} | 🌇 {sunset}\n"
            forecast_text += f"{moon_phase_name} (🌅 {moonrise} / 🌇 {moonset})\n"
            
            if pop > 10:
                forecast_text += f"🌧 Вероятность осадков: {pop:.0f}%\n"
            
            forecast_text += "\n"
        
        return forecast_text

    except Exception as e:
        logging.error(f"Ошибка прогноза: {e}")
        return None

# Запасная функция, если One Call API недоступен
async def get_weather_forecast_fallback(city: str, days: int) -> str:
    """Старая версия прогноза без данных о луне"""
    """Получает прогноз на указанное количество дней"""
    try:
        geo_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}"
        geo_response = requests.get(geo_url, timeout=10)
        geo_data = geo_response.json()

        if geo_data.get("cod") != 200:
            return None

        lat = geo_data["coord"]["lat"]
        lon = geo_data["coord"]["lon"]
        city_name = geo_data["name"]

        forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        forecast_response = requests.get(forecast_url, timeout=10)
        forecast_data = forecast_response.json()

        if forecast_data.get("cod") != "200":
            return None

        # Группируем прогнозы по дням
        daily_data = {}
        today = datetime.now().date()

        for item in forecast_data["list"]:
            date = datetime.fromtimestamp(item["dt"]).date()

            if date == today:
                continue

            if date not in daily_data:
                daily_data[date] = {
                    "temps": [],
                    "descriptions": [],
                    "humidity": [],
                    "wind_speed": [],
                    "wind_deg": [],
                    "pop": []
                }

            daily_data[date]["temps"].append(item["main"]["temp"])
            daily_data[date]["descriptions"].append(item["weather"][0]["description"])
            daily_data[date]["humidity"].append(item["main"]["humidity"])
            daily_data[date]["wind_speed"].append(item["wind"]["speed"])
            daily_data[date]["wind_deg"].append(item["wind"].get("deg", 0))
            daily_data[date]["pop"].append(item.get("pop", 0))

        if not daily_data:
            return f"🌍 *Прогноз для {city_name}*\n\nНа ближайшие дни прогноз отсутствует."

        # Формируем прогноз
        forecast_text = f"🌍 *Прогноз погоды для {city_name} на {days} дн.*\n\n"
        days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

        for date, data in list(sorted(daily_data.items()))[:days]:
            temps = data["temps"]
            temp_min = min(temps)
            temp_max = max(temps)
            avg_temp = sum(temps) / len(temps)

            avg_humidity = sum(data["humidity"]) / len(data["humidity"])
            avg_wind_speed = sum(data["wind_speed"]) / len(data["wind_speed"])
            avg_wind_deg = sum(data["wind_deg"]) / len(data["wind_deg"])

            wind_dir = wind_direction_to_text(avg_wind_deg)
            wind_arrow = wind_direction_to_arrow(avg_wind_deg)

            description = max(set(data["descriptions"]), key=data["descriptions"].count).capitalize()
            avg_pop = sum(data["pop"]) / len(data["pop"]) * 100

            date_str = date.strftime("%d.%m")
            day_name = days_ru[date.weekday()]

            forecast_text += f"📅 *{date_str} ({day_name})*\n"
            forecast_text += f"🌡 {temp_min:.0f}…{temp_max:.0f}°C (ср. {avg_temp:.1f}°C)\n"
            forecast_text += f"☁️ {description}\n"
            forecast_text += f"💧 Влажность: {avg_humidity:.0f}%\n"
            forecast_text += f"🌬 Ветер: {avg_wind_speed:.1f} м/с {wind_arrow} {wind_dir}\n"

            # ВАЖНО: Этот блок должен быть строго внутри цикла for
            if avg_pop > 10:
                forecast_text += f"🌧 Вероятность осадков: {avg_pop:.0f}%\n"

            forecast_text += "\n"

        return forecast_text

    except Exception as e:
        logging.error(f"Ошибка прогноза: {e}")
        return None
         
    pass


async def get_moon_phase_calculated(date=None):
    """
    Рассчитывает фазу луны астрономическим методом (без API)
    """
    try:
        if date is None:
            date = datetime.now()
        
        # Создаем наблюдателя в Гринвиче
        observer = ephem.Observer()
        observer.date = date.strftime('%Y/%m/%d')
        
        # Получаем луну
        moon = ephem.Moon()
        moon.compute(observer)
        
        # ephem.moon_phase возвращает фазу от 0 до 1
        # 0 - новолуние, 0.5 - полнолуние
        phase = moon.moon_phase
        
        logging.info(f"🌙 [расчетная] Фаза луны: {phase:.3f}")
        
        if phase < 0.03 or phase > 0.97:
            return "🌑 Новолуние"
        elif phase < 0.22:
            return "🌒 Растущий серп"
        elif phase < 0.28:
            return "🌓 Первая четверть"
        elif phase < 0.47:
            return "🌔 Растущая луна"
        elif phase < 0.53:
            return "🌕 Полнолуние"
        elif phase < 0.72:
            return "🌖 Убывающая луна"
        elif phase < 0.78:
            return "🌗 Последняя четверть"
        else:
            return "🌘 Убывающий серп"
            
    except Exception as e:
        logging.error(f"🌙 [расчетная] Ошибка: {e}")
        return None
async def get_uv_index(lat: float, lon: float) -> dict:
    """
    Получает UV-индекс по координатам
    Возвращает словарь с значением и описанием или None
    """
    try:
        # Используем UV API OpenWeatherMap
        url = f"http://api.openweathermap.org/data/2.5/uvi"
        params = {
            'appid': WEATHER_API_KEY,
            'lat': lat,
            'lon': lon
        }
        
        logging.info(f"☀️ [UV] Запрос UV-индекса для координат: {lat}, {lon}")
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            logging.error(f"☀️ [UV] Ошибка HTTP: {response.status_code}")
            return None
        
        data = response.json()
        uv_value = data.get('value')
        
        if uv_value is None:
            logging.error("☀️ [UV] Нет значения UV-индекса в ответе")
            return None
        
        # Определяем уровень опасности по шкале ВОЗ [citation:5]
        if uv_value <= 2:
            level = "🟢 Низкий"
            advice = "Можно находиться на солнце без защиты"
        elif uv_value <= 5:
            level = "🟡 Средний"
            advice = "Используйте солнцезащитный крем, носите головной убор"
        elif uv_value <= 7:
            level = "🟠 Высокий"
            advice = "Обязательно используйте защиту, избегайте полуденного солнца"
        elif uv_value <= 10:
            level = "🔴 Очень высокий"
            advice = "Сведите пребывание на солнце к минимуму, необходима усиленная защита"
        else:
            level = "🟣 Экстремальный"
            advice = "Избегайте выхода на улицу в середине дня!"
        
        return {
            'value': uv_value,
            'level': level,
            'advice': advice
        }
        
    except Exception as e:
        logging.error(f"☀️ [UV] Ошибка: {e}")
        return None

def get_moon_phase_name(moon_phase_value: float) -> str:
    """
    Преобразует числовое значение фазы луны (0-1) в текстовое описание и эмодзи
    Значения из OpenWeatherMap: 0 - новолуние, 0.25 - первая четверть, 
    0.5 - полнолуние, 0.75 - последняя четверть [citation:2][citation:6]
    """
    if moon_phase_value == 0 or moon_phase_value == 1:
        return "🌑 Новолуние"
    elif moon_phase_value == 0.25:
        return "🌓 Первая четверть"
    elif moon_phase_value == 0.5:
        return "🌕 Полнолуние"
    elif moon_phase_value == 0.75:
        return "🌗 Последняя четверть"
    elif 0 < moon_phase_value < 0.25:
        return "🌒 Растущий серп"
    elif 0.25 < moon_phase_value < 0.5:
        return "🌔 Растущая луна"
    elif 0.5 < moon_phase_value < 0.75:
        return "🌖 Убывающая луна"
    elif 0.75 < moon_phase_value < 1:
        return "🌘 Убывающий серп"
    else:
        return "🌙 Луна"


# ================== ФУНКЦИЯ ДЛЯ ОПРЕДЕЛЕНИЯ ТЕМПЕРАТУРЫ ВОДЫ ==================

async def get_water_temperature(city: str) -> float:
    """
    Получает температуру воды для прибрежных городов
    Возвращает температуру в °C или None, если данные недоступны
    """
    # Координаты популярных курортов Крыма (ключи на кириллице)
    crimean_beaches = {
        'севастополь': {'lat': 44.6, 'lon': 33.53},
        'симферополь': None,  # Не на море
        'ялта': {'lat': 44.5, 'lon': 34.17},
        'алушта': {'lat': 44.68, 'lon': 34.42},
        'евпатория': {'lat': 45.19, 'lon': 33.37},
        'феодосия': {'lat': 45.05, 'lon': 35.38},
        'судак': {'lat': 44.85, 'lon': 34.97},
        'саки': {'lat': 45.13, 'lon': 33.58},
        'керчь': {'lat': 45.36, 'lon': 36.48},
    }
    
    # Транслитерация с латиницы на кириллицу для ключей словаря
    translit_map = {
        'yalta': 'ялта',
        'y alta': 'ялта',
        'yalta': 'ялта',
        'sevastopol': 'севастополь',
        'sevastopol': 'севастополь',
        'simferopol': 'симферополь',
        'alushta': 'алушта',
        'evpatoria': 'евпатория',
        'feodosia': 'феодосия',
        'sudak': 'судак',
        'saki': 'саки',
        'kerch': 'керчь',
    }
    
    city_lower = city.lower().strip()
    logging.info(f"🌊 [get_water_temp] Проверка города: '{city_lower}'")
    
    # Проверяем, может быть город уже на кириллице
    if city_lower in crimean_beaches:
        coords = crimean_beaches[city_lower]
    # Если нет, пробуем транслитерацию
    elif city_lower in translit_map:
        russian_name = translit_map[city_lower]
        logging.info(f"🌊 [get_water_temp] Транслитерация: '{city_lower}' -> '{russian_name}'")
        coords = crimean_beaches.get(russian_name)
    else:
        logging.info(f"🌊 [get_water_temp] Город '{city_lower}' не найден в списке приморских")
        return None
    
    if not coords:
        logging.info(f"🌊 [get_water_temp] Город '{city_lower}' не на море")
        return None
    
    logging.info(f"🌊 [get_water_temp] Город найден, координаты: {coords}")
    
    # Получаем ключ
    WWO_KEY = os.getenv("WWO_API_KEY")
    if not WWO_KEY:
        logging.error("🌊 [get_water_temp] ❌ WWO_API_KEY не найден в переменных окружения!")
        return None
    
    logging.info(f"🌊 [get_water_temp] Ключ найден: {WWO_KEY[:5]}...{WWO_KEY[-5:]}")
    
    try:
        url = "https://api.worldweatheronline.com/premium/v1/marine.ashx"
        
        params = {
            'key': WWO_KEY,
            'q': f"{coords['lat']},{coords['lon']}",
            'format': 'json',
            'tp': '24',
            'lang': 'ru'
        }
        
        logging.info(f"🌊 [get_water_temp] Отправляю запрос к: {url}")
        logging.info(f"🌊 [get_water_temp] Параметры: {params}")
        
        response = requests.get(url, params=params, timeout=15)
        
        logging.info(f"🌊 [get_water_temp] Статус ответа: {response.status_code}")
        
        if response.status_code != 200:
            logging.error(f"🌊 [get_water_temp] ❌ Ошибка HTTP: {response.status_code}")
            logging.error(f"🌊 [get_water_temp] Тело ответа: {response.text[:200]}")
            return None
        
        data = response.json()
        
        if 'data' not in data or 'weather' not in data['data']:
            logging.error(f"🌊 [get_water_temp] ❌ Неожиданный формат ответа")
            return None
        
        water_temp = data['data']['weather'][0]['hourly'][0]['waterTemp_C']
        logging.info(f"🌊 [get_water_temp] ✅ Температура воды: {water_temp}°C")
        
        return float(water_temp)
        
    except Exception as e:
        logging.error(f"🌊 [get_water_temp] ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None

# ================== ФУНКЦИЯ ДЛЯ ПРЕОБРАЗОВАНИЯ ГРАДУСОВ В НАПРАВЛЕНИЕ ВЕТРА ==================

def wind_direction_to_text(degrees: float) -> str:
    """
    Преобразует направление ветра в градусах в текстовое описание
    """
    directions = [
        "северный", "северо-восточный", "восточный", "юго-восточный",
        "южный", "юго-западный", "западный", "северо-западный"
    ]
    
    # Преобразуем градусы в индекс массива (0-7)
    # Каждые 45 градусов - новое направление
    index = round(degrees / 45) % 8
    
    return directions[index]

def wind_direction_to_arrow(degrees: float) -> str:
    """
    Возвращает стрелку для визуализации направления ветра
    """
    arrows = ["⬆️", "↗️", "➡️", "↘️", "⬇️", "↙️", "⬅️", "↖️"]
    index = round(degrees / 45) % 8
    return arrows[index]

# ================== СОВЕТЫ ПО ОДЕЖДЕ ==================

def get_clothing_advice(temp, weather_desc, wind_speed):
    """Дает рекомендации по одежде на основе погоды"""
    advice = []
    
    # Температурные рекомендации
    if temp < -20:
        advice.append("🥶 Экстремально холодно! Арктический пуховик, термобелье, две шапки!")
    elif temp < -10:
        advice.append("🥶 Очень холодно! Пуховик, шапка, шарф, перчатки обязательны")
    elif temp < 0:
        advice.append("🧥 Холодно. Зимняя куртка, теплая обувь, шапка")
    elif temp < 10:
        advice.append("🧥 Прохладно. Осенняя куртка или пальто, можно шапку")
    elif temp < 15:
        advice.append("🧥 Свежо. Ветровка или свитер, джинсы")
    elif temp < 20:
        advice.append("👕 Комфортно. Легкая кофта или ветровка")
    elif temp < 25:
        advice.append("👕 Тепло. Футболка, шорты/джинсы, кеды")
    else:
        advice.append("👕 Жарко! Майка, шорты, головной убор, вода с собой")
    
    # Осадки
    if "дождь" in weather_desc.lower():
        advice.append("☔️ Обязательно возьми зонтик!")
    elif "снег" in weather_desc.lower():
        advice.append("❄️ Идет снег - надень непромокаемую обувь")
    
    # Ветер
    if wind_speed > 15:
        advice.append("💨 Ураганный ветер! Прячь лицо и застегивайся наглухо")
    elif wind_speed > 10:
        advice.append("💨 Сильный ветер - застегни куртку, ветровка пригодится")
    elif wind_speed > 5:
        advice.append("🍃 Легкий ветерок, но может продувать - имей это в виду")
    
    return "\n".join(advice)


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

def format_unix_time(timestamp: int, timezone_offset: int = 0) -> str:
    """
    Форматирует UNIX timestamp в читаемое время с учетом часового пояса
    OpenWeatherMap возвращает время в UTC 
    """
    from datetime import timezone, timedelta
    
    # Создаем объект datetime из timestamp
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    
    # Добавляем смещение часового пояса (в секундах)
    dt_local = dt + timedelta(seconds=timezone_offset)
    
    # Возвращаем время в формате ЧЧ:ММ
    return dt_local.strftime("%H:%M")

def clean_city_name(city: str) -> str:
    """Очищает название города от падежных окончаний"""
    # Словарь частых замен
    replacements = {
        'москве': 'Москва',
        'москва': 'Москва',
        'питере': 'Санкт-Петербург',
        'спб': 'Санкт-Петербург',
        'петербурге': 'Санкт-Петербург',
        'ленинграде': 'Санкт-Петербург',
        'ленинград': 'Санкт-Петербург',
        'киев': 'Киев',
        'киеве': 'Киев',
        'минск': 'Минск',
        'минске': 'Минск',
        'лондон': 'London',
        'лондоне': 'London',
        'париж': 'Paris',
        'париже': 'Paris',
        'берлин': 'Berlin',
        'берлине': 'Berlin',
        'рим': 'Rome',
        'риме': 'Rome',
        'токио': 'Tokyo',
        'пекин': 'Beijing',
        'пекине': 'Beijing',
        'севастополь': 'Sevastopol',
        'севастополе': 'Sevastopol',
        'симферополь': 'Simferopol',
        'симферополе': 'Simferopol',
        'ялта': 'Yalta',
        'ялте': 'Yalta',
    }
    
    city_lower = city.lower()
    if city_lower in replacements:
        return replacements[city_lower]
    
    # Если город заканчивается на 'е', 'а', 'у', 'и' - базовая очистка
    endings = ['е', 'у', 'а', 'и', 'ы']
    if any(city_lower.endswith(e) for e in endings):
        # Для русских городов убираем окончание
        if city_lower.endswith('е') or city_lower.endswith('у'):
            return city[:-1]
        if city_lower.endswith('а') or city_lower.endswith('ы'):
            # Сохраняем первую букву заглавной
            return city[:-1] + 'а' if city_lower.endswith('ы') else city[:-1]
    
    # Особые случаи
    if city_lower.endswith('ии'):  # например "в ялте" -> "ялта"
        return city[:-2] + 'а'
    
    return city

def extract_days_from_query(text: str) -> int:
    """
    Извлекает количество дней из текста запроса.
    Возвращает число дней (1-5) или None, если не указано.
    """
    text_lower = text.lower()
    
    # Паттерны для поиска дней
    days_patterns = [
        r'на\s+(\d+)\s+день',      # "на 5 дней", "на 3 дня"
        r'на\s+(\d+)\s+дня',
        r'на\s+(\d+)\s+дней',
        r'(\d+)\s+день',            # "5 дней"
        r'(\d+)\s+дня',
        r'(\d+)\s+дней',
        r'прогноз\s+на\s+(\d+)',    # "прогноз на 5"
        r'на\s+(\d+)',               # "на 5"
    ]
    
    for pattern in days_patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                days = int(match.group(1))
                # Ограничиваем от 1 до 5 дней
                return max(1, min(days, 5))
            except:
                pass
    
    return None  # дни не указаны

# ================== КЛАВИАТУРЫ ==================
def get_start_keyboard():
    """Красивая клавиатура для /start"""
    buttons = [
        [InlineKeyboardButton(text="🌤 Узнать погоду", callback_data="start_weather")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="start_help")],
        [InlineKeyboardButton(text="📢 Поделиться", switch_inline_query="бот погоды")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_weather_type_keyboard():
    """Клавиатура для выбора типа прогноза"""
    buttons = [
        [InlineKeyboardButton(text="🌤 Текущая погода", callback_data="type_current")],
        [InlineKeyboardButton(text="📅 Прогноз на дни", callback_data="type_forecast")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="type_cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_days_keyboard():
    """Клавиатура для выбора количества дней"""
    buttons = [
        [
            InlineKeyboardButton(text="1 день", callback_data="days_1"),
            InlineKeyboardButton(text="2 дня", callback_data="days_2"),
            InlineKeyboardButton(text="3 дня", callback_data="days_3")
        ],
        [
            InlineKeyboardButton(text="4 дня", callback_data="days_4"),
            InlineKeyboardButton(text="5 дней", callback_data="days_5"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="days_cancel")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ================== ОБРАБОТЧИКИ КОМАНД ==================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Приветствие с красивыми inline-кнопками"""
    await message.answer(
        "👋 *Привет! Я бот с прогнозом погоды*\n\n"
        "🔍 Нажми кнопку ниже, чтобы узнать погоду в любом городе мира!\n"
        "📅 Прогноз доступен на 1–5 дней.",
        parse_mode="Markdown",
        reply_markup=get_start_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Справка с реально кликабельными командами"""
    bot_username = (await bot.get_me()).username
    
    # Текст с командами-ссылками
    await message.answer(
        f'📋 <b>Доступные команды:</b>\n\n'
        f'🔹 <a href="tg://resolve?domain={bot_username}&command=start">/start</a> — приветствие\n'
        f'🔹 <a href="tg://resolve?domain={bot_username}&command=help">/help</a> — эта подсказка\n'
        f'🔹 <a href="tg://resolve?domain={bot_username}&command=weather">/weather</a> — узнать погоду\n\n'
        f'🌤 <b>Как пользоваться:</b>\n'
        f'1. Нажми /weather или кнопку ниже\n'
        f'2. Введи название города (например, Москва)\n'
        f'3. Выбери количество дней (1–5)',
        parse_mode="HTML"
    )
    
    # Кнопки для быстрого доступа
    help_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌤 Узнать погоду", callback_data="start_weather")],
        [InlineKeyboardButton(text="📢 Поделиться ботом", switch_inline_query="бот погоды")]
    ])
    
    await message.answer(
        "🌟 Выбери действие:",
        reply_markup=help_keyboard
    )

@dp.message(Command("weather"))
async def cmd_weather(message: Message, state: FSMContext):
    await message.answer("🌍 Напиши название города (например, `Москва`, `Лондон`, `Токио`):", parse_mode="Markdown")
    await state.set_state(WeatherStates.waiting_for_city)

@dp.callback_query(lambda c: c.data.startswith('quick_forecast_'))
async def callback_quick_forecast(callback: CallbackQuery):
    """Обработка быстрого перехода к прогнозу"""
    # Формат: quick_forecast_город_дни
    parts = callback.data.split('_')
    if len(parts) >= 4:
        city = parts[2]
        days = int(parts[3])
        
        await callback.message.answer(f"🔍 Получаю прогноз на *{days}* дн. для *{city}*...", parse_mode="Markdown")
        
        forecast = await get_weather_forecast(city, days)
        if forecast:
            await callback.message.answer(forecast, parse_mode="Markdown")
        else:
            await callback.message.answer("❌ Не удалось получить прогноз.", parse_mode="Markdown")
    
    await callback.answer()

# ================== ОБРАБОТЧИКИ СОСТОЯНИЙ ==================
@dp.message(WeatherStates.waiting_for_city)
async def process_city(message: Message, state: FSMContext):
    """Обработка введённого города"""
    city = message.text.strip()
    city_clean = clean_city_name(city)
    
    # Проверяем, не было ли сохранено количество дней
    data = await state.get_data()
    requested_days = data.get("requested_days")
    
    if requested_days:
        # Если пользователь запросил прогноз на определенное количество дней
        await message.answer(f"🔍 Получаю прогноз на *{requested_days}* дн. для *{city_clean}*...", parse_mode="Markdown")
        
        forecast = await get_weather_forecast(city_clean, requested_days)
        if forecast:
            await message.answer(forecast, parse_mode="Markdown")
        else:
            # Если прогноз не работает, показываем текущую погоду
            weather = await get_current_weather(city_clean)
            if weather:
                await message.answer(
                    f"⚠️ Не удалось получить прогноз, но вот текущая погода:\n\n{weather}",
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                    f"❌ Не удалось найти город *{city_clean}*.", 
                    parse_mode="Markdown"
                )
        
        await state.clear()
        return
    
    # Стандартный поток: город -> выбор типа (текущая/прогноз)
    await state.update_data(city=city_clean)
    
    await message.answer(
        f"📍 Город: *{city_clean}*\n\nЧто показать?",
        parse_mode="Markdown",
        reply_markup=get_weather_type_keyboard()
    )
    await state.set_state(WeatherStates.waiting_for_type)
    
@dp.callback_query(WeatherStates.waiting_for_days)
async def process_days_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора дней через кнопки"""
    action = callback.data
    
    if action == "days_cancel":
        await callback.message.edit_text("❌ Запрос отменён.")
        await state.clear()
        await callback.answer()
        return
    
    days = int(action.split("_")[1])
    data = await state.get_data()
    city = data.get("city")
    
    await callback.message.edit_text(f"🔍 Получаю прогноз на *{days}* дн. для *{city}*...", parse_mode="Markdown")
    
    forecast = await get_weather_forecast(city, days)
    
    if forecast:
        await callback.message.answer(forecast, parse_mode="Markdown")
    else:
        # Если прогноз не работает, пробуем текущую погоду
        current = await get_current_weather(city)
        if current:
            await callback.message.answer(
                f"⚠️ Не удалось получить прогноз, но вот текущая погода:\n\n{current}",
                parse_mode="Markdown"
            )
        else:
            await callback.message.answer(
                "❌ Город не найден. Проверь название и попробуй `/weather` снова.",
                parse_mode="Markdown"
            )
    
    await state.clear()
    await callback.answer()

@dp.message(WeatherStates.waiting_for_days)
async def process_days_text(message: Message, state: FSMContext):
    if message.text.isdigit():
        days = int(message.text)
        if 1 <= days <= 5:
            data = await state.get_data()
            city = data.get("city")
            
            await message.answer(f"🔍 Получаю прогноз на *{days}* дн. для *{city}*...", parse_mode="Markdown")
            
            forecast = await get_weather_forecast(city, days)
            
            if forecast:
                await message.answer(forecast, parse_mode="Markdown")
            else:
                current = await get_current_weather(city)
                if current:
                    await message.answer(
                        f"⚠️ Вот текущая погода вместо прогноза:\n\n{current}",
                        parse_mode="Markdown"
                    )
                else:
                    await message.answer(
                        "❌ Не удалось получить данные о погоде. Попробуй позже.",
                        parse_mode="Markdown"
                    )
            
            await state.clear()
        else:
            await message.answer("❌ Введи число от 1 до 5 или выбери на клавиатуре.")
    else:
        await message.answer("Пожалуйста, выбери количество дней на клавиатуре или введи число от 1 до 5.")


# ================== ОБРАБОТЧИК ВЫБОРА ТИПА ==================

@dp.callback_query(WeatherStates.waiting_for_type)
async def process_type_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа прогноза"""
    action = callback.data
    data = await state.get_data()
    city = data.get("city")
    
    if action == "type_cancel":
        await callback.message.edit_text("❌ Запрос отменён.")
        await state.clear()
        await callback.answer()
        return
    
    if action == "type_current":
        # Показываем текущую погоду
        await callback.message.edit_text(f"🔍 Получаю текущую погоду для *{city}*...", parse_mode="Markdown")
        
        current = await get_current_weather(city)
        
        if current:
            await callback.message.answer(current, parse_mode="Markdown")
        else:
            await callback.message.answer(
                "❌ Не удалось получить погоду. Проверь название города.",
                parse_mode="Markdown"
            )
        
        await state.clear()
        await callback.answer()
        return
    
    if action == "type_forecast":
        # Переходим к выбору дней
        await callback.message.edit_text(
            f"📍 Город: *{city}*\n\nВыбери количество дней:",
            parse_mode="Markdown",
            reply_markup=get_days_keyboard()
        )
        await state.set_state(WeatherStates.waiting_for_days)
        await callback.answer()

# ================== ОБРАБОТЧИКИ CALLBACK ==================
@dp.callback_query(lambda c: c.data == "start_weather")
async def callback_start_weather(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопки «Узнать погоду»"""
    await callback.message.answer("🌍 Напиши название города (например, `Москва`):", parse_mode="Markdown")
    await state.set_state(WeatherStates.waiting_for_city)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "start_help")
async def callback_start_help(callback: CallbackQuery):
    await cmd_help(callback.message)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "share_bot")
async def callback_share_bot(callback: CallbackQuery):
    """Обработка кнопки поделиться"""
    bot_username = (await bot.get_me()).username
    share_text = f"Отличный бот с прогнозом погоды! 👉 @{bot_username}"
    
    # Создаем кнопку для отправки другу
    share_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Отправить другу", switch_inline_query=share_text)]
    ])
    
    await callback.message.answer(
        "Нажми кнопку ниже, чтобы отправить бота другу:",
        reply_markup=share_keyboard
    )
    await callback.answer()




# ================== ОБРАБОТЧИК КЛЮЧЕВЫХ СЛОВ ==================


@dp.message(F.text, ~F.text.startswith('/'))
async def smart_reply(message: Message, state: FSMContext):
    text_lower = message.text.lower()
    
    # Проверяем, не находится ли пользователь уже в диалоге
    current_state = await state.get_state()
    if current_state is not None:
        return
    
    # Приветствия
    greetings = ["привет", "здравствуй", "хай", "hello", "добрый", "доброе", "добрый день", "здравствуйте"]
    if any(word in text_lower for word in greetings):
        await message.answer(
            "👋 Привет! Я бот погоды.\n"
            "Напиши 'погода' и название города, например: `погода Москва`\n"
            "Или используй /weather",
            parse_mode="Markdown"
        )
        return
    
    # Запрос погоды
    if "погода" in text_lower or "прогноз" in text_lower:
        city_found = None
        
        # Пробуем извлечь город из разных паттернов
        city_patterns = [
            r'погода\s+в\s+(\w+)',
            r'прогноз\s+в\s+(\w+)',
            r'погода\s+(\w+)',
            r'прогноз\s+(\w+)',
            r'(\w+)\s+погода',
            r'(\w+)\s+прогноз',
        ]
        
        for pattern in city_patterns:
            match = re.search(pattern, text_lower)
            if match:
                city_found = match.group(1)
                break
        
        # Проверяем, указано ли количество дней
        days = extract_days_from_query(text_lower)
        
        if city_found:
            # Очищаем название города
            city_clean = clean_city_name(city_found)
            
            logging.info(f"🔍 Запрос: город='{city_clean}', дней={days}")
            
            if days:
                # Запрашиваем прогноз на указанное количество дней
                await message.answer(f"🔍 Получаю прогноз на *{days}* дн. для *{city_clean}*...", parse_mode="Markdown")
                forecast = await get_weather_forecast(city_clean, days)
                
                if forecast:
                    await message.answer(forecast, parse_mode="Markdown")
                else:
                    weather = await get_current_weather(city_clean)
                    if weather:
                        await message.answer(
                            f"⚠️ Не удалось получить прогноз, но вот текущая погода:\n\n{weather}",
                            parse_mode="Markdown"
                        )
                    else:
                        await message.answer(
                            f"❌ Не удалось найти город *{city_clean}*. Проверь название.",
                            parse_mode="Markdown"
                        )
            else:
                # Показываем текущую погоду
                await message.answer(f"🔍 Получаю погоду для *{city_clean}*...", parse_mode="Markdown")
                weather = await get_current_weather(city_clean)
                
                if weather:
                    await message.answer(weather, parse_mode="Markdown")
                    
                    # Добавляем кнопку для быстрого перехода к прогнозу
                    forecast_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text=f"📅 Прогноз на 5 дней для {city_clean}", 
                            callback_data=f"quick_forecast_{city_clean}_5"
                        )]
                    ])
                    
                    await message.answer(
                        "Хочешь увидеть прогноз на несколько дней?",
                        reply_markup=forecast_keyboard
                    )
                else:
                    await message.answer(
                        f"❌ Не удалось найти город *{city_clean}*. Проверь название.",
                        parse_mode="Markdown"
                    )
            return
        
        # Если город не извлекли
        days = extract_days_from_query(text_lower)
        if days:
            await message.answer(
                f"🌍 Для какого города показать прогноз на {days} дней?",
                parse_mode="Markdown"
            )
            await state.set_state(WeatherStates.waiting_for_city)
            await state.update_data(requested_days=days)
            return
        
        # Просто запрос погоды без города
        await message.answer("🌍 Напиши название города (например, `Москва`):", parse_mode="Markdown")
        await state.set_state(WeatherStates.waiting_for_city)
        return

# ================== ОБРАБОТЧИК ВСЕГО ОСТАЛЬНОГО ==================
@dp.message()
async def handle_other_messages(message: Message):
    await message.answer(
        "Я не понимаю эту команду.\n"
        "Используй `/weather` чтобы узнать погоду.",
        parse_mode="Markdown"
    )

# ================== ГЛАВНАЯ ФУНКЦИЯ ==================
async def init_webhook():
    """Отдельная функция для инициализации webhook"""
    logging.info("🔄 Инициализация webhook...")
    
    # Удаляем старый webhook
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("✅ Старый webhook удалён")
    
    # Устанавливаем новый
    success = await bot.set_webhook(
        url=WEBHOOK_URL,
        allowed_updates=["message", "callback_query"],
        max_connections=40
    )
    
    if success:
        bot_info = await bot.get_me()
        logging.info(f"✅ Webhook установлен для @{bot_info.username}")
        logging.info(f"📎 URL: {WEBHOOK_URL}")
    else:
        logging.error("❌ Не удалось установить webhook")

async def cleanup():
    """Очистка при остановке"""
    logging.info("🔄 Останавливаю бота...")
    await bot.delete_webhook()
    await bot.session.close()
    logging.info("✅ Бот остановлен")

async def handle_root(request):
    """Обработчик для проверки работы"""
    return web.Response(text="Bot is running! Webhook is active.")

async def main():
    """Создание и настройка приложения"""
    app = web.Application()
    
    # Добавляем обработчик для проверки
    app.router.add_get('/', handle_root)
    app.router.add_get('/health', handle_root)
    
    # Настраиваем webhook
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    # ЯВНО вызываем инициализацию webhook перед запуском сервера
    await init_webhook()
    
    return app

# ================== ТОЧКА ВХОДА ==================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    logging.info(f"🚀 Запуск сервера на порту {port}")
    
    # Запускаем приложение
    try:
        web.run_app(main(), host="0.0.0.0", port=port)
    except KeyboardInterrupt:
        asyncio.run(cleanup())
        logging.info("Бот остановлен пользователем")
    finally:
        logging.info("Завершение работы")






