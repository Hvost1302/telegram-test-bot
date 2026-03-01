import asyncio
import logging
import os
import requests
import re
from datetime import datetime
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
    """Получает текущую погоду"""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if data.get("cod") != 200:
            logging.error(f"Ошибка API: {data}")
            return None
        
        return (
            f"🌤 *Текущая погода в {data['name']}*\n\n"
            f"🌡 Температура: {data['main']['temp']:.1f}°C (ощущается как {data['main']['feels_like']:.1f}°C)\n"
            f"📝 Описание: {data['weather'][0]['description'].capitalize()}\n"
            f"💧 Влажность: {data['main']['humidity']}%\n"
            f"🌬 Ветер: {data['wind']['speed']} м/с"
        )
    except Exception as e:
        logging.error(f"Ошибка: {e}")
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
        
        forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        forecast_response = requests.get(forecast_url, timeout=10)
        forecast_data = forecast_response.json()
        
        if forecast_data.get("cod") != "200":
            return None
        
        daily_forecasts = {}
        today = datetime.now().date()
        
        for item in forecast_data["list"]:
            date = datetime.fromtimestamp(item["dt"]).date()
            if date == today:
                continue
            
            if date not in daily_forecasts and len(daily_forecasts) < days:
                daily_forecasts[date] = {
                    "temp_min": item["main"]["temp_min"],
                    "temp_max": item["main"]["temp_max"],
                    "description": item["weather"][0]["description"],
                    "humidity": item["main"]["humidity"],
                    "wind": item["wind"]["speed"]
                }
        
        if not daily_forecasts:
            return f"🌍 *Прогноз для {city_name}*\n\nНа ближайшие дни прогноз отсутствует."
        
        forecast_text = f"🌍 *Прогноз погоды для {city_name} на {days} дн.*\n\n"
        days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        
        for date in sorted(daily_forecasts.keys())[:days]:
            day = daily_forecasts[date]
            date_str = date.strftime("%d.%m")
            day_name = days_ru[date.weekday()]
            
            forecast_text += (
                f"📅 *{date_str} ({day_name})*\n"
                f"🌡 {day['temp_min']:.0f}…{day['temp_max']:.0f}°C\n"
                f"☁️ {day['description'].capitalize()}\n"
                f"💧 Влажность: {day['humidity']}%, 🌬 Ветер: {day['wind']:.1f} м/с\n\n"
            )
        
        return forecast_text
    except Exception as e:
        logging.error(f"Ошибка прогноза: {e}")
        return None

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

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







