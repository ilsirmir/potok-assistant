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

# Локальные серверы моделей, такие как Ollama, LM Studio и vLLM, ключ не
# проверяют, но клиентская библиотека без него не стартует. Подставляем
# заглушку, чтобы локальный запуск не требовал выдумывать ключ.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")
LLM_IS_LOCAL = any(host in LLM_BASE_URL for host in LOCAL_HOSTS)
if LLM_IS_LOCAL and not LLM_API_KEY:
    LLM_API_KEY = "local"

# --- Хранилище --------------------------------------------------------------
# Демо-режим: реестр читается и пишется в локальный файл вместо Google
# Таблицы. Нужен, чтобы проект можно было запустить, имея только ключ
# модели, — настройка сервисного аккаунта занимает полчаса и отпугивает.
# Включается сам, если доступов к Google нет.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_SOURCE = os.path.join(BASE_DIR, "potok_demo_data.xlsx")
DEMO_FILE = os.path.join(BASE_DIR, "demo_workspace.xlsx")

# --- Google ----------------------------------------------------------------
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "credentials.json")
SHEET_ID = os.getenv("SHEET_ID")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# --- Прочее ----------------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")


def _resolve_demo():
    """Демо, если явно попросили или если Google не настроен."""
    flag = os.getenv("DEMO_MODE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    credentials = GOOGLE_CREDENTIALS
    if not os.path.isabs(credentials):
        credentials = os.path.join(BASE_DIR, credentials)
    return not (os.path.exists(credentials) and SHEET_ID)


DEMO_MODE = _resolve_demo()


def require(*names):
    """Падаем с понятным сообщением, а не с AttributeError где-то в глубине."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(
            "В .env не хватает переменных: " + ", ".join(missing)
        )
