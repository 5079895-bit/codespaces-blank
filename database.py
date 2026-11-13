import sqlite3
import logging
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_database.db')

def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10) # Добавлен timeout (защита от блокировки, п.3)
    conn.row_factory = sqlite3.Row
    return conn

def _get_user_date(user_id, utc_offset):
    """Возвращает текущую дату пользователя с учетом его часового пояса"""
    now_utc = datetime.utcnow()
    user_now = now_utc + timedelta(hours=utc_offset)
    return user_now.strftime('%Y-%m-%d')

def init_db():
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            gender TEXT,
            age INTEGER,
            height INTEGER,
            weight REAL,
            lifestyle TEXT,
            dislikes TEXT,
            restrictions TEXT,
            survey_step INTEGER DEFAULT 0,
            age_attempts INTEGER DEFAULT 0,
            created_at TEXT,
            week INTEGER DEFAULT 1,
            last_norms_update TEXT,
            utc_offset INTEGER DEFAULT 3
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            dish TEXT,
            calories INTEGER,
            protein REAL,
            fat REAL,
            carbs REAL,
            sugar REAL,
            bad_fat REAL,
            weight REAL,
            is_liquid INTEGER DEFAULT 0,
            ml INTEGER,
            salt REAL DEFAULT 0
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS water_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            ml INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weight_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            weight REAL
        )
    ''')

    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]

    if "age_attempts" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN age_attempts INTEGER DEFAULT 0")
    if "created_at" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
    if "week" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN week INTEGER DEFAULT 1")
    if "last_norms_update" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_norms_update TEXT")
    if "utc_offset" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN utc_offset INTEGER DEFAULT 3")

    cursor.execute("PRAGMA table_info(food_log)")
    food_columns = [row[1] for row in cursor.fetchall()]
    if "weight" not in food_columns:
        cursor.execute("ALTER TABLE food_log ADD COLUMN weight REAL")
    if "is_liquid" not in food_columns:
        cursor.execute("ALTER TABLE food_log ADD COLUMN is_liquid INTEGER DEFAULT 0")
    if "ml" not in food_columns:
        cursor.execute("ALTER TABLE food_log ADD COLUMN ml INTEGER")
    if "salt" not in food_columns:
        cursor.execute("ALTER TABLE food_log ADD COLUMN salt REAL DEFAULT 0")

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_food_log_user_date ON food_log (user_id, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_water_log_user_date ON water_log (user_id, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_weight_log_user_date ON weight_log (user_id, date)')

    conn.commit()
    conn.close()

def get_user(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        user_dict = dict(user)
        user_dict['survey_step'] = int(user_dict.get('survey_step') or 0)
        user_dict['week'] = int(user_dict.get('week') or 1)
        user_dict['weight'] = float(user_dict['weight']) if user_dict.get('weight') else None
        user_dict['utc_offset'] = int(user_dict.get('utc_offset') or 3)
        return user_dict
    return None

def save_answer(user_id, field, value):
    allowed = {"name", "gender", "age", "height", "weight", "lifestyle", "dislikes", "restrictions", "age_attempts", "week", "last_norms_update", "utc_offset"}
    if field not in allowed:
        raise ValueError(f"Недопустимое поле: {field}")
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(f'UPDATE users SET {field} = ? WHERE user_id = ?', (value, user_id))
    conn.commit()
    conn.close()

def set_step(user_id, step):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET survey_step = ? WHERE user_id = ?', (int(step), user_id))
    conn.commit()
    conn.close()

def create_user(user_id):
    conn = _connect()
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        'INSERT OR IGNORE INTO users (user_id, survey_step, created_at, week, last_norms_update) VALUES (?, 0, ?, 1, ?)',
        (user_id, now, now)
    )
    conn.commit()
    conn.close()

def add_food(user_id, date, dish, calories, protein, fat, carbs, sugar, bad_fat, weight, is_liquid=0, ml=None, salt=0):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO food_log (user_id, date, dish, calories, protein, fat, carbs, sugar, bad_fat, weight, is_liquid, ml, salt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, date, dish, calories, protein, fat, carbs, sugar, bad_fat, weight, is_liquid, ml, salt))
    conn.commit()
    conn.close()

def get_day_summary(user_id, date):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT SUM(calories) as cal, SUM(protein) as prot, SUM(fat) as fat,
               SUM(carbs) as carb, SUM(sugar) as sug, SUM(bad_fat) as bad_fat, SUM(salt) as salt
        FROM food_log WHERE user_id = ? AND date = ?
    ''', (user_id, date))
    result = cursor.fetchone()
    conn.close()

    if not result or result['cal'] is None:
        return None

    return {
        "calories": round(result['cal'], 1),
        "protein": round(result['prot'], 1),
        "fat": round(result['fat'], 1),
        "carbs": round(result['carb'], 1),
        "sugar": round(result['sug'], 1),
        "bad_fat": round(result['bad_fat'], 1),
        "salt": round(result['salt'], 1),
    }

def update_last_food(user_id, date, new_dish, calories, protein, fat, carbs, sugar, bad_fat, weight, is_liquid=None, ml=None, salt=0):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM food_log WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1', (user_id, date))
    row = cursor.fetchone()
    if row:
        last_id = row['id']
        if is_liquid is not None:
            cursor.execute('''
                UPDATE food_log SET dish = ?, calories = ?, protein = ?, fat = ?, carbs = ?, sugar = ?, bad_fat = ?, weight = ?, is_liquid = ?, ml = ?, salt = ?
                WHERE id = ?
            ''', (new_dish, calories, protein, fat, carbs, sugar, bad_fat, weight, is_liquid, ml, salt, last_id))
        else:
            cursor.execute('''
                UPDATE food_log SET dish = ?, calories = ?, protein = ?, fat = ?, carbs = ?, sugar = ?, bad_fat = ?, weight = ?, salt = ?
                WHERE id = ?
            ''', (new_dish, calories, protein, fat, carbs, sugar, bad_fat, weight, salt, last_id))
        conn.commit()
    conn.close()

def get_last_food(user_id, date):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM food_log WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1', (user_id, date))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_last_dish(user_id, date):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT dish FROM food_log WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1', (user_id, date))
    row = cursor.fetchone()
    conn.close()
    return row['dish'] if row else None

def add_water(user_id, date, ml):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO water_log (user_id, date, ml) VALUES (?, ?, ?)', (user_id, date, int(ml)))
    conn.commit()
    conn.close()

def get_day_liquid(user_id, date):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(ml) as total FROM water_log WHERE user_id = ? AND date = ?', (user_id, date))
    water_result = cursor.fetchone()
    water_total = water_result['total'] if water_result and water_result['total'] else 0

    cursor.execute('SELECT SUM(ml) as total FROM food_log WHERE user_id = ? AND date = ? AND is_liquid = 1', (user_id, date))
    food_result = cursor.fetchone()
    food_total = food_result['total'] if food_result and food_result['total'] else 0
    conn.close()
    return water_total + food_total

def get_water_range(user, norms):
    # Вода теперь считается от скорректированного веса (п.10)
    corrected_weight = norms.get('corrected_weight', norms['weight'])
    center = int(round(corrected_weight * 25 / 50.0) * 50)
    if center > 3000:
        return 2400, 3000
    low = int(round(center * 0.9 / 50.0) * 50)
    high = min(int(round(center * 1.1 / 50.0) * 50), 3000)
    return low, high

def get_days_since_registration(user_id, utc_offset):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT created_at FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row['created_at']:
        return 0
    try:
        reg_date = datetime.strptime(row['created_at'], '%Y-%m-%d %H:%M:%S').date()
        now_utc = datetime.utcnow()
        user_now = (now_utc + timedelta(hours=utc_offset)).date()
        return (user_now - reg_date).days
    except ValueError:
        return 0

def get_unique_food_days(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(DISTINCT date) as days FROM food_log WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result['days'] if result else 0

def get_unique_food_days_for_week(user_id, days_passed):
    # Считаем только за текущую неделю (п.7)
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(DISTINCT date) as days FROM food_log WHERE user_id = ? AND date >= date("now", ?)', (user_id, f'-{days_passed} days'))
    result = cursor.fetchone()
    conn.close()
    return result['days'] if result else 0

def add_weight(user_id, date, weight):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO weight_log (user_id, date, weight) VALUES (?, ?, ?)', (user_id, date, weight))
    conn.commit()
    conn.close()

def get_calories_history(user_id, days=7):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT date, SUM(calories) as total_cal
        FROM food_log
        WHERE user_id = ? AND date >= date('now', ?)
        GROUP BY date ORDER BY date ASC
    ''', (user_id, f'-{days} days'))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]