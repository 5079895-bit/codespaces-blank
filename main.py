import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
_pending_clarification: dict[int, dict] = {}
_pending_restrictions_text: set[int] = set()
_pending_ack: set[int] = set()
_pending_week_end: set[int] = set()
_pending_weekly_weight: set[int] = set()

BTN_EAT = "🍽 Записать еду"
BTN_SUMMARY = "📊 Итог дня"
BTN_HELP = "💡 Помощь / совет"
BTN_CONTINUE = "➡️ Продолжаем?"
BTN_RES_NO = "✅ Нет ограничений"
BTN_RES_YES = "⚠️ Есть ограничения"
BTN_ACK = "✅ Я всё понял(а), продолжаем"

def _esc(text) -> str:
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _get_lock(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock

def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_EAT)],
            [KeyboardButton(text=BTN_SUMMARY)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def _continue_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CONTINUE)]],
        resize_keyboard=True,
        is_persistent=True,
    )

def _restrictions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_RES_NO)], [KeyboardButton(text=BTN_RES_YES)]],
        resize_keyboard=True,
        is_persistent=True,
    )

def _ack_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ACK)]],
        resize_keyboard=True,
        is_persistent=True,
    )

def _calc_norms(user: dict) -> dict:
    weight = float(user['weight']) if user and user.get('weight') else 70.0
    height = user.get('height') if user and user.get('height') else 170
    age = user.get('age') if user and user.get('age') else 30
    gender = user.get('gender') if user and user.get('gender') else 'Ж'
    lifestyle = (user.get('lifestyle') or 'умеренный').lower()

    if gender == "М":
        bmr = (10 * weight) + (6.25 * height) - (5 * age) + 5
    else:
        bmr = (10 * weight) + (6.25 * height) - (5 * age) - 161

    if "сидяч" in lifestyle: mult = 1.2
    elif "актив" in lifestyle: mult = 1.55
    else: mult = 1.375

    support_center = int(bmr * mult)
    support_low = int(round((support_center - 100) / 50.0) * 50)
    support_high = int(round((support_center + 100) / 50.0) * 50)

    if gender == "М": loss_center = max(support_center - 400, 1500)
    else: loss_center = max(support_center - 400, 1200)
    loss_low = int(round((loss_center - 100) / 50.0) * 50)
    loss_high = int(round((loss_center + 100) / 50.0) * 50)

    prot_center = weight * 1.2
    prot_low = int(round(prot_center * 0.9 / 10.0) * 10)
    prot_high = int(round(prot_center * 1.1 / 10.0) * 10)

    if user.get('user_id'):
        water_low, water_high = database.get_water_range(user['user_id'])
    else:
        center = int(round(weight * 25 / 50.0) * 50)
        if center > 3000: water_low, water_high = 2400, 3000
        else:
            water_low = int(round(center * 0.9 / 50.0) * 50)
            water_high = min(int(round(center * 1.1 / 50.0) * 50), 3000)

    return {
        "support_low": support_low, "support_high": support_high,
        "loss_low": loss_low, "loss_high": loss_high,
        "protein_low": prot_low, "protein_high": prot_high,
        "water_low": water_low, "water_high": water_high,
        "weight": weight,
    }

PROTEIN_BLOCK = (
    "<b>Почему я обращаю твоё внимание на БЕЛОК</b>\n\n"
    "Белок — это база, на которой держится всё остальное. Он поддерживает мышцы "
    "(а мышцы сжигают калории даже в покое), даёт долгое чувство сытости и помогает "
    "коже и волосам оставаться в тонусе. Когда белка достаточно, худеть проще и спокойнее — "
    "меньше тянет на перекусы.\n\n"
    "Поэтому я буду считать белок отдельно и мягко подсказывать, сколько его примерно нужно. "
    "Это ориентир, а не обязательство: если сегодня получилось меньше — ничего страшного, "
    "просто постарайся добавить белок в следующий приём пищи, если будет удобно."
)

def _calories_line(total_cal: int, norms: dict) -> str:
    loss_low, loss_high, support_high = norms['loss_low'], norms['loss_high'], norms['support_high']
    if total_cal <= loss_high:
        rem_low = max(loss_low - total_cal, 0)
        rem_high = max(loss_high - total_cal, 0)
        return f"🔥 Съедено: {total_cal} ккал (осталось на сегодня ~{rem_low}–{rem_high})"
    elif total_cal <= support_high:
        return f"🔥 Съедено: {total_cal} ккал (чуть выше диапазона снижения — это просто цифра. До нормы поддержания ещё есть запас)"
    else:
        return f"🔥 Съедено: {total_cal} ккал (чуть выше диапазона — это просто цифра. Завтра просто продолжим)"

async def _send_day_summary(message: types.Message, user_id: int):
    summary = database.get_day_summary(user_id)
    water_total = database.get_day_water(user_id)
    water_low, water_high = database.get_water_range(user_id)
    user = database.get_user(user_id)
    norms = _calc_norms(user)

    if summary is None and water_total == 0:
        await message.answer("Сегодня ты еще ничего не записывал. Присылай, что съел!")
        return

    lines = ["📊 <b>Итог дня:</b>\n"]
    if summary is None:
        lines.append("🍽 Еда: пока ничего не записано.\n")
    else:
        lines.append(_calories_line(int(summary['calories']), norms) + "\n")
        lines.append(f"🥩 Белка: {summary['protein']} г из ~{norms['protein_low']}–{norms['protein_high']} г\n")
        lines.append(f"🧈 Жиры: {summary['fat']} / Углеводы: {summary['carbs']}\n")
        lines.append(f"🍭 Плохие сахара: {summary['sugar']} г\n")
        lines.append(f"🥓 Вредные жиры: {summary['bad_fat']} г\n")

    water_pct = int(round(water_total / water_high * 100)) if water_high else 0
    lines.append(f"\n💧 Вода: {water_total} мл из {water_low}–{water_high} мл ({water_pct}%)")
    await message.answer("".join(lines))

def _week_ready(user_id: int, user: dict) -> bool:
    """Проверяет, пора ли подводить итог недели.
    Для 1-й недели: прошло 7 дней и 5 дней с едой.
    Для 2+ недель: прошло 7 дней с прошлого итога (created_at + week*7)."""
    week = user.get('week', 1)
    days_passed = database.get_days_since_registration(user_id)
    food_days = database.get_unique_food_days(user_id)

    if week == 1:
        return days_passed >= 7 and food_days >= 5

    # для 2+ недель: проверяем, прошло ли 7 дней с начала текущей недели
    # начало текущей недели = created_at + (week-1)*7 дней
    need_days = 7 * week
    return days_passed >= need_days and food_days >= 5

def _build_week_summary(user_id: int, week: int) -> str:
    food_days = database.get_unique_food_days(user_id)
    if week == 1:
        return (
            f"🎉 <b>Первая неделя завершена!</b>\n\n"
            f"Ты записывал еду <b>{food_days} дней из 7</b>. Это отличный результат!\n\n"
            "Дальше — полная программа: посчитаем нормы, ориентиры по белку и воде. Жми <b>«Продолжаем?»</b>."
        )
    return (
        f"🎉 <b>Неделя {week} завершена!</b>\n\n"
        f"Ты записывал еду <b>{food_days} дней</b>. Молодец!\n\n"
        "Давай взвесимся, чтобы видеть динамику. Жми <b>«Продолжаем?»</b>."
    )

async def _check_week_progress(message: types.Message, user_id: int, user: dict) -> bool:
    if not _week_ready(user_id, user): return False
    _pending_week_end.add(user_id)
    await message.answer(_build_week_summary(user_id, user.get('week', 1)), reply_markup=_continue_keyboard())
    return True

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = database.get_user(user_id)

    if user is None:
        await message.answer("Привет! Введи код-приглашение:")
        return

    step = user['survey_step']
    if step == 0:
        database.set_step(user_id, 1)
        await message.answer("Давай познакомимся. Как тебя зовут?")
    elif step < 10:
        await message.answer("Мы не закончили знакомство. Продолжим? Напиши любое сообщение.")
    else:
        if user_id in _pending_week_end:
            await message.answer("Жми «Продолжаем?», чтобы перейти к следующей неделе.", reply_markup=_continue_keyboard())
            return
        await message.answer("Привет! Присылай, что съел.", reply_markup=_main_menu_keyboard())

@dp.message(Command("test_week2"))
async def cmd_test_week2(message: types.Message):
    user_id = message.from_user.id
    if not database.get_user(user_id):
        await message.answer("Сначала /start.")
        return
    old_date = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%d %H:%M:%S')
    conn = database._connect()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET created_at = ?, week = 1 WHERE user_id = ?', (old_date, user_id))
    for i in range(5):
        date_str = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        cursor.execute('INSERT INTO food_log (user_id, date, dish, calories, protein, fat, carbs, sugar, bad_fat, weight) VALUES (?, ?, ?, 500, 20, 15, 60, 5, 2, 100)', (user_id, date_str, 'Тест'))
    conn.commit()
    conn.close()
    await message.answer("🧪 <b>ТЕСТ АКТИВИРОВАН!</b>\nНапиши 'Привет', чтобы проверить переход.")

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
    _pending_clarification.pop(user_id, None)
    _pending_week_end.discard(user_id)
    _pending_weekly_weight.discard(user_id)
    _pending_ack.discard(user_id)
    _pending_restrictions_text.discard(user_id)

    await message.answer("🧹 Тест сброшен. Всё удалено. Ты на первой неделе.", reply_markup=_main_menu_keyboard())

@dp.message()
async def handle_message(message: types.Message):
    lock = _get_lock(message.from_user.id)
    async with lock:
        await _handle_message_locked(message)

async def _handle_message_locked(message: types.Message):
    user_id = message.from_user.id
    user = database.get_user(user_id)
    text = message.text.strip() if message.text else ""

    if user is None:
        if text == INVITE_CODE:
            database.create_user(user_id)
            database.set_step(user_id, 1)
            await message.answer("Код верный! Давай познакомимся. Как тебя зовут?")
        else:
            await message.answer("Код не подошёл.")
        return

    if user_id in _pending_ack:
        if text == BTN_ACK:
            _pending_ack.discard(user_id)
            await _show_norms_and_finish(message, user_id)
            return
        else:
            await message.answer("Нажми кнопку ниже.", reply_markup=_ack_keyboard())
            return

    # --- Взвешивание в конце недели ---
    if user_id in _pending_weekly_weight:
        weight = _parse_weight(text)
        if weight is None:
            await message.answer("Пожалуйста, напиши вес цифрами (например: 75 или 75.5).")
            return

        _pending_weekly_weight.discard(user_id)
        database.add_weight(user_id, weight)

        old_weight = user.get('weight')
        last_update_str = user.get('last_norms_update')

        days_ok = False
        if not last_update_str:
            days_ok = True
        else:
            try:
                last_update = datetime.strptime(last_update_str, '%Y-%m-%d %H:%M:%S').date()
                days_ok = (datetime.now().date() - last_update).days >= 21
            except ValueError:
                days_ok = True

        weight_down = (old_weight is None) or (weight < float(old_weight))

        if days_ok and weight_down:
            database.save_answer(user_id, "weight", weight)
            database.save_answer(user_id, "last_norms_update", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            updated_user = database.get_user(user_id)
            norms = _calc_norms(updated_user)
            await message.answer(
                f"⚖️ Вес записан: {weight} кг.\n\n"
                "Прошло 3 недели — я обновил твои нормы под новый вес!\n\n"
                f"🔥 <b>Поддержание:</b> ~{norms['support_low']}–{norms['support_high']} ккал/день\n"
                f"🎯 <b>Снижение:</b> ~{norms['loss_low']}–{norms['loss_high']} ккал/день\n"
                f"🥩 <b>Белок:</b> ~{norms['protein_low']}–{norms['protein_high']} г/день\n"
                f"💧 <b>Вода:</b> {norms['water_low']}–{norms['water_high']} мл/день\n\n"
                "Продолжаем в том же духе!",
                reply_markup=_main_menu_keyboard()
            )
        else:
            if days_ok and not weight_down:
                await message.answer(
                    f"⚖️ Вес записан: {weight} кг.\n\n"
                    "Нормы пока не меняю — посмотрим динамику дальше. 💚",
                    reply_markup=_main_menu_keyboard()
                )
            else:
                await message.answer(
                    f"⚖️ Вес записан: {weight} кг.\n\n"
                    "Нормы калорий пересчитываем раз в 3 недели, чтобы не дёргать тебя из-за колебаний воды. "
                    "Пока вес просто сохраняется для истории.",
                    reply_markup=_main_menu_keyboard()
                )

        database.save_answer(user_id, "week", user.get('week', 1) + 1)
        return

    step = user['survey_step']

    if step < 10:
        await _handle_survey(message, user, user_id, text, step)
        return

    if step == 99: return

    if user_id in _pending_week_end:
        if text == BTN_CONTINUE:
            _pending_week_end.discard(user_id)
            current_week = user.get('week', 1)
            if current_week == 1:
                # Переход 1→2: полный опрос
                database.set_step(user_id, 4)
                database.save_answer(user_id, "week", 2)
                await message.answer("Отлично! Твой пол? (М или Ж)", reply_markup=ReplyKeyboardRemove())
            else:
                # Переход 2→3, 3→4...: взвешивание
                _pending_weekly_weight.add(user_id)
                await message.answer("Отлично! Напиши свой текущий вес цифрами (например: 75.5).", reply_markup=ReplyKeyboardRemove())
            return
        await message.answer(_build_week_summary(user_id, user.get('week', 1)), reply_markup=_continue_keyboard())
        return

    if step == 10:
        if await _check_week_progress(message, user_id, user):
            return
        await _handle_work(message, user_id, text)

async def _handle_survey(message, user, user_id, text, step):
    if step == 1:
        if not re.match(r"^[а-яА-ЯёЁa-zA-Z]+$", text):
            await message.answer("Пожалуйста, буквами.")
            return
        database.save_answer(user_id, "name", text)
        database.set_step(user_id, 2)
        await message.answer("Сколько тебе лет?")
    elif step == 2:
        if not text.isdigit():
            await message.answer("Цифрами, пожалуйста.")
            return
        age = int(text)
        if age < 18 or age > 80:
            attempts = user.get('age_attempts', 0)
            if attempts >= 1:
                await message.answer("К сожалению, не смогу работать.")
                database.set_step(user_id, 99)
                return
            database.save_answer(user_id, "age_attempts", attempts + 1)
            await message.answer("Точно? Введи возраст еще раз.")
            return
        database.save_answer(user_id, "age", age)
        database.set_step(user_id, 3)
        await message.answer(
            "У нас есть два пути:\n\n"
            "<b>1️⃣ Неделя привычек</b> — без правил.\n"
            "<b>2️⃣ Полная программа</b> — сразу настроим.\n\n"
            "Напиши <b>1</b> или <b>2</b>."
        )
    elif step == 3:
        if text == "1":
            database.set_step(user_id, 10)
            database.save_answer(user_id, "week", 1)
            await message.answer("Супер! Записывай еду. Через 7 дней подведём итоги.", reply_markup=_main_menu_keyboard())
        elif text == "2":
            database.set_step(user_id, 4)
            await message.answer("Отлично! Твой пол? (М или Ж)")
        else:
            await message.answer("Напиши <b>1</b> или <b>2</b>.")
    elif step == 4:
        if text.upper() not in ["М", "Ж"]:
            await message.answer("Напиши 'М' или 'Ж'.")
            return
        database.save_answer(user_id, "gender", text.upper())
        database.set_step(user_id, 5)
        await message.answer("Твой рост в см?")
    elif step == 5:
        if not text.isdigit():
            await message.answer("Цифрами.")
            return
        database.save_answer(user_id, "height", int(text))
        database.set_step(user_id, 6)
        await message.answer("Твой вес в кг?")
    elif step == 6:
        weight = _parse_weight(text)
        if weight is None:
            await message.answer("Напиши вес цифрами (например: 70 или 70.5).")
            return
        database.save_answer(user_id, "weight", weight)
        database.add_weight(user_id, weight)
        database.set_step(user_id, 7)
        await message.answer("Образ жизни?\n1 — сидячий\n2 — умеренный\n3 — активный")
    elif step == 7:
        mapping = {"1": "сидячий", "2": "умеренный", "3": "активный"}
        if text not in mapping:
            await message.answer("Напиши 1, 2 или 3.")
            return
        database.save_answer(user_id, "lifestyle", mapping[text])
        database.set_step(user_id, 8)
        await message.answer("Есть медицинские ограничения?", reply_markup=_restrictions_keyboard())
    elif step == 8:
        if text == BTN_RES_NO:
            database.save_answer(user_id, "restrictions", "нет")
            database.set_step(user_id, 9)
            database.save_answer(user_id, "week", 2)
            await _show_norms_and_finish(message, user_id)
        elif text == BTN_RES_YES:
            _pending_restrictions_text.add(user_id)
            await message.answer("Напиши, какие именно.", reply_markup=ReplyKeyboardRemove())
        elif user_id in _pending_restrictions_text:
            database.save_answer(user_id, "restrictions", text)
            _pending_restrictions_text.discard(user_id)
            database.set_step(user_id, 10)
            database.save_answer(user_id, "week", 2)
            _pending_ack.add(user_id)
            await message.answer(
                "⚠️ <b>Спасибо, что поделился.</b> Обязательно проконсультируйся с врачом.\n\n"
                "Особенно важно про белок: если есть проблемы с почками/печенью, обсуди норму с доктором.\n\n"
                "Нажми кнопку ниже, чтобы продолжить.",
                reply_markup=_ack_keyboard()
            )
        else:
            await message.answer("Выбери вариант.", reply_markup=_restrictions_keyboard())

async def _show_norms_and_finish(message: types.Message, user_id: int):
    updated_user = database.get_user(user_id)
    norms = _calc_norms(updated_user)
    database.set_step(user_id, 10)
    database.save_answer(user_id, "last_norms_update", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    text_result = (
        f"🔥 <b>Поддержание:</b> ~{norms['support_low']}–{norms['support_high']} ккал/день\n"
        f"🎯 <b>Снижение:</b> ~{norms['loss_low']}–{norms['loss_high']} ккал/день\n"
        f"🥩 <b>Белок:</b> ~{norms['protein_low']}–{norms['protein_high']} г/день\n"
        f"💧 <b>Вода:</b> {norms['water_low']}–{norms['water_high']} мл/день\n\n"
        f"{PROTEIN_BLOCK}\n\n"
        "Теперь присылай, что съел."
    )
    await message.answer(text_result, reply_markup=_main_menu_keyboard())

THANKS_WORDS = ("спасибо", "благодарю", "спс", "thanks")
GREET_WORDS = ("привет", "здравствуй", "хай", "добрый день", "добрый вечер", "доброе утро")
BYE_WORDS = ("пока", "до свидания", "до встречи", "бай")
OK_WORDS = ("ок", "окей", "хорошо", "понял", "поняла", "ясно", "ага", "угу")

def _service_reply(text_lower: str):
    if any(w in text_lower for w in THANKS_WORDS): return "Пожалуйста! Если что — я рядом. 💚"
    if any(w in text_lower for w in GREET_WORDS): return "Привет! Присылай, что съел."
    if any(w in text_lower for w in BYE_WORDS): return "До связи! Не забудь записать, что съел. 👋"
    if text_lower in OK_WORDS: return "Хорошо! Жду, что съел."
    return None

WATER_TRIGGERS = ("вода", "выпил", "выпила", "воды", "мл воды", "стакан воды")
WATER_FOOD_WORDS = ("съел", "съела", "ел", "ела", "поел", "поела", "суп", "борщ", "каша", "еда", "обед", "завтрак", "ужин", "кола", "сок", "морс", "компот", "лимонад", "квас", "пиво", "вино", "кофе", "чай", "капучино", "латте", "смузи", "энергетик", "фанта", "спрайт", "пепси", "милкшейк", "коктейль")

def _parse_water(text: str):
    text_lower = text.lower().strip()
    if not any(t in text_lower for t in WATER_TRIGGERS): return None
    if any(w in text_lower for w in WATER_FOOD_WORDS): return None
    match = re.search(r"(\d+(?:[.,]\d+)?)", text_lower)
    if not match:
        if "стакан" in text_lower: return 250
        return -1
    value = float(match.group(1).replace(",", "."))
    if "л" in text_lower or value < 10: ml = int(value * 1000)
    else: ml = int(value)
    if ml <= 0 or ml > 5000: return None
    return ml

async def _handle_work(message, user_id, text):
    text_lower = text.lower().strip()

    if user_id in _pending_clarification:
        if text in (BTN_EAT, BTN_SUMMARY, BTN_HELP):
            _pending_clarification.pop(user_id, None)
        elif any(w in text_lower for w in THANKS_WORDS + GREET_WORDS + BYE_WORDS):
            _pending_clarification.pop(user_id, None)
            service = _service_reply(text_lower)
            if service: await message.answer(service); return
        else:
            await _handle_clarification_answer(message, user_id, text)
            return

    if text == BTN_EAT:
        _help_mode.discard(user_id)
        await message.answer("Пришли, что съел — текстом или фото.")
        return

    if text == BTN_SUMMARY or text_lower in ["день", "итог", "итоги", "на сегодня всё", "на сегодня все"]:
        _help_mode.discard(user_id)
        await _send_day_summary(message, user_id)
        return

    if text == BTN_HELP:
        _help_mode.add(user_id)
        await message.answer("Спрашивай — помогу разобраться с питанием.")
        return

    if user_id in _help_mode:
        if any(w in text_lower for w in THANKS_WORDS):
            _help_mode.discard(user_id)
            await message.answer("Пожалуйста! Если что — я рядом. 💚")
            return
        _help_mode.discard(user_id)
        await message.answer("Секунду, думаю...")
        answer = _safe_ask_ai(text)
        if answer: await message.answer(_esc(answer))
        else: await message.answer("Не смог ответить.")
        return

    service = _service_reply(text_lower)
    if service: await message.answer(service); return

    water_ml = _parse_water(text_lower)
    if water_ml == -1:
        await message.answer("Сколько воды? Напиши в мл (например: 200) или литрах (0.5).")
        return
    if water_ml is not None and water_ml > 0:
        database.add_water(user_id, water_ml)
        total = database.get_day_water(user_id)
        water_low, water_high = database.get_water_range(user_id)
        reply = f"💧 Записал: {water_ml} мл. Всего: {total} мл из {water_low}–{water_high} мл."
        if water_ml > 3000: reply += "\n\n3 л — ориентир, а не потолок. 💚"
        await message.answer(reply)
        return

    if message.photo:
        await _handle_photo(message, user_id)
        return

    if not text:
        await message.answer("Я понимаю только текст и фото. Пришли, что съел.")
        return

    impulse_words = ["хочу", "хочется", "захотелось", "ужасно хочу", "не могу удержаться"]
    is_negated = "не хочу" in text_lower or "не хочется" in text_lower
    if (not is_negated) and any(word in text_lower for word in impulse_words):
        await message.answer("Секунду, подумаю вместе с тобой...")
        answer = _safe_ask_ai(text)
        if answer: await message.answer(_esc(answer))
        else: await message.answer("Не смог ответить.")
        return

    is_correction = "это было" in text_lower or "на самом деле" in text_lower or "пересчитай" in text_lower
    if text.isdigit():
        await _handle_correction_by_weight(message, user_id, int(text))
        return
    has_number = re.search(r'\d+', text)
    if has_number and not any(w in text_lower for w in ["грамм", "мл", "гр"]):
        weight = int(re.search(r'\d+', text).group())
        last_dish = database.get_last_dish(user_id)
        if last_dish:
            first_word = last_dish.split()[0].lower()
            if first_word in text_lower:
                await _handle_correction_by_weight(message, user_id, weight)
                return
    elif is_correction:
        await _handle_correction(message, user_id, text)
        return

    await message.answer("Секунду, считаю...")
    food_data = _safe_analyze_food(text)

    if not food_data:
        await message.answer("Не смог распознать еду.\n\nЕсли хочешь спросить — нажми 💡 Помощь.")
        return

    if food_data.get('needs_clarification'):
        question = food_data.get('question') or "Уточни, пожалуйста, что именно и сколько?"
        _pending_clarification[user_id] = {"original_text": text, "question": question, "attempts": 0}
        await message.answer(f"Уточню: {_esc(question)}")
        return

    if food_data.get('calories') is not None:
        await _save_food_and_reply(message, user_id, food_data, fallback_dish='блюдо')
    else:
        await message.answer("Не смог распознать еду.")

async def _handle_clarification_answer(message, user_id, text):
    pending = _pending_clarification.get(user_id)
    if not pending: return

    if pending.get("is_water_photo"):
        _pending_clarification.pop(user_id, None)
        water_ml = _parse_water("вода " + text)
        if water_ml and water_ml > 0:
            database.add_water(user_id, water_ml)
            total = database.get_day_water(user_id)
            water_low, water_high = database.get_water_range(user_id)
            await message.answer(f"💧 Записал: {water_ml} мл. Всего: {total} мл из {water_low}–{water_high} мл.")
        else: await message.answer("Не понял, сколько воды. Напиши число (например: 500).")
        return

    original = pending["original_text"].lower()
    text_lower = text.lower()

    if "кола" in original and "без сахар" in text_lower: dish_name = "Кола Зеро (Coca-Cola Zero)"
    elif "кола" in original and "с сахар" in text_lower: dish_name = "Кола обычная (Coca-Cola)"
    else:
        match = re.search(r"\d+", text)
        if match:
            clean_name = re.sub(r"[\d\s]+(мл|г|гр|грамм|литр|л)?", " ", text, flags=re.IGNORECASE).strip()
            dish_name = re.sub(r'\s+', ' ', f"{original} {clean_name}").strip()
        else: dish_name = re.sub(r'\s+', ' ', f"{original} {text}").strip()

    match = re.search(r"\d+", text)
    extracted_weight = int(match.group()) if match else 100
    _pending_clarification.pop(user_id, None)

    await message.answer(f"Секунду, считаю '{_esc(dish_name)}'...")
    food_data = _safe_analyze_food(f"{dish_name}, порция {extracted_weight} г")

    if not food_data or food_data.get('calories') is None:
        await message.answer(f"Не смог распознать КБЖУ для '{_esc(dish_name)}'. Попробуй иначе.")
        return
    await _save_food_and_reply(message, user_id, food_data, fallback_dish=dish_name)

async def _handle_correction_by_weight(message, user_id, target_weight):
    row = database.get_last_food(user_id)
    if not row:
        await message.answer("Не нашел последней записи.")
        return

    old_weight = row['weight'] if row.get('weight') else 100
    dish_name = row['dish']
    dish_name = re.sub(r'\(\d+\s*г/мл\)', '', dish_name).strip()
    factor = target_weight / old_weight

    new_cal = int(row['calories'] * factor)
    new_prot = round(row['protein'] * factor, 1)
    new_fat = round(row['fat'] * factor, 1)
    new_carb = round(row['carbs'] * factor, 1)
    new_sugar = round(row['sugar'] * factor, 1)
    new_bad_fat = round(row['bad_fat'] * factor, 1)
    new_dish_name = f"{dish_name} ({target_weight} г/мл)"

    database.update_last_food(
        user_id, new_dish_name, new_cal, new_prot, new_fat, new_carb, new_sugar, new_bad_fat, target_weight
    )

    reply_text = (
        f"Обновил: {_esc(new_dish_name)}\n"
        f"🔥 {new_cal} ккал | Б {new_prot} / Ж {new_fat} / У {new_carb}\n"
        f"🍭 Сахара: {new_sugar} г | 🥓 Вредные жиры: {new_bad_fat} г"
    )
    await message.answer(reply_text)

async def _handle_photo(message, user_id):
    await message.answer("Смотрю на фото...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    os.makedirs("downloads", exist_ok=True)
    file_path = f"downloads/{photo.file_id}.jpg"

    try:
        await bot.download_file(file.file_path, file_path)
        description = ai.analyze_photo(file_path)
    finally:
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except OSError: pass

    if not description:
        await message.answer("Не смог разобрать фото. Опиши текстом.")
        return

    desc_lower = description.lower()
    water_signals = ("вода", "бутылка", "стакан", "минеральн")
    food_signals = ("суп", "борщ", "котлет", "мясо", "хлеб", "сыр", "салат", "тарелк", "еда", "пицца", "бургер", "картошк", "макарон", "рис", "гречк", "торт", "печенье", "конфет")
    is_water = any(w in desc_lower for w in water_signals) and not any(f in desc_lower for f in food_signals)

    if is_water:
        _pending_clarification[user_id] = {"original_text": "вода", "question": "Сколько воды?", "attempts": 0, "is_water_photo": True}
        await message.answer("Вижу воду. Сколько там мл? (Напиши число)")
        return

    await message.answer(f"Вижу: {_esc(description)}\n\nСчитаю...")
    food_data = _safe_analyze_food(description)

    if not food_data:
        await message.answer("Не смог посчитать.")
        return

    if food_data.get('needs_clarification'):
        question = food_data.get('question') or "Уточни, что именно и сколько?"
        _pending_clarification[user_id] = {"original_text": description, "question": question, "attempts": 0}
        await message.answer(f"Уточню: {_esc(question)}")
        return

    if food_data.get('calories') is not None:
        await _save_food_and_reply(message, user_id, food_data, fallback_dish=description)
    else:
        await message.answer("Не смог посчитать.")

async def _handle_correction(message, user_id, correction_text):
    last_dish = database.get_last_dish(user_id)
    if not last_dish:
        await message.answer("Я не нашел предыдущей записи.")
        return

    await message.answer("Понял, пересчитываю...")
    food_data = _safe_recalculate(last_dish, correction_text)

    if not (food_data and food_data.get('calories') is not None):
        await message.answer("Не смог пересчитать.")
        return

    try:
        database.update_last_food(
            user_id, food_data.get('dish') or last_dish, food_data.get('calories', 0),
            food_data.get('protein', 0), food_data.get('fat', 0), food_data.get('carbs', 0),
            food_data.get('sugar', 0), food_data.get('bad_fat', 0), food_data.get('weight') or 100
        )
    except Exception:
        logger.exception("Ошибка update_last_food")
        await message.answer("Не смог обновить.")
        return

    await message.answer(f"Обновил: {_esc(food_data.get('dish') or last_dish)}\n🔥 {food_data.get('calories', 0)} ккал.")

async def _save_food_and_reply(message, user_id, food_data, fallback_dish):
    cal = food_data.get('calories', 0) or 0
    prot = food_data.get('protein', 0) or 0
    fat = food_data.get('fat', 0) or 0
    carb = food_data.get('carbs', 0) or 0
    weight = food_data.get('weight') or 100
    dish_name = food_data.get('dish') or fallback_dish

    if not dish_name:
        await message.answer("Похоже, это не еда. Пришли, что съел.")
        return

    try:
        database.add_food(
            user_id, dish_name, cal, prot, fat, carb,
            food_data.get('sugar', 0), food_data.get('bad_fat', 0), weight
        )
    except Exception:
        logger.exception("Ошибка add_food")
        await message.answer("Не смог сохранить. Попробуй ещё раз.")
        return

    sugar_str = food_data.get('sugar', 0) if food_data.get('sugar') is not None else 0
    bad_fat_str = food_data.get('bad_fat', 0) if food_data.get('bad_fat') is not None else 0

    reply_text = (
        f"Записал: {_esc(dish_name)}\n"
        f"🔥 {cal} ккал | Б {prot} / Ж {fat} / У {carb}\n"
        f"🍭 Сахара: {sugar_str} г | 🥓 Вредные жиры: {bad_fat_str} г"
    )

    user = database.get_user(user_id)
    norms = _calc_norms(user)
    summary = database.get_day_summary(user_id)

    if summary:
        total_cal = int(summary['calories'])
        total_prot = summary['protein']
        water_total = database.get_day_water(user_id)
        water_low, water_high = database.get_water_range(user_id)

        reply_text += f"\n\n📊 <b>Итог на сейчас:</b>\n"
        reply_text += _calories_line(total_cal, norms) + "\n"
        reply_text += f"🥩 Белка: {total_prot} г из ~{norms['protein_low']}–{norms['protein_high']} г\n"
        reply_text += f"💧 Вода: {water_total} мл из {water_low}–{water_high} мл"

        if total_prot < norms['protein_low'] - 20:
            reply_text += f"\n\n💚 <i>Можно добавить белок — осталось {int(norms['protein_low'] - total_prot)} г. Но это не обязательно!</i>"

    await message.answer(reply_text)

def _safe_analyze_food(text):
    try: return ai.analyze_food(text)
    except Exception: logger.exception("Ошибка ai.analyze_food"); return None

def _safe_recalculate(dish, weight_text):
    try: return ai.recalculate_food(dish, weight_text)
    except Exception: logger.exception("Ошибка ai.recalculate_food"); return None

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