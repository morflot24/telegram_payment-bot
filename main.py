import asyncio
import logging
import sys
import os
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Попытка импорта конфига
try:
    import config_private
except ImportError:
    print("❌ ОШИБКА: Файл config_private.py не найден!")
    print("Создайте этот файл на хостинге вручную.")
    sys.exit(1)

# Логирование
logging.basicConfig(level=logging.INFO)

# Инициализация
bot = Bot(token=config_private.BOT_TOKEN)
dp = Dispatcher()
DB_NAME = 'bot_database.db'

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                start_date TEXT,
                expiry_date TEXT
            )
        ''')
        await db.commit()

# --- КЛАВИАТУРЫ ---
def get_start_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Купить доступ ({config_private.PRICE}₽)", callback_data="buy")],
        [InlineKeyboardButton(text="Моя подписка", callback_data="check")]
    ])

def get_pay_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Я оплатил (отправить чек)", callback_data="send_proof")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
    ])

def get_admin_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Пустить", callback_data=f"ok_{user_id}"),
            InlineKeyboardButton(text="❌ Отказ", callback_data=f"no_{user_id}")
        ]
    ])

# --- ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ ---

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        f"👋 Привет! Это бот для доступа в закрытый канал.\n\n"
        f"📅 Срок подписки: **{config_private.DAYS} день**\n"
        f"💰 Стоимость: **{config_private.PRICE} руб.**",
        parse_mode="Markdown",
        reply_markup=get_start_kb()
    )

@dp.callback_query(F.data == "buy")
async def buy(call: types.CallbackQuery):
    await call.message.edit_text(
        f"💳 **Реквизиты для оплаты:**\n\n"
        f"📱 **СБП:** `{config_private.PHONE}`\n"
        f"🏦 **Банк:** {config_private.BANK}\n"
        f"💰 **Сумма:** `{config_private.PRICE}` руб.\n\n"
        f"⚠️ После перевода нажмите кнопку «Я оплатил» и пришлите скриншот чека.",
        parse_mode="Markdown",
        reply_markup=get_pay_kb()
    )

@dp.callback_query(F.data == "send_proof")
async def wait_proof(call: types.CallbackQuery):
    await call.message.answer("📸 Жду скриншот оплаты прямо сейчас...")
    await call.answer()

@dp.message(F.photo)
async def get_proof(message: types.Message):
    # Уведомляем админа
    caption = (
        f"💰 **Новая оплата!**\n"
        f"👤 Юзер: {message.from_user.full_name} (@{message.from_user.username})\n"
        f"🆔 ID: `{message.from_user.id}`"
    )
    try:
        await bot.send_photo(
            chat_id=config_private.ADMIN_ID,
            photo=message.photo[-1].file_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=get_admin_kb(message.from_user.id)
        )
        await message.answer("✅ Чек отправлен на проверку. Ожидайте подтверждения.")
    except Exception as e:
        await message.answer("Ошибка отправки админу. Попробуйте позже.")
        logging.error(e)

@dp.callback_query(F.data == "check")
async def check_sub(call: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT expiry_date FROM users WHERE user_id = ?", (call.from_user.id,))
        row = await cursor.fetchone()
    
    if row:
        expiry = datetime.fromisoformat(row[0])
        left = (expiry - datetime.now()).days
        if left >= 0:
            await call.answer(f"Подписка активна! Осталось дней: {left}", show_alert=True)
        else:
            await call.answer("Подписка истекла.", show_alert=True)
    else:
        await call.answer("Нет активной подписки.", show_alert=True)

@dp.callback_query(F.data == "cancel")
async def cancel(call: types.CallbackQuery):
    await call.message.edit_text("Меню", reply_markup=get_start_kb())

# --- АДМИНКА ---

@dp.callback_query(F.data.startswith("ok_"))
async def approve(call: types.CallbackQuery):
    user_id = int(call.data.split("_")[1])
    now = datetime.now()
    expiry = now + timedelta(days=config_private.DAYS)
    
    # Пишем в БД
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, username, start_date, expiry_date) VALUES (?, ?, ?, ?)",
            (user_id, "user", now.isoformat(), expiry.isoformat())
        )
        await db.commit()
    
    # Ссылка
    try:
        link = await bot.create_chat_invite_link(
            chat_id=config_private.CHANNEL_ID,
            member_limit=1,
            name=f"Sub_{user_id}"
        )
        await bot.send_message(
            user_id,
            f"✅ **Оплата принята!**\n\nВаша ссылка: {link.invite_link}\nСрок действия: до {expiry.strftime('%d.%m.%Y')}",
            parse_mode="HTML"
        )
        await call.message.edit_caption(caption=call.message.caption + "\n\n✅ **ПРИНЯТО**", parse_mode="Markdown")
    except Exception as e:
        await call.answer(f"Ошибка (бот админ в канале?): {e}", show_alert=True)

@dp.callback_query(F.data.startswith("no_"))
async def decline(call: types.CallbackQuery):
    user_id = int(call.data.split("_")[1])
    await bot.send_message(user_id, "❌ Оплата не подтверждена. Свяжитесь с админом.")
    await call.message.edit_caption(caption=call.message.caption + "\n\n❌ **ОТКАЗ**", parse_mode="Markdown")

# --- ФОНОВАЯ ПРОВЕРКА ---
async def scheduler():
    while True:
        try:
            await asyncio.sleep(3600) # Проверка раз в час
            logging.info("Checking subs...")
            async with aiosqlite.connect(DB_NAME) as db:
                users = await (await db.execute("SELECT user_id, expiry_date FROM users")).fetchall()
                for uid, exp in users:
                    if datetime.now() > datetime.fromisoformat(exp):
                        try:
                            await bot.ban_chat_member(config_private.CHANNEL_ID, uid)
                            await bot.unban_chat_member(config_private.CHANNEL_ID, uid)
                            await db.execute("DELETE FROM users WHERE user_id = ?", (uid,))
                            await db.commit()
                            await bot.send_message(uid, "Ваша подписка истекла.")
                        except Exception as e:
                            logging.error(f"Err kicking {uid}: {e}")
        except Exception as e:
            logging.error(e)

async def main():
    await init_db()
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
