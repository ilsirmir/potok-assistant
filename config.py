"""Конфигурация. Всё, что зависит от окружения, читается здесь и нигде больше."""

import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM -------------------------------------------------------------------
# Провайдер меняется через .env без правки кода: оба говорят в формате OpenAI.
#
#   Gemini:    LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
#              LLM_MODEL=gemini-3.6-flash
#              LLM_API_KEY=<ключ из AI Studio>
#
# Названия моделей Google меняет часто, а список /models отдаёт в том числе
# те, что закрыты для новых аккаунтов. Если приходит 404 — пробуйте
# следующую по списку: gemini-3.6-flash, gemini-3.7-flash, gemini-3.5-flash-lite.
#
#   GigaChat:  LLM_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
#              LLM_MODEL=GigaChat-2
#              LLM_API_KEY=<access token>

LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
)
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")

# --- Google ----------------------------------------------------------------
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "credentials.json")
SHEET_ID = os.getenv("SHEET_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
]

# --- Прочее ----------------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

# Часовой пояс для событий календаря.
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")


def require(*names):
    """Падаем с понятным сообщением, а не с AttributeError где-то в глубине."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(
            "В .env не хватает переменных: " + ", ".join(missing)
        )
