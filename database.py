import sqlite3
import logging
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_database.db')

def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

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
            last_norms_update TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS food_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT DEFAULT CURRENT_DATE,
            dish TEXT,
            calories INTEGER,
            protein REAL,
            fat REAL,
            carbs REAL,
            sugar REAL,
            bad_fat REAL,
            weight REAL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS water_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT DEFAULT CURRENT_DATE,
            ml INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weight_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT DEFAULT CURRENT_DATE,
            weight REAL
        )
    ''')

    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]

    if "age_attempts" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN age_attempts INTEGER DEFAULT 0")
    if "created_at" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
        cursor.execute("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    if "week" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN week INTEGER DEFAULT 1")
    if "last_norms_update" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_norms_update TEXT")
        cursor.execute("UPDATE users SET last_norms_update = CURRENT_TIMESTAMP WHERE last_norms_update IS NULL")

    cursor.execute("PRAGMA table_info(food_log)")
    food_columns = [row[1] for row in cursor.fetchall()]
    if "weight" not in food_columns:
        cursor.execute("ALTER TABLE food_log ADD COLUMN weight REAL")

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
        return user_dict
    return None

def save_answer(user_id, field, value):
    allowed = {"name", "gender", "age", "height", "weight", "lifestyle", "dislikes", "restrictions", "age_attempts", "week", "last_norms_update"}
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

def add_food(user_id, dish, calories, protein, fat, carbs, sugar, bad_fat, weight):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO food_log (user_id, dish, calories, protein, fat, carbs, sugar, bad_fat, weight)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, dish, calories, protein, fat, carbs, sugar, bad_fat, weight))
    conn.commit()
    conn.close()

def get_day_summary(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT SUM(calories) as cal, SUM(protein) as prot, SUM(fat) as fat,
               SUM(carbs) as carb, SUM(sugar) as sug, SUM(bad_fat) as bad_fat
        FROM food_log WHERE user_id = ? AND date = CURRENT_DATE
    ''', (user_id,))
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
    }

def update_last_food(user_id, new_dish, calories, protein, fat, carbs, sugar, bad_fat, weight):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM food_log WHERE user_id = ? AND date = CURRENT_DATE ORDER BY id DESC LIMIT 1', (user_id,))
    row = cursor.fetchone()
    if row:
        last_id = row['id']
        cursor.execute('''
            UPDATE food_log SET dish = ?, calories = ?, protein = ?, fat = ?, carbs = ?, sugar = ?, bad_fat = ?, weight = ?
            WHERE id = ?
        ''', (new_dish, calories, protein, fat, carbs, sugar, bad_fat, weight, last_id))
        conn.commit()
    conn.close()

def get_last_food(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM food_log WHERE user_id = ? AND date = CURRENT_DATE ORDER BY id DESC LIMIT 1', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_last_dish(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT dish FROM food_log WHERE user_id = ? AND date = CURRENT_DATE ORDER BY id DESC LIMIT 1', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row['dish'] if row else None

def add_water(user_id, ml):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO water_log (user_id, ml) VALUES (?, ?)', (user_id, int(ml)))
    conn.commit()
    conn.close()

def get_day_water(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(ml) as total FROM water_log WHERE user_id = ? AND date = CURRENT_DATE', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result['total'] if result and result['total'] else 0

def get_water_range(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT weight FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row['weight']:
        center = int(round(float(row['weight']) * 25 / 50.0) * 50)
        if center > 3000:
            return 2400, 3000
        low = int(round(center * 0.9 / 50.0) * 50)
        high = min(int(round(center * 1.1 / 50.0) * 50), 3000)
        return low, high
    return 2000, 2500

def get_days_since_registration(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT created_at FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row['created_at']:
        return 0
    try:
        reg_date = datetime.strptime(row['created_at'], '%Y-%m-%d %H:%M:%S').date()
        return (datetime.now().date() - reg_date).days
    except ValueError:
        return 0

def get_unique_food_days(user_id):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(DISTINCT date) as days FROM food_log WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result['days'] if result else 0

def add_weight(user_id, weight):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO weight_log (user_id, weight) VALUES (?, ?)', (user_id, weight))
    conn.commit()
    conn.close()

def get_weight_history(user_id, limit=10):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT date, weight FROM weight_log WHERE user_id = ? ORDER BY id DESC LIMIT ?', (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows][::-1]

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