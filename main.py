import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# --- SOZLAMALAR ---
TOKEN = "8659429195:AAFLxH_2ElWhF_FLm4zstCM2tnzy3UPy_94"
WEB_APP_URL = "https://vocab-trainer-5.netlify.app/"

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
scheduler = AsyncIOScheduler()

# --- BAZA BILAN ISHLASH ---
def init_db():
    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()
    # Foydalanuvchilar (Ballar va streak bilan)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            points INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            last_active TEXT
        )
    """)
    # So'zlar jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            word TEXT,
            translation TEXT,
            next_review TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- FSM (Holatlar) ---
class AddWordState(StatesGroup):
    waiting_for_word = State()

# --- TELEGRAM BOT QISMI ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "No username"
    today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()
    
    # Foydalanuvchini bazaga qo'shish yoki tekshirish
    cursor.execute("SELECT streak, points FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute("INSERT INTO users (user_id, username, points, streak, last_active) VALUES (?, ?, 0, 1, ?)", 
                       (user_id, username, today))
    conn.commit()
    conn.close()

    # Mini App tugmasi va boshqa tugmalar
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧠 Vocab Trainer (Mini App)", web_app=WebAppInfo(url=WEB_APP_URL))],
            [InlineKeyboardButton(text="➕ So'z qo'shish", callback_data="add_word"),
             InlineKeyboardButton(text="🏆 Reyting (Leaderboard)", callback_data="leaderboard")],
            [InlineKeyboardButton(text="📊 Mening statistikam", callback_data="my_stats")]
        ]
    )
    
    await message.answer(
        "Salom! **Vocab Trainer** botiga xush kelibsiz.\n"
        "🔹 Veb-ilovadan foydalanishingiz yoki bot orqali so'z yodlashingiz mumkin.\n"
        "⏰ Har kuni **08:00**, **12:00** va **17:00** da eslatmalar kelib turadi!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# --- REYTING (LEADERBOARD) ---
@dp.callback_query(F.data == "leaderboard")
async def show_leaderboard(callback: types.CallbackQuery):
    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 5")
    top_users = cursor.fetchall()
    conn.close()

    text = "🏆 **Eng faol o'quvchilar reytingi:**\n\n"
    for i, (uname, pts) in enumerate(top_users, 1):
        text += f"{i}. @{uname} — ⭐ {pts} ball\n"

    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# --- STATISTIKA ---
@dp.callback_query(F.data == "my_stats")
async def show_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT points, streak FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    
    cursor.execute("SELECT COUNT(*) FROM words WHERE user_id = ?", (user_id,))
    word_count = cursor.fetchone()[0]
    conn.close()

    points = user_data[0] if user_data else 0
    streak = user_data[1] if user_data else 0

    text = (
        f"📊 **Sizning statistikangiz:**\n\n"
        f"📚 Jami so'zlaringiz: {word_count} ta\n"
        f"⭐ To'plagan ballingiz: {points}\n"
        f"🔥 Ketma-ketlik (Streak): {streak} kun"
    )
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# --- BOT ORQALI SO'Z QO'SHISH ---
@dp.callback_query(F.data == "add_word")
async def ask_word(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Inglizcha so'z va tarjimasini yuboring (Masalan: `apple - olma`):", parse_mode="Markdown")
    await state.set_state(AddWordState.waiting_for_word)
    await callback.answer()

@dp.message(AddWordState.waiting_for_word)
async def save_word_from_bot(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if "-" in text:
        word, translation = text.split("-", 1)
    elif ":" in text:
        word, translation = text.split(":", 1)
    else:
        await message.answer("Noto'g'ri format! `so'z - tarjima` ko'rinishida yuboring.")
        return

    user_id = message.from_user.id
    next_review = datetime.now() + timedelta(days=1)

    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO words (user_id, word, translation, next_review) VALUES (?, ?, ?, ?)",
                   (user_id, word.strip(), translation.strip(), next_review))
    # Har bir qo'shilgan so'z uchun +5 ball beramiz
    cursor.execute("UPDATE users SET points = points + 5 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await message.answer(f"✅ Saqlandi va +5 ball qo'shildi!\n**{word.strip()}** — *{translation.strip()}*", parse_mode="Markdown")
    await state.clear()


# --- FASTAPI QISMI (NETLIFY MINI APP BILAN BOG'LANISH) ---

class WebWordModel(BaseModel):
    user_id: int
    word: str
    translation: str

@app.post("/api/add_word")
async def api_add_word(data: WebWordModel):
    """Netlify saytdagi Mini App orqali yuborilgan so'zni qabul qilish"""
    next_review = datetime.now() + timedelta(days=1)
    
    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO words (user_id, word, translation, next_review) VALUES (?, ?, ?, ?)",
                   (data.user_id, data.word.strip(), data.translation.strip(), next_review))
    cursor.execute("UPDATE users SET points = points + 5 WHERE user_id = ?", (data.user_id,))
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Mini App orqali so'z saqlandi va ball qo'shildi!"}


# --- 08:00, 12:00, 17:00 DA TEST VA ESLATMA YUBORISH ---

async def send_scheduled_reminders():
    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    for user in users:
        user_id = user[0]
        # Tasodifiy bitta so'zni tanlaymiz
        cursor.execute("SELECT word, translation FROM words WHERE user_id = ? ORDER BY RANDOM() LIMIT 1", (user_id,))
        word_data = cursor.fetchone()

        if word_data:
            word, translation = word_data
            
            # Viktorina/Test tugmalarini yasaymiz (To'g'ri va noto'g'ri variantlar bilan)
            cursor.execute("SELECT translation FROM words WHERE user_id = ? AND translation != ? ORDER BY RANDOM() LIMIT 3", (user_id, translation))
            wrong_answers = [row[0] for row in cursor.fetchall()]
            
            options = wrong_answers + [translation]
            import random
            random.shuffle(options)

            keyboard_buttons = [[InlineKeyboardButton(text=opt, callback_data=f"ans_{opt == translation}")] for opt in options]
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

            try:
                await bot.send_message(
                    user_id,
                    f"🔔 **So'z yodlash vaqti!**\n\n🇺🇸 **{word}** so'zining tarjimasi qaysi biri?",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Xabar yuborishda xatolik ({user_id}): {e}")

    conn.close()

# --- TEST JAVOBINI TEKSHIRISH ---
@dp.callback_query(F.data.startswith("ans_"))
async def check_answer(callback: types.CallbackQuery):
    is_correct = callback.data.split("_")[1] == "True"
    user_id = callback.from_user.id
    
    conn = sqlite3.connect("vocab_bot.db")
    cursor = conn.cursor()

    if is_correct:
        cursor.execute("UPDATE users SET points = points + 10 WHERE user_id = ?", (user_id,))
        conn.commit()
        await callback.message.edit_text("✅ To'g'ri! Sizga +10 ball qo'shildi 🎉")
    else:
        await callback.message.edit_text("❌ Noto'g'ri. Keyingi safar albatta topasiz!")
        
    conn.close()
    await callback.answer()

# --- ISHGA TUSHIRISH ---

async def main():
    # 08:00, 12:00, 17:00 da eslatma yuborishni sozlaymiz
    scheduler.add_job(send_scheduled_reminders, CronTrigger(hour="8,12,17", minute="0"))
    scheduler.start()

    # Render uchun dinamik portni olish (lokalda 8000 bo'lib ishlayveradi)
    port = int(os.environ.get("PORT", 8000))

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    logging.info("Bot va Mini App server ishga tushmoqda...")
    
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())