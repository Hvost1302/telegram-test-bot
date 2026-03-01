import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, Update
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токены из переменных окружения (на Render будем их задавать)
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") + WEBHOOK_PATH  # Render сам подставит URL

# Проверяем, что токены заданы
if not BOT_TOKEN:
    raise ValueError("Нет BOT_TOKEN в переменных окружения!")
if not WEATHER_API_KEY:
    raise ValueError("Нет WEATHER_API_KEY в переменных окружения!")

# Создаем объекты бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# СОСТОЯНИЯ ДЛЯ FSM
class WeatherStates(StatesGroup):
    waiting_for_city = State()

# Функция получения погоды (та же самая)
async def get_weather(city: str) -> str:
    """Получает погоду для указанного города"""
    try:
        import requests
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        response = requests.get(url)
        data = response.json()
        
        if data.get("cod") != 200:
            logging.error(f"Ошибка API: {data}")
            return None
        
        city_name = data["name"]
        temp = data["main"]["temp"]
        feels_like = data["main"]["feels_like"]
        description = data["weather"][0]["description"].capitalize()
        humidity = data["main"]["humidity"]
        wind_speed = data["wind"]["speed"]
        
        weather_text = (
            f"🌤 <b>Погода в {city_name}</b>\n\n"
            f"🌡 Температура: {temp:.1f}°C (ощущается как {feels_like:.1f}°C)\n"
            f"📝 Описание: {description}\n"
            f"💧 Влажность: {humidity}%\n"
            f"🌬 Ветер: {wind_speed} м/с"
        )
        return weather_text
    except Exception as e:
        logging.error(f"Ошибка при запросе погоды: {e}")
        return None

# Обработчик /start
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот с прогнозом погоды.\n"
        "Используй /weather чтобы узнать погоду в любом городе."
    )

# Обработчик /help
@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Доступные команды:\n"
        "/start - Приветствие\n"
        "/help - Эта подсказка\n"
        "/weather - Узнать погоду"
    )

# Обработчик /weather - вход в FSM
@dp.message(Command("weather"))
async def cmd_weather(message: Message, state: FSMContext):
    await message.answer("🌍 Напишите название города:")
    await state.set_state(WeatherStates.waiting_for_city)

# Обработчик сообщений в состоянии waiting_for_city
@dp.message(WeatherStates.waiting_for_city)
async def process_city(message: Message, state: FSMContext):
    city = message.text.strip()
    await message.answer(f"🔍 Ищу погоду в городе {city}...")
    
    weather_info = await get_weather(city)
    
    if weather_info:
        await message.answer(weather_info, parse_mode="HTML")
    else:
        await message.answer("❌ Не удалось найти город. Проверьте название и попробуйте снова.\n"
                            "Используйте /weather для нового запроса.")
    
    await state.clear()

# Обработчик всех остальных сообщений
@dp.message()
async def handle_other_messages(message: Message):
    await message.answer("Я не понимаю эту команду. Используйте /help для списка команд.")

# Настройка webhook при запуске
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен на {WEBHOOK_URL}")

# Очистка webhook при остановке
async def on_shutdown():
    await bot.delete_webhook()
    logging.info("Webhook удален")

# Главная функция
async def main():
    # Создаем aiohttp приложение
    app = web.Application()
    
    # Настраиваем обработчик webhook
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    
    # Регистрируем путь для webhook
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    # Регистрируем функции запуска/остановки
    app.on_startup.append(lambda _: on_startup())
    app.on_shutdown.append(lambda _: on_shutdown())
    
    # Получаем порт из переменной окружения (Render задает PORT)
    port = int(os.getenv("PORT", "8000"))
    
    # Запускаем веб-сервер
    logging.info(f"Запуск сервера на порту {port}")
    return app

# Точка входа для aiohttp
if __name__ == "__main__":
    try:
        web.run_app(main(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
    except KeyboardInterrupt:

        logging.info("Бот остановлен")
