from openai import AsyncOpenAI
"""Конфигурация проекта. Все настройки читаются из файла .env."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if getattr(sys, "frozen", False):          # запущено как .exe — файлы лежат рядом с .exe
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    """Безопасное чтение целого числа из окружения."""
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _set(name: str) -> set[str]:
    """Список через запятую -> множество (id или username без @, в нижнем регистре)."""
    raw = os.getenv(name, "")
    return {x.strip().lstrip("@").lower() for x in raw.split(",") if x.strip()}


# --- Telegram ---
API_ID = _int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "").strip()
PHONE = os.getenv("PHONE", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

# --- Автоответчик ---
DELAY_MIN = _int("REPLY_DELAY_MIN", 120)   # секунды (2 минуты)
DELAY_MAX = _int("REPLY_DELAY_MAX", 300)   # секунды (5 минут)
if DELAY_MAX < DELAY_MIN:
    DELAY_MIN, DELAY_MAX = DELAY_MAX, DELAY_MIN
MAX_REPLIES_PER_HOUR = _int("MAX_REPLIES_PER_HOUR", 20)
WHITELIST = _set("WHITELIST")   # если не пусто — отвечаем только этим людям
BLACKLIST = _set("BLACKLIST")   # никогда не отвечаем этим людям
DEFAULT_REPLY = os.getenv(
    "DEFAULT_REPLY", "Привет, {name}! Сейчас не могу ответить, отвечу позже."
)

# Задачи «нужно ответить»: сколько часов ждать ответа. Старше — закрываются как устаревшие
# (вам придёт уведомление в «Избранное»). 0 = не устаревают никогда.
TASK_MAX_AGE_HOURS = _int("TASK_MAX_AGE_HOURS", 48)
# При запуске бот просматривает непрочитанные личные чаты и берёт в работу
# сообщения, пришедшие, пока он был выключен (не старше стольки часов).
CATCHUP_HOURS = _int("CATCHUP_HOURS", 24)

# Шанс (в процентах), что ответ уйдёт реплаем (с цитатой) на сообщение собеседника.
# Если сообщений без ответа несколько, шанс не ниже 75%. Пересланное — всегда реплаем.
REPLY_QUOTE_CHANCE = max(0, min(100, _int("REPLY_QUOTE_CHANCE", 35)))

# Шаблон срабатывает, только если в сообщении не больше стольких слов сверх ключа.
# Длинные и «хитрые» вопросы уходят в ИИ. Большое число = шаблоны ловят почти всё.
TEMPLATE_EXTRA_WORDS = _int("TEMPLATE_EXTRA_WORDS", 4)

# Стиль ответов ИИ (применяется кодом, а не только промптом):
# писать с маленькой буквы (имена и аббревиатуры сохраняются) — 1/0
LOWERCASE_REPLIES = _int("LOWERCASE_REPLIES", 1)
# в скольких процентах ответов оставлять ОДНУ скобочку «)» (эмодзи убираются всегда)
SMILE_KEEP_CHANCE = max(0, min(100, _int("SMILE_KEEP_CHANCE", 20)))

# Рабочее время Юли (Пн-Пт, МСК): в это время ИИ знает, что она на работе
WORK_START = _int("WORK_START", 10)
WORK_END = _int("WORK_END", 20)

# Шанс (в процентах), что в ответе ИИ Юля вставит лёгкий мат. 0 = не материться никогда.
SWEAR_CHANCE = max(0, min(100, _int("SWEAR_CHANCE", 15)))

# --- Защита от бана ---
FLOOD_LIMIT_24H = 3  # столько FloodWait за 24 часа -> автостоп отправки

# --- AI (ZvenoAI) ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
# Ставим дефолтную модель ZvenoAI на случай, если в .env пусто
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "cohere/north-mini-code:free").strip()

# Промт Юли
AI_SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    "Ты — Юля, 20 лет. Работаешь в мебельном магазине, ходишь в зал. Рост 164 см. "
    "Сейчас ты в чате знакомств и общаешься с собеседником. "
    "Правила общения: Пишешь как обычный человек в мессенджере: без стикеров, без эмодзи (максимум один, и то редко). "
    "Отвечаешь коротко, не расписываешь абзацы. Одно-два предложения — норма. "
    "Не строишь из себя идеального собеседника: можешь отшутиться или задать встречный вопрос, но отвечай по-доброму, без сухости. "
    "Не используешь канцелярит и «ассистентский» тон. Никаких «Чем я могу вам помочь?». "
    "Про себя рассказываешь неохотно, по чуть-чуть, как будто тебя вытягивают на разговор. "
    "Если тема интересна — можешь разговориться, если нет — отвечаешь вяло. "
    "Ты ищешь знакомства, но не горишь: «дальше как пойдёт». "
    "Пишешь с большой буквы, иногда с опечатками, как в реальной переписке. "
    "Не задаёшь по три вопроса подряд. Максимум один. "
    "Если собеседник грубит или пошлит — не поучай и не злись, отшутись по-доброму или смени тему. "
    "Начни разговор так, как начала бы Юля: коротко, нейтрально, без энтузиазма."
)

# --- Инициализация клиента ZvenoAI ---
# ВАЖНО: Здесь изменен адрес на ZvenoAI и добавлен префикс /v1
ai_client = AsyncOpenAI(
    api_key=OPENAI_API_KEY or "no-key",  # пустой ключ не должен ронять запуск
    base_url="https://api.zveno.ai/v1"
)

# --- Пути ---
DB_PATH = BASE_DIR / "userbot.db"
LOG_FILE = BASE_DIR / "userbot.log"
TEMPLATES_FILE = BASE_DIR / "templates.txt"


def validate() -> list[str]:
    """Проверка обязательных настроек. Возвращает список ошибок."""
    errors = []
    if not API_ID:
        errors.append("В .env не задан API_ID (см. README, ШАГ 1).")
    if not API_HASH:
        errors.append("В .env не задан API_HASH (см. README, ШАГ 1).")
    if not SESSION_STRING:
        errors.append("В .env не задан SESSION_STRING (см. README, ШАГ 5).")
    return errors