import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import database
import ai

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
INVITE_CODE = os.getenv("INVITE_CODE", "1234")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

_user_locks: dict[int, asyncio.Lock] = {}
_help_mode: set[int] = set()
_help_waiting_answer: set[int] = set()
_pending_clarification: dict[int, dict] = {}
_pending_week_end: set[int] = set()
_pending_weekly_weight: set[int] = set()
_pending_agree: set[int] = set()

_no_guilt_shown_today: dict[int, str] = {}

BTN_EAT = "🍽 Записать еду"
BTN_SUMMARY = "📊 Итог дня"
BTN_HELP = "🐾 Котик, подскажи..."
BTN_CONTINUE = "➡️ Продолжаем?"
BTN_AGREE = "✅ Согласна / Согласен"

DIARY_HELP_TEXT = (
    "📝 <b>Как вести дневник (это просто!)</b>\n\n"
    "Пиши, что съел и выпил, — как вспомнится. Точность до грамма не нужна:\n"
    "🍲 Борщ с мясом и сметаной 300 г — отлично!\n"
    "🍗 Жареная куриная котлета 100 г и картофельное пюре на молоке 3,2% — прекрасно!\n\n"
    "Можно и покороче:\n"
    "🍎 Яблоко, одно среднее (или 150 г)\n"
    "🥛 Кефир 2,5%, стакан\n"
    "☕ Кофе с молоком, без сахара\n\n"
    "А если вес не знаешь — просто скажи «не знаю». Я возьму средний показатель и всё запишу, без лишних вопросов. 💚\n\n"
    "Что-то забыл — не беда, записывай по ходу дня, как удобно.\n\n"
    "📷 Мы только учимся распознавать еду по фото. Если что-то не пойму — уточню, без переживаний."
)
DIARY_HELP_TAIL = "Расскажи, пожалуйста, что ты сегодня уже ел и пил. Поехали! 💚"

def _esc(text) -> str:
    if text is None: return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]

def _main_menu_keyboard(user: dict) -> ReplyKeyboardMarkup:
    utc_offset = user.get('utc_offset', 3)
    user_now = datetime.utcnow() + timedelta(hours=utc_offset)
    
    buttons = [[KeyboardButton(text=BTN_EAT)]]
    if user_now.hour >= 19:
        buttons.append([KeyboardButton(text=BTN_SUMMARY)])
    buttons.append([KeyboardButton(text=BTN_HELP)])
    
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True, is_persistent=True,
    )

def _continue_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CONTINUE)]], resize_keyboard=True, is_persistent=True)

def _agree_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_AGREE)]], resize_keyboard=True, is_persistent=True)

def _timezone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="UTC+2 (Калининград)")], [KeyboardButton(text="UTC+3 (Москва, Питер)")],
            [KeyboardButton(text="UTC+4 (Самара, Ижевск)")], [KeyboardButton(text="UTC+5 (Екатеринбург, Ташкент)")],
            [KeyboardButton(text="UTC+6 (Омск, Астана)")], [KeyboardButton(text="UTC+7 (Новосибирск, Красноярск)")],
        ],
        resize_keyboard=True, is_persistent=True,
    )

def _food_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Исправить", callback_data="fix_last")],
        [InlineKeyboardButton(text="🗑 Убрать из дневника", callback_data="delete_last")]
    ])

def _calc_norms(user: dict) -> dict:
    weight = float(user['weight']) if user and user.get('weight') else 70.0
    height = user.get('height') if user and user.get('height') else 170
    age = user.get('age') if user and user.get('age') else 30
    gender = user.get('gender') if user and user.get('gender') else 'Ж'
    lifestyle = (user.get('lifestyle') or 'умеренный').lower()

    if gender == "М": bmr = (10 * weight) + (6.25 * height) - (5 * age) + 5
    else: bmr = (10 * weight) + (6.25 * height) - (5 * age) - 161

    if "сидяч" in lifestyle: mult = 1.2
    elif "актив" in lifestyle: mult = 1.55
    else: mult = 1.375

    support_center = int(bmr * mult)
    support_low = int(round((support_center - 100) / 50.0) * 50)
    support_high = int(round((support_center + 100) / 50.0) * 50)
    loss_center = max(support_center - 400, 1500 if gender == "М" else 1200)
    loss_low = int(round((loss_center - 100) / 50.0) * 50)
    loss_high = int(round((loss_center + 100) / 50.0) * 50)

    ideal_weight = height - (100 if gender == "М" else 110)
    corrected_weight = ideal_weight + (weight - ideal_weight) * 0.25
    prot_center = corrected_weight * 1.2
    prot_low, prot_high = int(prot_center * 0.9), int(prot_center * 1.1)

    norms = {"support_low": support_low, "support_high": support_high, "loss_low": loss_low, "loss_high": loss_high,
             "protein_low": prot_low, "protein_high": prot_high, "weight": weight, "corrected_weight": corrected_weight}
    water_low, water_high = database.get_water_range(user, norms)
    norms['water_low'], norms['water_high'] = water_low, water_high
    return norms

def _calc_percent(current, target):
    if not target or target == 0:
        return 0
    return min(100, int(round((current / target) * 100)))

async def _send_day_summary(message: types.Message, user_id: int):
    user = database.get_user(user_id)
    today_str = database._get_user_date(user_id, user.get('utc_offset', 3))
    summary = database.get_day_summary(user_id, today_str)
    liquid_total = database.get_day_liquid(user_id, today_str)
    
    if summary is None and liquid_total == 0:
        await message.answer("Сегодня ты еще ничего не записывал. Присылай, что съел!")
        return

    norms = _calc_norms(user)
    day_cal = int(summary['calories']) if summary else 0
    day_prot = summary['protein'] if summary else 0
    day_fat = summary['fat'] if summary else 0
    day_carb = summary['carbs'] if summary else 0
    target_cal = norms['support_high']
    
    if day_cal > (target_cal + 500):
        if _no_guilt_shown_today.get(user_id) != today_str:
            conn = database._connect()
            cursor = conn.cursor()
            cursor.execute('SELECT dish FROM food_log WHERE user_id = ? AND date = ?', (user_id, today_str))
            rows = cursor.fetchall()
            conn.close()
            dishes = [r['dish'] for r in rows if r['dish']]
            
            no_guilt_text = ai.generate_no_guilt_message(day_cal, target_cal, dishes)
            await message.answer(no_guilt_text)
            _no_guilt_shown_today[user_id] = today_str
            return
        else:
            await message.answer("Мы рядом. Всё хорошо. 💚")
            return

    if user.get('week', 1) == 1:
        ai_text = ai.generate_day_summary(user, summary, liquid_total) or "Ты сегодня отлично потрудился! 💚"
        await message.answer(
            f"🔥 <b>За сегодня:</b> {day_cal} ккал | Б {day_prot} / Ж {day_fat} / У {day_carb}\n"
            f"💧 Вода: {liquid_total} мл\n\n"
            f"{ai_text}",
            reply_markup=_main_menu_keyboard(user)
        )
    else:
        p_cal = _calc_percent(day_cal, target_cal)
        p_prot = _calc_percent(day_prot, norms['protein_high'])
        p_water = _calc_percent(liquid_total, norms['water_high'])
        
        ai_text = ai.generate_day_summary(user, summary, liquid_total) or "Хороший ритм! 💚"
        
        text = (
            f"🐾 <b>Твои итоги дня:</b>\n"
            f"🔥 Калории: {day_cal} из {target_cal} ккал (<b>{p_cal}%</b>)\n"
            f"🥩 Белок: {day_prot} из {norms['protein_high']} г ({p_prot}%)\n"
            f"💧 Вода: {liquid_total} из {norms['water_high']} мл ({p_water}%)\n\n"
            f"{ai_text}"
        )
        await message.answer(text, reply_markup=_main_menu_keyboard(user))

@dp.callback_query(F.data == "delete_last")
async def cb_delete_last(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = database.get_user(user_id)
    today_str = database._get_user_date(user_id, user.get('utc_offset', 3))
    
    conn = database._connect()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM food_log WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1', (user_id, today_str))
    row = cursor.fetchone()
    if row:
        cursor.execute('DELETE FROM food_log WHERE id = ?', (row['id'],))
        conn.commit()
        await callback.message.edit_text(callback.message.html_text + "\n\n🗑 <i>Запись удалена.</i>")
    else:
        await callback.message.answer("Не нашел последнюю запись.")
    conn.close()
    await callback.answer()

@dp.callback_query(F.data == "fix_last")
async def cb_fix_last(callback: CallbackQuery):
    user_id = callback.from_user.id
    _pending_clarification[user_id] = {"is_fix": True}
    await callback.message.answer("Хорошо, напиши заново, что нужно исправить (например: «борщ 250»).")
    await callback.answer()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = database.get_user(user_id)
    if user is None:
        await message.answer("Привет! Мы ещё в стадии теста, поэтому введи код-приглашение — без него пользоваться ботом не получится. 🐾")
        return
    step = user['survey_step']
    if step == 99:
        await message.answer("🐾 Мы уже говорили про возраст. Когда тебе исполнится 18, будем рады познакомиться поближе!")
    elif step == 0:
        database.set_step(user_id, 1)
        await message.answer("Давай познакомимся. Как тебя зовут?")
    elif step < 10:
        await message.answer("Мы не закончили знакомство. Продолжим? Напиши любое сообщение.")
    else:
        await message.answer("Привет! Присылай, что съел.", reply_markup=_main_menu_keyboard(user))

@dp.message(Command("reset_test"))
async def cmd_reset_test(message: types.Message):
    user_id = message.from_user.id
    if not database.get_user(user_id):
        await message.answer("Сначала /start.")
        return
    conn = database._connect()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM food_log WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM water_log WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM weight_log WHERE user_id = ?', (user_id,))
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('UPDATE users SET week = 1, created_at = ?, last_norms_update = ? WHERE user_id = ?', (now_str, now_str, user_id))
    conn.commit()
    conn.close()
    database.set_step(user_id, 10)
    _help_mode.discard(user_id)
    _help_waiting_answer.discard(user_id)
    _pending_clarification.pop(user_id, None)
    _pending_week_end.discard(user_id)
    _pending_weekly_weight.discard(user_id)
    _pending_agree.discard(user_id)
    _no_guilt_shown_today.pop(user_id, None)
    await message.answer("🧹 Тест сброшен. Всё удалено. Ты на первой неделе.", reply_markup=_main_menu_keyboard(database.get_user(user_id)))

@dp.message()
async def handle_message(message: types.Message):
    async with _get_lock(message.from_user.id):
        await _handle_message_locked(message)

async def _handle_message_locked(message: types.Message):
    user_id = message.from_user.id
    user = database.get_user(user_id)
    text = message.text.strip() if message.text else ""
    text_lower = text.lower()

    if user is None:
        if text == INVITE_CODE:
            database.create_user(user_id)
            database.set_step(user_id, 1)
            await message.answer("Код верный! Давай познакомимся. Как тебя зовут?")
        else:
            await message.answer("Код не подошёл. Попробуй ещё раз — без кода пользоваться ботом не получится. 🐾")
        return

    if user_id in _pending_agree:
        if text == BTN_AGREE:
            _pending_agree.discard(user_id)
            await message.answer("🐾 Давай сверим часы, чтобы я понимал твой день. Выбери свой часовой пояс:", reply_markup=_timezone_keyboard())
            database.set_step(user_id, 35)
        else:
            await message.answer("Нажми, пожалуйста, кнопку ниже — без этого дальше не получится. 🐾", reply_markup=_agree_keyboard())
        return

    if user['survey_step'] == 35:
        tz_map = {"UTC+2 (Калининград)": 2, "UTC+3 (Москва, Питер)": 3, "UTC+4 (Самара, Ижевск)": 4, "UTC+5 (Екатеринбург, Ташкент)": 5, "UTC+6 (Омск, Астана)": 6, "UTC+7 (Новосибирск, Красноярск)": 7}
        if text in tz_map:
            database.save_answer(user_id, "utc_offset", tz_map[text])
            database.set_step(user_id, 3)
            await message.answer(
                "Отлично, часы сверены! Теперь я знаю твой часовой пояс. ⏰\n\n"
                "🐾 У нас есть два пути:\n\n"
                "1️⃣ <b>Неделя привычек</b> (рекомендовано) — мягкий старт. Семь дней просто записываем еду и напитки.\n\n"
                "2️⃣ <b>Полная программа</b> — сразу настроим ориентиры для снижения веса.\n\n"
                "Напиши 1 или 2.", reply_markup=ReplyKeyboardRemove()
            )
        else:
            await message.answer("Пожалуйста, выбери пояс из кнопок ниже.", reply_markup=_timezone_keyboard())
        return

    if user_id in _pending_weekly_weight:
        weight = _parse_weight(text)
        if weight is None:
            await message.answer("Пожалуйста, напиши вес цифрами (например: 75 или 75.5).")
            return
        _pending_weekly_weight.discard(user_id)
        today_str = database._get_user_date(user_id, user.get('utc_offset', 3))
        database.add_weight(user_id, today_str, weight)
        database.save_answer(user_id, "week", user.get('week', 1) + 1)
        await message.answer(f"⚖️ Вес записан: {weight} кг. Нормы обновим раз в 3 недели. Продолжаем!", reply_markup=_main_menu_keyboard(database.get_user(user_id)))
        return

    step = user['survey_step']
    if step < 10:
        await _handle_survey(message, user, user_id, text, step)
        return
    if step == 99:
        await message.answer("🐾 Мы уже говорили про возраст.")
        return
    if user_id in _pending_week_end:
        if text == BTN_CONTINUE:
            _pending_week_end.discard(user_id)
            database.set_step(user_id, 4)
            database.save_answer(user_id, "week", 2)
            await message.answer("🐾 <b>Первая неделя позади!</b>\n\nОтлично, теперь давай настроим твои ориентиры. Задам несколько вопросов. 💚", reply_markup=ReplyKeyboardRemove())
            await message.answer("🐾 Для расчётов мне нужно знать твой пол. Напиши <b>М</b> или <b>Ж</b>.")
            return
        await message.answer("Жми «Продолжаем?».", reply_markup=_continue_keyboard())
        return

    if step == 10:
        await _handle_work(message, user_id, text)

async def _handle_survey(message, user, user_id, text, step):
    if step == 1:
        database.save_answer(user_id, "name", text)
        database.set_step(user_id, 2)
        await message.answer("Сколько тебе лет?")
    elif step == 2:
        if not text.isdigit():
            await message.answer("Цифрами, пожалуйста.")
            return
        age = int(text)
        if age < 18:
            if user.get('age_attempts', 0) >= 1:
                await message.answer("🐾 <b>Давай немного подождём!</b>\n\nGOODKAT AI доступен с 18 лет. Береги себя! 🐾💚")
                database.set_step(user_id, 99)
                return
            database.save_answer(user_id, "age_attempts", 1)
            await message.answer("Точно? Введи возраст еще раз.")
            return
        database.save_answer(user_id, "age", age)
        database.set_step(user_id, 3)
        _pending_agree.add(user_id)
        await message.answer(
            "💚 <b>Пара важных слов перед началом</b>\n\n"
            "Забота о себе — это здорово и важно в любом возрасте. Я очень хочу помочь тебе на этом пути.\n\n"
            "Но чтобы наше путешествие было безопасным: если у тебя есть хронические заболевания, особенности здоровья или ты ждешь малыша — пожалуйста, сначала посоветуйся с врачом.\n\n"
            "Я берегу твое здоровье, ведь оно важнее любых цифр. 🐾",
            reply_markup=_agree_keyboard()
        )
    elif step == 3:
        if text == "1":
            database.set_step(user_id, 10)
            database.save_answer(user_id, "week", 1)
            await message.answer(
                "🐾 <b>НЕДЕЛЯ ПРИВЫЧЕК</b>\n\n"
                "7 дней, чтобы лучше узнать себя. Ты будешь рассказывать мне о своём питании, а я помогу всё записать.\n\n" 
                + DIARY_HELP_TEXT + "\n\n" + DIARY_HELP_TAIL,
                reply_markup=_main_menu_keyboard(database.get_user(user_id))
            )
        elif text == "2":
            database.set_step(user_id, 4)
            await message.answer(
                "🐾 <b>ПОЛНАЯ ПРОГРАММА</b>\n\n"
                "Отлично, идём настраиваться! Задам несколько коротких вопросов.\n\n"
                "———\n\n"
                "🐾 Для расчётов мне нужно знать твой пол. Напиши <b>М</b> или <b>Ж</b>."
            )
        else:
            await message.answer("Напиши <b>1</b> или <b>2</b>.")
    elif step == 4:
        if text.upper() not in ["М", "Ж"]:
            await message.answer("Пожалуйста, напиши <b>М</b> или <b>Ж</b>. 🙏")
            return
        database.save_answer(user_id, "gender", text.upper())
        database.set_step(user_id, 5)
        await message.answer("Спасибо! А какой у тебя рост в см? (например, 170)")
    elif step == 5:
        if not text.isdigit():
            await message.answer("Напиши, пожалуйста, рост цифрами — например, 170. 🙂")
            return
        database.save_answer(user_id, "height", int(text))
        database.set_step(user_id, 6)
        await message.answer("Записал. Теперь вес в кг — можно с дробной частью (например, 70.5).")
    elif step == 6:
        weight = _parse_weight(text)
        if weight is None:
            await message.answer("Напиши, пожалуйста, вес цифрами — например, 70 или 70.5. 🙂")
            return
        database.save_answer(user_id, "weight", weight)
        today_str = database._get_user_date(user_id, user.get('utc_offset', 3))
        database.add_weight(user_id, today_str, weight)
        database.set_step(user_id, 7)
        await message.answer(
            "🐾 Нам остался последний шаг, чтобы посчитать твои нормы.\n\n"
            "Как обычно проходит твой день?\n"
            "1️⃣ Больше сижу (работа за компьютером, машина)\n"
            "2️⃣ Двигаюсь умеренно (прогулки, домашние дела)\n"
            "3️⃣ Много двигаюсь (активная работа, спорт)\n\n"
            "Напиши 1, 2 или 3. 💚"
        )
    elif step == 7:
        mapping = {"1": "сидячий", "2": "умеренный", "3": "активный"}
        if text not in mapping:
            await message.answer("Напиши, пожалуйста, <b>1</b>, <b>2</b> или <b>3</b>. 🙂")
            return
        database.save_answer(user_id, "lifestyle", mapping[text])
        database.set_step(user_id, 9)
        database.save_answer(user_id, "week", 2)
        await _show_norms_and_finish(message, user_id)

async def _show_norms_and_finish(message: types.Message, user_id: int):
    user = database.get_user(user_id)
    norms = _calc_norms(user)
    database.set_step(user_id, 10)
    database.save_answer(user_id, "last_norms_update", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    text_result = (
        "🐾 <b>Теперь у нас есть важные ориентиры!</b>\n\n"
        f"🔥 <b>Чтобы вес оставался примерно таким же</b> — около <b>{norms['support_low']}–{norms['support_high']}</b> ккал в день.\n\n"
        f"🎯 <b>Если хочется немного снизить вес</b> — попробуем диапазон <b>{norms['loss_low']}–{norms['loss_high']}</b> ккал в день.\n\n"
        f"🥚 <b>Белок</b> — примерно <b>{norms['protein_low']}–{norms['protein_high']}</b> г в день.\n\n"
        f"💧 <b>Жидкость</b> — около <b>{norms['water_low']}–{norms['water_high']}</b> мл.\n\n"
        "🧂 <b>Соль</b> — ориентир около <b>5 г</b> в день.\n\n"
        "Будем разбираться во всём постепенно, шаг за шагом. 💚"
    )
    await message.answer(text_result)
    
    await asyncio.sleep(1)
    await message.answer(DIARY_HELP_TEXT + "\n\n" + DIARY_HELP_TAIL, reply_markup=_main_menu_keyboard(user))

THANKS_WORDS = ("спасибо", "благодарю", "спс", "thanks")
GREET_WORDS = ("привет", "здравствуй", "хай", "добрый день", "добрый вечер", "доброе утро")
BYE_WORDS = ("пока", "до свидания", "до встречи", "бай")
OK_WORDS = ("ок", "окей", "хорошо", "понял", "поняла", "ясно", "ага", "угу")

def _service_reply(text_lower: str):
    if any(w in text_lower for w in THANKS_WORDS): return "Пожалуйста! Если что — я рядом. 💚"
    if any(w in text_lower for w in GREET_WORDS): return "Привет! Присылай, что съел."
    if any(w in text_lower for w in BYE_WORDS): return "До связи! Не забудь записать. 👋"
    if text_lower in OK_WORDS: return "Хорошо! Жду, что съел."
    return None

async def _handle_work(message, user_id, text):
    text_lower = text.lower().strip()
    user = database.get_user(user_id)
    today_str = database._get_user_date(user_id, user.get('utc_offset', 3))

    if message.photo:
        await _handle_photo(message, user_id, text if text else None)
        return

    if text == BTN_EAT:
        _help_mode.discard(user_id)
        _help_waiting_answer.discard(user_id)
        _pending_clarification.pop(user_id, None)
        await message.answer("Пришли, что съел — текстом или фото.")
        return
    if text == BTN_SUMMARY or text_lower in ["день", "итог", "итоги"]:
        _help_mode.discard(user_id)
        _help_waiting_answer.discard(user_id)
        _pending_clarification.pop(user_id, None)
        await _send_day_summary(message, user_id)
        return
    if text == BTN_HELP:
        _help_mode.add(user_id)
        await message.answer("Я тут! Спрашивай, что тебя интересует про еду или питание. 💚")
        return

    # НОВАЯ ЛОГИКА ОЖИДАНИЯ "ДА" ИЛИ "НЕТ"
    if user_id in _help_waiting_answer:
        if text_lower in ("да", "ага", "угу", "конечно"):
            _help_waiting_answer.discard(user_id)
            await message.answer("Хорошо, спрашивай! 💚")
            return
        elif text_lower in ("нет", "неа", "хватит", "всё"):
            _help_waiting_answer.discard(user_id)
            _help_mode.discard(user_id)
            await message.answer("Понял, возвращаемся к дневнику. Присылай, что съел — текстом или фото.", reply_markup=_main_menu_keyboard(user))
            return
        else:
            # ТУТ МЫ ИГНОРИРУЕМ ЛЮБОЙ ДРУГОЙ ТЕКСТ
            await message.answer("Напиши 'да' или 'нет'.")
            return

    if user_id in _help_mode:
        if any(w in text_lower for w in THANKS_WORDS):
            _help_mode.discard(user_id)
            await message.answer("Пожалуйста! Если что — я рядом. 💚")
            return
        await message.answer("Секунду, думаю...")
        answer = _safe_ask_ai(text)
        if answer: 
            await message.answer(f"{_esc(answer)}\n\n<i>Есть еще вопросы? Напиши 'да' или 'нет'.</i>")
            _help_waiting_answer.add(user_id)
        else: 
            await message.answer("Не смог ответить.")
        return

    if user_id in _pending_clarification:
        if any(w in text_lower for w in THANKS_WORDS + GREET_WORDS + BYE_WORDS):
            _pending_clarification.pop(user_id, None)
            service = _service_reply(text_lower)
            if service: await message.answer(service); return
        else:
            await _handle_clarification_answer(message, user_id, text, today_str)
            return

    service = _service_reply(text_lower)
    if service: await message.answer(service); return

    if not text:
        await message.answer("Я понимаю только текст и фото.")
        return

    await message.answer("Секунду, смотрю...")
    
    ai_res = ai.get_questions_for_ai(text)
    dish = ai_res.get("dish", text)
    questions = ai_res.get("questions", [])

    if not questions:
        food_data = ai.calculate_nutrition(dish, text)
        if food_data and food_data.get('calories') is not None:
            await _save_food_and_reply(message, user_id, food_data, fallback_dish=dish, today_str=today_str, is_fix=False)
        else:
            await message.answer("Не смог посчитать. Попробуй написать вес (например 200 г).")
        return

    _pending_clarification[user_id] = {
        "original_text": text,
        "dish": dish,
        "questions": questions,
        "answers": [],
        "current_index": 0,
        "is_fix": False
    }
    
    await message.answer(f"Записал: {_esc(dish)}. {_esc(questions[0])}")


async def _handle_clarification_answer(message, user_id, text, today_str):
    pending = _pending_clarification.get(user_id)
    if not pending: return

    if pending.get("is_fix"):
        _pending_clarification.pop(user_id, None)
        await message.answer("Секунду, смотрю...")
        
        ai_res = ai.get_questions_for_ai(text)
        dish = ai_res.get("dish", text)
        questions = ai_res.get("questions", [])

        if not questions:
            food_data = ai.calculate_nutrition(dish, text)
            if food_data and food_data.get('calories') is not None:
                await _save_food_and_reply(message, user_id, food_data, fallback_dish=dish, today_str=today_str, is_fix=True)
            else:
                await message.answer("Не смог посчитать. Попробуй написать вес (например 200 г).")
            return

        _pending_clarification[user_id] = {
            "original_text": text,
            "dish": dish,
            "questions": questions,
            "answers": [],
            "current_index": 0,
            "is_fix": True
        }
        await message.answer(f"Записал: {_esc(dish)}. {_esc(questions[0])}")
        return

    answer = text.strip()
    if answer.isdigit() and len(answer) <= 2:
        current_q = pending["questions"][pending["current_index"]].lower()
        if "штук" in current_q or "сколько" in current_q:
            pass
        else:
            await message.answer(f"Ты имеешь в виду {answer} штуки или {answer} грамма?")
            return

    if any(w in answer.lower() for w in ("не знаю", "незнаю", "как обычно", "на глаз")):
        answer = "стандарт"

    pending["answers"].append(answer)
    pending["current_index"] += 1

    if pending["current_index"] < len(pending["questions"]):
        q = pending["questions"][pending["current_index"]]
        _pending_clarification[user_id] = pending
        await message.answer(f"Понял. {_esc(q)}")
        return

    _pending_clarification.pop(user_id, None)
    dish = pending["dish"]
    details = ", ".join(pending["answers"])
    is_fix = pending.get("is_fix", False)
    
    await message.answer(f"Понял. Секунду, считаю «{_esc(dish)}»...")
    food_data = ai.calculate_nutrition(dish, details)
    
    if not food_data or food_data.get('calories') is None:
        await message.answer(f"Не смог распознать КБЖУ для «{_esc(dish)}». Попробуй иначе.")
        return
    await _save_food_and_reply(message, user_id, food_data, fallback_dish=dish, today_str=today_str, is_fix=is_fix)


async def _handle_photo(message, user_id, caption=None):
    await message.answer("Смотрю на фото...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    os.makedirs("downloads", exist_ok=True)
    file_path = f"downloads/{photo.file_id}.jpg"

    try:
        await bot.download_file(file.file_path, file_path)
        photo_result = ai.analyze_photo(file_path)
    finally:
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except OSError: pass

    if not photo_result or not photo_result.get("is_food"):
        await message.answer("Похоже, это не еда. Возможно, я ошибся — пришли другое фото или напиши текстом. 🐾")
        return

    desc = photo_result.get("description", "") or ""
    is_compound = photo_result.get("is_compound", False)

    if is_compound or "," in desc or " и " in desc:
        _pending_clarification[user_id] = {
            "original_text": desc,
            "dish": desc,
            "questions": ["Если всё верно — уточни вес каждого (например: суп 300, макароны 200). Или скажи 'не знаю', и я возьму средние порции."],
            "answers": [],
            "current_index": 0,
            "is_fix": False,
            "is_draft": True
        }
        await message.answer(f"Вижу: {_esc(desc)}\n\nЕсли всё верно — уточни вес каждого, или я могу посчитать средние значения. Если нужны правки — напиши свой вариант.")
    else:
        _pending_clarification[user_id] = {
            "original_text": desc,
            "dish": desc,
            "questions": ["Сколько примерно грамм?"],
            "answers": [],
            "current_index": 0,
            "is_fix": False
        }
        await message.answer(f"Вижу: {_esc(desc)}\n\nСколько примерно грамм?")


async def _save_food_and_reply(message, user_id, food_data, fallback_dish, today_str, is_fix=False):
    cal = food_data.get('calories', 0) or 0
    prot = food_data.get('protein', 0) or 0
    fat = food_data.get('fat', 0) or 0
    carb = food_data.get('carbs', 0) or 0
    weight = food_data.get('weight') or 100
    dish_name = food_data.get('dish') or fallback_dish

    if not dish_name:
        await message.answer("Похоже, это не еда.")
        return

    is_liquid = 1 if food_data.get('is_liquid') else 0
    ml = food_data.get('ml')
    salt = food_data.get('salt', 0) or 0

    try:
        if is_fix:
            database.update_last_food(
                user_id, today_str, dish_name, cal, prot, fat, carb,
                food_data.get('sugar', 0), food_data.get('bad_fat', 0), weight,
                is_liquid=is_liquid, ml=ml, salt=salt
            )
        else:
            database.add_food(
                user_id, today_str, dish_name, cal, prot, fat, carb,
                food_data.get('sugar', 0), food_data.get('bad_fat', 0), weight,
                is_liquid=is_liquid, ml=ml, salt=salt
            )
    except Exception:
        logger.exception("Ошибка сохранения еды (is_fix=%s)", is_fix)
        await message.answer("Не смог сохранить. Попробуй ещё раз.")
        return

    user = database.get_user(user_id)
    norms = _calc_norms(user)
    summary = database.get_day_summary(user_id, today_str)
    liquid_total = database.get_day_liquid(user_id, today_str)

    day_cal = int(summary['calories']) if summary else 0
    day_prot = summary['protein'] if summary else 0
    day_fat = summary['fat'] if summary else 0
    day_carb = summary['carbs'] if summary else 0

    action_word = "исправил" if is_fix else "записал"
    reply_text = f"Отлично, {action_word}: {_esc(dish_name)} ({weight} г/мл)\n"
    reply_text += f"🔥 {cal} ккал | Б {prot} / Ж {fat} / У {carb}\n\n"
    
    if user.get('week', 1) == 1:
        reply_text += f"📊 <b>За сегодня:</b> {day_cal} ккал | Б {day_prot} / Ж {day_fat} / У {day_carb}\n"
        reply_text += f"💧 Вода: {liquid_total} мл"
    else:
        target_cal = norms['support_high']
        target_prot = norms['protein_high']
        target_water = norms['water_high']
        
        p_cal = _calc_percent(day_cal, target_cal)
        p_prot = _calc_percent(day_prot, target_prot)
        p_water = _calc_percent(liquid_total, target_water)
        
        reply_text += f"📊 <b>За сегодня:</b> {day_cal} из {target_cal} ккал ({p_cal}%)\n"
        reply_text += f"🥩 Белок: {day_prot} из {target_prot} г ({p_prot}%)\n"
        reply_text += f"💧 Вода: {liquid_total} из {target_water} мл ({p_water}%)"
    
    if "(среднее)" in dish_name or "(средняя" in dish_name or "стандарт" in dish_name.lower():
         reply_text += "\n\n<i>Взял средний показатель.</i>"

    await message.answer(reply_text, reply_markup=_food_inline_keyboard())

def _safe_ask_ai(question):
    try: return ai.ask_ai_question(question)
    except Exception: logger.exception("Ошибка ai.ask_ai_question"); return None

def _parse_weight(text):
    try: return float(text.strip().replace(",", "."))
    except ValueError: return None

async def main():
    database.init_db()
    logger.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())