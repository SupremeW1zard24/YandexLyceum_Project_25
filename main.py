import asyncio
import aiosqlite
import requests
from datetime import datetime, timedelta
import pytz
import csv
from io import BytesIO
import CFG

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, CallbackQuery
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

API_TOKEN = CFG.API_TOKEN
WEATHER_API_KEY = CFG.WEATHER_API_KEY
ADMIN_ID = CFG.ADMIN_ID
DATABASE = "bot_db.sqlite"
LOG_CHANNEL_ID = CFG.LOG_CHANNEL_ID

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ==================== СОСТОЯНИЯ ДЛЯ FSM ====================
class FeedbackStates(StatesGroup):
    waiting_for_q1 = State()
    waiting_for_q2 = State()
    waiting_for_q3 = State()


# ==================== КЛАВИАТУРЫ ====================
def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏰ Время часового пояса", callback_data="cmd_time")],
        [InlineKeyboardButton(text="🌦 Погода города", callback_data="cmd_weather")],
        [InlineKeyboardButton(text="📚 Википедия", callback_data="cmd_wiki")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="cmd_help")]
    ])


def get_rating_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data="rate_1"),
            InlineKeyboardButton(text="2", callback_data="rate_2"),
            InlineKeyboardButton(text="3", callback_data="rate_3"),
            InlineKeyboardButton(text="4", callback_data="rate_4"),
            InlineKeyboardButton(text="5", callback_data="rate_5"),
        ],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_feedback")]
    ])


# ==================== БАЗА ДАННЫХ ====================
async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                until TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                reg_time TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                user_id INTEGER,
                q1 INTEGER,
                q2 INTEGER,
                q3 INTEGER,
                timestamp TEXT
            )
        ''')
        await db.commit()


async def is_user_blocked(user_id: int) -> tuple[bool, str, str]:
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute('SELECT reason, until FROM blacklist WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                reason, until = row
                if until:
                    until_dt = datetime.strptime(until, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() > until_dt:
                        await db.execute('DELETE FROM blacklist WHERE user_id = ?', (user_id,))
                        await db.commit()
                        return False, "", ""
                return True, reason or "не указана", until or "бессрочно"
            return False, "", ""


async def save_user(user: types.User):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, reg_time)
            VALUES (?, ?, ?, ?)
        ''', (user.id, user.username, user.first_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        await db.commit()


async def save_feedback(user_id: int, data: dict):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            INSERT INTO feedback (user_id, q1, q2, q3, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, data['q1'], data['q2'], data['q3'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        await db.commit()


async def get_all_users() -> list[int]:
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute('SELECT user_id FROM users') as cursor:
            return [row[0] async for row in cursor]


async def get_all_users_info():
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute('SELECT user_id, username, first_name, reg_time FROM users') as cursor:
            return await cursor.fetchall()


async def get_all_blocks():
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute('SELECT user_id, reason, until FROM blacklist') as cursor:
            return await cursor.fetchall()


async def block_user(user_id: int, reason: str = "", minutes: int = 0):
    until = None
    if minutes > 0:
        until = (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            INSERT OR REPLACE INTO blacklist (user_id, reason, until)
            VALUES (?, ?, ?)
        ''', (user_id, reason, until))
        await db.commit()
    try:
        msg = f"⛔ Вы были заблокированы.\nПричина: {reason or 'не указана'}"
        if until:
            msg += f"\nДо: {until}"
        await bot.send_message(user_id, msg)

    except:
        pass


async def unblock_user(user_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('DELETE FROM blacklist WHERE user_id = ?', (user_id,))
        await db.commit()


# ==================== ОБРАТНАЯ СВЯЗЬ ====================
@router.message(Command("feedback"))
async def cmd_feedback(message: types.Message, state: FSMContext):
    blocked, reason, until = await is_user_blocked(message.from_user.id)
    if blocked:
        return await message.answer(f"⛔ Вы заблокированы. Причина: {reason}\nДо: {until}")

    await state.set_state(FeedbackStates.waiting_for_q1)
    await message.answer(
        "📝 Оцените бота (1-5):\n\n1. Удобство интерфейса:",
        reply_markup=get_rating_keyboard()
    )


@router.callback_query(F.data == "cmd_time")
async def button_time(callback: CallbackQuery):
    await callback.message.answer("Введите команду: /time [часовой пояс]")
    await callback.answer()


@router.callback_query(F.data == "cmd_weather")
async def button_weather(callback: CallbackQuery):
    await callback.message.answer("Введите команду: /weather [город]")
    await callback.answer()


@router.callback_query(F.data == "cmd_wiki")
async def button_wiki(callback: CallbackQuery):
    await callback.message.answer("Введите команду: /wiki [запрос]")
    await callback.answer()


@router.callback_query(F.data == "cmd_help")
async def button_help(callback: CallbackQuery):
    await cmd_help(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("rate_"), FeedbackStates.waiting_for_q1)
async def process_q1(callback: CallbackQuery, state: FSMContext):
    rate = int(callback.data.split("_")[1])
    await state.update_data(q1=rate)
    await state.set_state(FeedbackStates.waiting_for_q2)
    await callback.message.edit_text(
        "2. Точность ответов бота:",
        reply_markup=get_rating_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rate_"), FeedbackStates.waiting_for_q2)
async def process_q2(callback: CallbackQuery, state: FSMContext):
    rate = int(callback.data.split("_")[1])
    await state.update_data(q2=rate)
    await state.set_state(FeedbackStates.waiting_for_q3)
    await callback.message.edit_text(
        "3. Скорость ответа бота:",
        reply_markup=get_rating_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rate_"), FeedbackStates.waiting_for_q3)
async def process_q3(callback: CallbackQuery, state: FSMContext):
    rate = int(callback.data.split("_")[1])
    data = await state.update_data(q3=rate)
    await state.clear()

    await save_feedback(callback.from_user.id, data)

    feedback_text = (
        "📊 <b>Спасибо за отзыв! При необходимости администратор свяжется с Вами для уточнения деталей. </b>\n\n"
        f"1. Удобство интерфейса: {data['q1']}/5\n"
        f"2. Точность ответов: {data['q2']}/5\n"
        f"3. Скорость ответа: {data['q3']}/5"
    )
    await callback.message.edit_text(feedback_text)
    await callback.answer()

    admin_msg = (
        "🔔 <b>Новый отзыв</b>\n\n"
        f"👤 ID: <code>{callback.from_user.id}</code>\n"
        f"🔹 Удобство: {data['q1']}/5\n"
        f"🔹 Точность: {data['q2']}/5\n"
        f"🔹 Скорость: {data['q3']}/5"
    )
    await bot.send_message(ADMIN_ID, admin_msg)


@router.callback_query(F.data == "cancel_feedback")
async def cancel_feedback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Опрос прерван.")
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного диалога.")
        return
    await state.clear()
    await message.answer("❌ Опрос прерван.")


@router.message(FeedbackStates.waiting_for_q1)
@router.message(FeedbackStates.waiting_for_q2)
@router.message(FeedbackStates.waiting_for_q3)
async def handle_wrong_input(message: types.Message):
    await message.answer("⚠️ Пожалуйста, используйте кнопки для оценки (1-5).")


# ==================== ОСНОВНЫЕ КОМАНДЫ ====================
@router.message(Command("start"))
async def cmd_start(message: types.Message):
    blocked, reason, until = await is_user_blocked(message.from_user.id)
    if blocked:
        return await message.answer(f"⛔ Вы заблокированы.\nПричина: {reason}\nДо: {until}")
    await save_user(message.from_user)
    await message.answer("👋 Здравствуй!!! Я – твой цифровой помощник! Функции:",
                         reply_markup=main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "<b>📖 Справка по командам:</b>\n\n"
        "✅ /start — начать работу с ботом\n"
        "✅ /help — показать это сообщение\n"
        "✅ /weather [город] — погода в городе\n"
        "✅ /time [часовой пояс] — текущее время в городе\n"
        "✅ /wiki [запрос] — краткое описание из Википедии\n"
        "✅ /feedback — оставить отзыв о боте\n"
        "🚫 /broadcast [текст] — рассылка от админа\n"
        "🚫 /get_stats — статистика пользователей\n"
        "🚫 /block [user_id] [минуты] [причина] — заблокировать пользователя\n"
        "🚫 /unblock [user_id] — разблокировать пользователя"
    )
    await message.answer(help_text)


# ==================== ПОГОДА ====================
@router.message(Command("weather"))
async def cmd_weather(message: types.Message):
    blocked, reason, until = await is_user_blocked(message.from_user.id)
    if blocked:
        return await message.answer(f"⛔ Вы заблокированы. Причина: {reason}\nДо: {until}")
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.answer("❗ Использование: /weather <город>")
    city = args[1]
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        temp = data["main"]["temp"]
        desc = data["weather"][0]["description"]
        await message.answer(f"🌡 Погода в городе <b>{city}</b>: {temp}°C, {desc}")
    else:
        await message.answer("⚠️ Не удалось получить данные о погоде.")


# ==================== ВРЕМЯ ====================
@router.message(Command("time"))
async def cmd_time(message: types.Message):
    blocked, reason, until = await is_user_blocked(message.from_user.id)
    if blocked:
        return await message.answer(f"⛔ Вы заблокированы. Причина: {reason}\nДо: {until}")
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.answer("❗ Использование: /time <город>")
    city = args[1]
    matches = [tz for tz in pytz.all_timezones if city.lower() in tz.lower()]
    if not matches:
        return await message.answer(
            "❌ Часовой пояс не найден в списке временных зон. Введите запрос на английском языке.")
    tz = pytz.timezone(matches[0])
    current_time = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    await message.answer(f"⏰ Время в поясе <b>{matches[0]}</b>: {current_time}")


# -----------------------


# === Админ-меню: блокировка ===
@router.message(Command("admin_menu_t"))
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    await message.answer("🔒 Введите команду: /block USER_ID")


@router.message(Command("block_t"))
async def block_user_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer("❗ Использование: /block 123456789")
    user_id = int(parts[1])
    await block_user(user_id)
    await message.answer(f"✅ Пользователь {user_id} заблокирован.")


# === Статистика (CSV) ===
@router.message(Command("get_stats_t"))
async def get_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    users = await get_all_users()
    buffer = BytesIO()
    writer = csv.writer(buffer)
    writer.writerow(["User ID"])
    for uid in users:
        writer.writerow([uid])
    buffer.seek(0)
    await message.answer_document(InputFile(buffer, filename="user_stats.csv"))


# -----------------------


# ==================== ВИКИПЕДИЯ ====================
@router.message(Command("wiki"))
async def search_wiki(message: types.Message):
    blocked, reason, until = await is_user_blocked(message.from_user.id)
    if blocked:
        return await message.answer(f"⛔ Вы заблокированы. Причина: {reason}\nДо: {until}")
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.answer("❗ Использование: /wiki <слово>")
    query = args[1]
    response = requests.get(f"https://ru.wikipedia.org/api/rest_v1/page/summary/{query}")
    if response.status_code == 200:
        data = response.json()
        title = data.get("title", query)
        extract = data.get("extract", "Нет описания.")
        await message.answer(f"<b>{title}</b>\n\n{extract}")
    else:
        await message.answer("⚠️ Статья не найдена.")


# ==================== АДМИН-КОМАНДЫ ====================
@router.message(Command("block"))
async def cmd_block(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    parts = message.text.split(maxsplit=3)
    if len(parts) < 2:
        return await message.answer("❗ Использование: /block <user_id> [минуты] [причина]")
    try:
        user_id = int(parts[1])
        minutes = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        reason = parts[3] if len(parts) > 3 else ""
        await block_user(user_id, reason, minutes)
        await message.answer(
            f"✅ Пользователь {user_id} заблокирован{' на ' + str(minutes) + ' минут' if minutes else ''}. Причина: "
            f"{reason or 'Не указана'}")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при блокировке: {e}")


@router.message(Command("unblock"))
async def cmd_unblock(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("❗ Использование: /unblock <user_id>")
    try:
        user_id = int(parts[1])
        await unblock_user(user_id)
        await message.answer(f"✅ Пользователь {user_id} разблокирован.")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при разблокировке: {e}")


@router.message(Command("broadcast"))
async def broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        return await message.answer("❗ Использование: /broadcast <текст>")
    text = parts[1]
    users = await get_all_users()
    count = 0
    for uid in users:
        blocked, *_ = await is_user_blocked(uid)
        if not blocked:
            try:
                await bot.send_message(uid, f"📢 {text}")
                count += 1
            except:
                pass
    await message.answer(f"✅ Отправлено {count} пользователям.")


@router.message(Command("get_stats"))
async def get_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ Нет доступа.")
    users = await get_all_users_info()
    blocks = await get_all_blocks()
    text = "<b>📋 Пользователи:</b>\n\n"
    for uid, uname, fname, reg in users:
        text += f"ID: <code>{uid}</code>\nИмя: {fname or '-'}\nUsername: @{uname or '-'}\nЗарегистрирован: {reg}\n\n"
    if blocks:
        text += "<b>🚫 Заблокированные:</b>\n\n"
        for uid, reason, until in blocks:
            text += f"ID: <code>{uid}</code>\nПричина: {reason or '—'}\nДо: {until or 'бессрочно'}\n\n"
    await message.answer(text)


# ==================== ЛОГИРОВАНИЕ ====================
async def log_message(message: types.Message):
    try:
        sender = message.from_user
        log_text = (
            f"🕒 <b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</b>\n"
            f"👤 <b>От:</b> {sender.full_name} (@{sender.username or '—'})\n"
            f"🆔 <b>ID:</b> {sender.id}\n"
            f"💬 <b>Сообщение:</b>\n{message.text or '[не текстовое сообщение]'}"
        )
        await bot.send_message(LOG_CHANNEL_ID, log_text)
    except Exception as e:
        print(f"Ошибка логирования сообщения: {e}")


@router.message()
async def catch_all(message: types.Message):
    await log_message(message)


# ==================== ЗАПУСК ====================
async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
