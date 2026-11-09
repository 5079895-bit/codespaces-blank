import os
import json
import base64
import logging
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
logger = logging.getLogger(__name__)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

def _extract_json(content: str):
    if not content:
        return None
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(content[start:end + 1])
    except json.JSONDecodeError as e:
        logger.warning("Не удалось распарсить JSON от ИИ: %s | сырой ответ: %r", e, content[:200])
        return None

FOOD_RULES = """ВАЖНЫЕ ПРАВИЛА:
1. Если это чистая вода или чай без сахара — ставь 0 ккал.
2. Если это сок, газировка или сладкий напиток — считай калории и сахар.
3. ВАЖНО ПРО СУПЫ: Запомни среднюю калорийность супов на 100 г (не путай с сухим весом!):
   - Гороховый суп: ~65 ккал/100 г (на курином бульоне), если на свинине — до 90 ккал.
   - Борщ, щи: ~40-50 ккал/100 г (без сметаны).
   - Рассольник: ~50-60 ккал/100 г.
   - Куриный бульон: ~15-20 ккал/100 г.
   - Крем-суп (тыквенный, грибной): ~50-70 ккал/100 г.
   Если пользователь указывает просто "суп", бери среднее значение ~50 ккал/100 г. НЕ ЗАВЫШАЙ калорийность супов!
4. ВАЖНО ПРО САХАР: В поле "sugar" указывай ТОЛЬКО ДОБАВЛЕННЫЙ САХАР (в десертах, газировке, соусах). НЕ СЧИТАЙ природный сахар из фруктов, ягод, молока или овощей как вредный. Если сахар природный — ставь 0.
5. ВАЖНО ПРО ВЕС: Всегда указывай в поле "weight" вес порции в граммах. Если пользователь написал "суп 300", weight = 300. Если написал "яблоко", возьми средний вес (например, 200 г).
6. ЕСЛИ ТЫ СОМНЕВАЕШЬСЯ — спроси. Примеры сомнений:
   - "кола" — с сахаром или без (zero)? И сколько мл?
   - "чай" / "кофе" — с сахаром или без? С молоком? Какой объём?
   - "капучино" — на обычном молоке или обезжиренном? Какой объём?
   - "борщ" — со сметаной или без? Какой объём?
   - "салат" — с майонезом или без? Какой размер порции?
   - "пюре с котлетой" — котлета жареная или на пару? Какая примерно порция?
   
   ВАЖНО ПРО ФОТО И СЛОЖНЫЕ БЛЮДА: Если на фото несколько блюд или блюдо сложное, и ты не можешь точно определить состав — перечисли всё, что видишь, и задай ОДИН общий вопрос.
   Например: "Вижу суп, мясо и овощи. Уточни: что за мясо, какой суп и какие овощи?"
   
   ВАЖНО: Если пользователь НЕ указал объём или вес, ты ОБЯЗАН спросить про это в первую очередь! 
   Один вопрос должен содержать и уточнение состава, и уточнение объёма.
   
   Если сомневаешься — верни needs_clarification=true и задай ОДИН короткий вопрос в поле question. При этом calories и остальные поля НЕ заполняй (оставь null).
   НЕ спрашивай, если можешь разумно предположить (например, "200г творога" — всё ясно).
"""

def analyze_food(text):
    prompt = f"""Ты — нутрициолог. Пользователь написал: "{text}".
Твоя задача — оценить калории, белки, жиры, углеводы, добавленный сахар, вредные жиры и вес порции.

{FOOD_RULES}

Отвечай СТРОГО в формате JSON без лишних слов.
Если ты УВЕРЕН:
{{"dish": "яблоко", "weight": 200, "calories": 100, "protein": 0, "fat": 0, "carbs": 25, "sugar": 0, "bad_fat": 0, "needs_clarification": false, "question": null}}
Если ты СОМНЕВАЕШЬСЯ:
{{"dish": "кола", "needs_clarification": true, "question": "Кола с сахаром или без (zero)? И сколько мл?", "calories": null, "protein": null, "fat": null, "carbs": null, "sugar": null, "bad_fat": null, "weight": null}}"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content
        return _extract_json(content)
    except Exception as e:
        logger.exception("Ошибка ИИ (analyze_food): %s", e)
        return None

def recalculate_food(dish, weight_text):
    """Пересчитывает КБЖУ конкретного блюда на указанный вес."""
    prompt = f"""Ты — нутрициолог. Есть блюдо: "{dish}".
Пользователь уточняет порцию: "{weight_text}".
Пересчитай КБЖУ ИМЕННО ЭТОГО БЛЮДА с учётом указанного веса.
НЕ МЕНЯЙ название блюда. Если вес не указан явно — считай, что это граммы.
В поле "sugar" указывай ТОЛЬКО ДОБАВЛЕННЫЙ САХАР. Природный сахар (фрукты, молоко) — 0.
В поле "weight" укажи итоговый вес порции.
Отвечай СТРОГО в формате JSON без лишних слов.
Пример: {{"dish": "беляш", "weight": 150, "calories": 450, "protein": 15, "fat": 20, "carbs": 50, "sugar": 1, "bad_fat": 8, "needs_clarification": false, "question": null}}"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content
        return _extract_json(content)
    except Exception as e:
        logger.exception("Ошибка ИИ (recalculate_food): %s", e)
        return None

def analyze_photo(file_path):
    try:
        with open(file_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")

        response = client.chat.completions.create(
            model="deepseek-flash",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Опиши, что на этом фото. Перечисли все блюда, которые видишь, и примерный вес каждого. Ответь кратко, одним предложением."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_string}"}},
                    ],
                }
            ],
            temperature=0.1,
        )
        description = response.choices[0].message.content
        logger.info("DeepSeek увидел: %s", description)
        return description
    except Exception as e:
        logger.exception("Ошибка ИИ (analyze_photo): %s", e)
        return None

def ask_ai_question(question):
    prompt = f"""Ты — помощник по питанию и похудению в духе бережного отношения к еде (без стыда и запретов).
Пользователь спрашивает: "{question}".

ТВОИ ПРАВИЛА:
1. Отвечай ТОЛЬКО на вопросы про еду, калории, питание, похудение и пищевые привычки.
   Если вопрос не по теме (политика, погода, программирование и т.д.) — ответь ровно одну фразу:
   "Я помогаю только с питанием и движением. Давайте вернёмся к еде." и ничего больше.
2. Отвечай кратко, тепло и по делу. Без лекций и морализаторства.
3. Если пользователь спрашивает про продукты или понятия (например, "что такое плохой сахар",
   "почему грудка лучше свинины") — объясни простыми словами, с примером.
4. Если пользователь говорит, что ОЧЕНЬ ХОЧЕТ вредную еду (например, "очень хочу беляш",
   "хочу пиццу", "не могу удержаться от шоколадки") — НЕ запрещай и НЕ стыди. Ответь по шагам:
   а) спокойно скажи, что это можно;
   б) назови примерную калорийность и что там "тяжёлого" (жир, сахар, соль) — коротко;
   в) предложи съесть половину или четверть порции;
   г) предложи альтернативу по вкусу (например, домашний вариант или менее калорийный аналог);
   д) посоветуй есть медленно и осознанно, без телефона.
5. Если пользователь пишет "хочу съесть [что-то]" без явного вопроса — работай по правилу 4.
6. Не давай медицинских диагнозов. Если вопрос про болезнь — посоветуй обратиться к врачу.

Отвечай по-русски."""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.exception("Ошибка ИИ (ask_ai_question): %s", e)
        return None