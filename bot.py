async def main():
    # ПРИНУДИТЕЛЬНАЯ УСТАНОВКА WEBHOOK
    print("🔄 Принудительная установка webhook...")
    await bot.delete_webhook()
    success = await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook установлен: {success}, URL: {WEBHOOK_URL}")
    
    app = web.Application()
    # ... остальной код

import asyncio
import logging
import os
import requests
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токены из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") + WEBHOOK_PATH

if not BOT_TOKEN or not WEATHER_API_KEY:
    raise ValueError("Токены не заданы в переменных окружения!")

# Создаем объекты бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# СОСТОЯНИЯ ДЛЯ FSM
class WeatherStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_days = State()  # Новое состояние для выбора дней

# Функция получения ТЕКУЩЕЙ погоды (оставляем для совместимости)
async def get_current_weather(city: str) -> str:
    """Получает текущую погоду для указанного города"""
    try:
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
            f"🌤 <b>Текущая погода в {city_name}</b>\n\n"
            f"🌡 Температура: {temp:.1f}°C (ощущается как {feels_like:.1f}°C)\n"
            f"📝 Описание: {description}\n"
            f"💧 Влажность: {humidity}%\n"
            f"🌬 Ветер: {wind_speed} м/с"
        )
        return weather_text
    except Exception as e:
        logging.error(f"Ошибка при запросе погоды: {e}")
        return None

# НОВАЯ ФУНКЦИЯ: Получение ПРОГНОЗА на несколько дней
async def get_weather_forecast(city: str, days: int) -> str:
    """
    Получает прогноз погоды на указанное количество дней (1-5)
    Использует API 5 day / 3 hour forecast [citation:1]
    """
    try:
        # Сначала получаем координаты города через current weather
        geo_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}"
        geo_response = requests.get(geo_url)
        geo_data = geo_response.json()
        
        if geo_data.get("cod") != 200:
            return None
        
        # Получаем координаты
        lat = geo_data["coord"]["lat"]
        lon = geo_data["coord"]["lon"]
        city_name = geo_data["name"]
        
        # Запрашиваем 5-дневный прогноз (3-часовые интервалы) [citation:1]
        forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        forecast_response = requests.get(forecast_url)
        forecast_data = forecast_response.json()
        
        if forecast_data.get("cod") != "200":
            logging.error(f"Ошибка прогноза: {forecast_data}")
            return None
        
        # Группируем прогнозы по дням
        daily_forecasts = {}
        
        for item in forecast_data["list"]:
            # Преобразуем timestamp в дату
            date = datetime.fromtimestamp(item["dt"]).date()
            
            # Пропускаем сегодняшний день (чтобы не смешивать с текущей погодой)
            if date == datetime.now().date():
                continue
            
            # Ограничиваем количество дней
            if len(daily_forecasts) >= days:
                break
            
            if date not in daily_forecasts:
                daily_forecasts[date] = {
                    "temps": [],
                    "descriptions": [],
                    "humidity": [],
                    "wind_speed": []
                }
            
            # Собираем данные для этого дня
            daily_forecasts[date]["temps"].append(item["main"]["temp"])
            daily_forecasts[date]["descriptions"].append(item["weather"][0]["description"])
            daily_forecasts[date]["humidity"].append(item["main"]["humidity"])
            daily_forecasts[date]["wind_speed"].append(item["wind"]["speed"])
        
        # Формируем прогноз по дням
        if not daily_forecasts:
            return f"🌍 <b>Прогноз для {city_name}</b>\n\nНа ближайшие дни прогноз отсутствует."
        
        forecast_text = f"🌍 <b>Прогноз погоды для {city_name} на {days} дн.</b>\n\n"
        
        # Сортируем дни
        for date in sorted(daily_forecasts.keys())[:days]:
            day_data = daily_forecasts[date]
            
            # Вычисляем средние/максимальные/минимальные значения
            avg_temp = sum(day_data["temps"]) / len(day_data["temps"])
            max_temp = max(day_data["temps"])
            min_temp = min(day_data["temps"])
            
            # Самое частое описание погоды
            description = max(set(day_data["descriptions"]), key=day_data["descriptions"].count)
            description = description.capitalize()
            
            # Средние влажность и ветер
            avg_humidity = sum(day_data["humidity"]) / len(day_data["humidity"])
            avg_wind = sum(day_data["wind_speed"]) / len(day_data["wind_speed"])
            
            # Форматируем дату
            date_str = date.strftime("%d.%m")
            day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][date.weekday()]
            
            forecast_text += (
                f"📅 <b>{date_str} ({day_name})</b>\n"
                f"🌡 {min_temp:.0f}…{max_temp:.0f}°C (ср. {avg_temp:.1f}°C)\n"
                f"☁️ {description}\n"
                f"💧 Влажность: {avg_humidity:.0f}%, 🌬 Ветер: {avg_wind:.1f} м/с\n\n"
            )
        
        return forecast_text
        
    except Exception as e:
        logging.error(f"Ошибка при запросе прогноза: {e}")
        return None

# Функция создания клавиатуры для выбора дней
def get_days_keyboard():
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

# Обработчик /start
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот с прогнозом погоды.\n\n"
        "🔍 <b>/weather</b> - Узнать погоду (текущую или на несколько дней)\n"
        "ℹ️ <b>/help</b> - Справка"
    )

# Обработчик /help
@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Доступные команды:\n"
        "/start - Приветствие\n"
        "/help - Эта подсказка\n"
        "/weather - Узнать погоду (с выбором дней)"
    )

# Обработчик /weather - вход в FSM
@dp.message(Command("weather"))
async def cmd_weather(message: Message, state: FSMContext):
    await message.answer("🌍 Напишите название города:")
    await state.set_state(WeatherStates.waiting_for_city)

# Обработчик ввода города
@dp.message(WeatherStates.waiting_for_city)
async def process_city(message: Message, state: FSMContext):
    city = message.text.strip()
    
    # Сохраняем город в состоянии
    await state.update_data(city=city)
    
    # Спрашиваем, на сколько дней нужен прогноз
    await message.answer(
        f"📍 Город: {city}\n\n"
        f"Выберите период прогноза:",
        reply_markup=get_days_keyboard()
    )
    await state.set_state(WeatherStates.waiting_for_days)

# Обработчик выбора количества дней (инлайн-кнопки)
@dp.callback_query(WeatherStates.waiting_for_days)
async def process_days_selection(callback: CallbackQuery, state: FSMContext):
    action = callback.data
    
    if action == "days_cancel":
        await callback.message.edit_text("❌ Запрос отменён.")
        await state.clear()
        await callback.answer()
        return
    
    # Получаем количество дней из callback_data
    days = int(action.split("_")[1])
    
    # Получаем сохраненный город
    data = await state.get_data()
    city = data.get("city")
    
    await callback.message.edit_text(f"🔍 Получаю прогноз на {days} дн. для города {city}...")
    
    # Получаем прогноз
    forecast = await get_weather_forecast(city, days)
    
    if forecast:
        await callback.message.answer(forecast, parse_mode="HTML")
    else:
        # Если прогноз не получился, пробуем хотя бы текущую погоду
        current = await get_current_weather(city)
        if current:
            await callback.message.answer(
                f"⚠️ Не удалось получить прогноз на {days} дней, но вот текущая погода:\n\n{current}",
                parse_mode="HTML"
            )
        else:
            await callback.message.answer(
                "❌ Не удалось найти город. Проверьте название и попробуйте снова.\n"
                "Используйте /weather для нового запроса."
            )
    
    await state.clear()
    await callback.answer()

# Обработчик для старых версий (если кто-то просто ввел город без выбора дней)
@dp.message(WeatherStates.waiting_for_days)
async def process_days_text(message: Message, state: FSMContext):
    # Если пользователь ввел число
    if message.text.isdigit():
        days = int(message.text)
        if 1 <= days <= 5:
            data = await state.get_data()
            city = data.get("city")
            
            await message.answer(f"🔍 Получаю прогноз на {days} дн. для города {city}...")
            
            forecast = await get_weather_forecast(city, days)
            
            if forecast:
                await message.answer(forecast, parse_mode="HTML")
            else:
                current = await get_current_weather(city)
                if current:
                    await message.answer(
                        f"⚠️ Вот текущая погода вместо прогноза:\n\n{current}",
                        parse_mode="HTML"
                    )
                else:
                    await message.answer("❌ Не удалось получить данные о погоде.")
            
            await state.clear()
        else:
            await message.answer("❌ Введите число от 1 до 5 или выберите на клавиатуре.")
    else:
        await message.answer("Пожалуйста, выберите количество дней на клавиатуре или введите число от 1 до 5.")

# Обработчик всех остальных сообщений
@dp.message()
async def handle_other_messages(message: Message):
    await message.answer(
        "Я не понимаю эту команду.\n"
        "Используйте /weather чтобы узнать погоду."
    )

# Настройка webhook
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен на {WEBHOOK_URL}")

async def on_shutdown():
    await bot.delete_webhook()
    logging.info("Webhook удален")

async def main():
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    app.on_startup.append(lambda _: on_startup())
    app.on_shutdown.append(lambda _: on_shutdown())
    port = int(os.getenv("PORT", "8000"))
    logging.info(f"Запуск сервера на порту {port}")
    return app

if __name__ == "__main__":
    try:
        web.run_app(main(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
    except KeyboardInterrupt:
        logging.info("Бот остановлен")

