import asyncio
import logging
import os
import requests
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

# ⚠️ ВАЖНО: Если RENDER_EXTERNAL_URL не задан (например, при локальном запуске),
# используем заглушку. На продакшене Render должен добавлять эту переменную автоматически.
if not RENDER_URL:
    logging.error("❌ RENDER_EXTERNAL_URL не задан! Webhook не будет работать.")
    # Можно также задать вручную, но лучше настроить в Render:
    # RENDER_URL = "https://your-app.onrender.com"

WEBHOOK_URL = RENDER_URL + WEBHOOK_PATH if RENDER_URL else None

if not BOT_TOKEN or not WEATHER_API_KEY:
    raise ValueError("❌ Токены не заданы в переменных окружения!")

# ================== ИНИЦИАЛИЗАЦИЯ ==================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================== СОСТОЯНИЯ FSM ==================
class WeatherStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_days = State()

# ================== ФУНКЦИИ ПОГОДЫ ==================
async def get_current_weather(city: str) -> str:
    """Получает текущую погоду"""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        response = requests.get(url)
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
        # Получаем координаты города
        geo_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}"
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if geo_data.get("cod") != 200:
            return None
        
        lat = geo_data["coord"]["lat"]
        lon = geo_data["coord"]["lon"]
        city_name = geo_data["name"]
        
        # Запрашиваем прогноз
        forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        forecast_response = requests.get(forecast_url)
        forecast_data = forecast_response.json()
        
        if forecast_data.get("cod") != "200":
            return None
        
        # Группируем по дням
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
        
        # Формируем ответ
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

# ================== КЛАВИАТУРЫ ==================
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

def get_start_keyboard():
    """Красивая клавиатура для /start"""
    buttons = [
        [InlineKeyboardButton(text="🌤 Узнать погоду", callback_data="start_weather")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="start_help")],
        [InlineKeyboardButton(text="📢 Поделиться", switch_inline_query="бот погоды")]
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
    """Справка в Markdown"""
    await message.answer(
        "📋 *Доступные команды:*\n\n"
        "🔹 `/start` — приветствие\n"
        "🔹 `/help` — эта подсказка\n"
        "🔹 `/weather` — узнать погоду\n\n"
        "🌤 *Как пользоваться:*\n"
        "1. Нажми `/weather` или кнопку «Узнать погоду»\n"
        "2. Введи название города (например, `Москва`)\n"
        "3. Выбери количество дней (1–5)\n\n"
        "_Пример: /weather → Москва → 3 дня_",
        parse_mode="Markdown"
    )

@dp.message(Command("weather"))
async def cmd_weather(message: Message, state: FSMContext):
    """Начало запроса погоды"""
    await message.answer("🌍 Напиши название города (например, `Москва`, `Лондон`, `Токио`):", parse_mode="Markdown")
    await state.set_state(WeatherStates.waiting_for_city)

# ================== ОБРАБОТЧИКИ CALLBACK ==================
@dp.callback_query(lambda c: c.data == "start_weather")
async def callback_start_weather(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопки «Узнать погоду»"""
    await callback.message.answer("🌍 Напиши название города (например, `Москва`):", parse_mode="Markdown")
    await state.set_state(WeatherStates.waiting_for_city)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "start_help")
async def callback_start_help(callback: CallbackQuery):
    """Обработка кнопки «Помощь»"""
    await cmd_help(callback.message)
    await callback.answer()

# ================== ОБРАБОТЧИКИ СОСТОЯНИЙ ==================
@dp.message(WeatherStates.waiting_for_city)
async def process_city(message: Message, state: FSMContext):
    """Обработка введённого города"""
    city = message.text.strip()
    await state.update_data(city=city)
    
    await message.answer(
        f"📍 Город: *{city}*\n\nВыбери период прогноза:",
        parse_mode="Markdown",
        reply_markup=get_days_keyboard()
    )
    await state.set_state(WeatherStates.waiting_for_days)

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
    """Обработка ручного ввода числа дней"""
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

# ================== ОБРАБОТЧИК ВСЕГО ОСТАЛЬНОГО ==================
@dp.message()
async def handle_other_messages(message: Message):
    """Ответ на неизвестные сообщения"""
    await message.answer(
        "Я не понимаю эту команду.\n"
        "Используй `/weather` чтобы узнать погоду.",
        parse_mode="Markdown"
    )

# ================== WEBHOOK ==================
async def on_startup(app: web.Application):
    """Действия при запуске (теперь с параметром app)"""
    if not WEBHOOK_URL:
        logging.error("❌ WEBHOOK_URL не задан, пропускаю установку webhook")
        return
        
    logging.info(f"🔄 Устанавливаю webhook на {WEBHOOK_URL}")
    
    # Удаляем старый webhook и сбрасываем ожидающие обновления
    await bot.delete_webhook(drop_pending_updates=True)
    
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

async def on_shutdown(app: web.Application):
    """Действия при остановке (теперь с параметром app)"""
    logging.info("🔄 Останавливаю бота...")
    await bot.delete_webhook()
    await bot.session.close()
    logging.info("✅ Бот остановлен")

# ================== ЗАПУСК ==================
def main():
    """Главная функция запуска"""
    app = web.Application()
    
    # Настраиваем обработчик webhook
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    # Регистрируем обработчики жизненного цикла приложения
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    port = int(os.getenv("PORT", "10000"))  # Render ожидает порт 10000
    logging.info(f"🚀 Запуск сервера на порту {port}")
    
    # Добавляем простой обработчик для корневого пути (для проверки)
    async def health_check(request):
        return web.Response(text="Bot is running!")
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    return app

# ================== ТОЧКА ВХОДА ==================
if __name__ == "__main__":
    try:
        web.run_app(main(), host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
