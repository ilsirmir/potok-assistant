"""Проверка доступов перед запуском ассистента.

    python check_setup.py

Только читает: ничего в реестре не меняет.
"""

import datetime as dt
import sys

try:
    from openai import OpenAI
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    import config
except ImportError as e:
    print(f"Не хватает библиотеки: {e.name}")
    print("Выполните: pip install -r requirements.txt")
    sys.exit(1)

OK, FAIL = "  [ок]", "  [ошибка]"
results = []


def check(name, fn):
    print(f"\n{name}")
    try:
        print(f"{OK} {fn()}")
        results.append((name, True))
    except Exception as e:
        print(f"{FAIL} {e}")
        results.append((name, False))


def google_creds():
    import os
    if not os.path.exists(config.GOOGLE_CREDENTIALS):
        raise RuntimeError(
            f"файл {config.GOOGLE_CREDENTIALS} не найден рядом со скриптом")
    return service_account.Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS, scopes=config.SCOPES)


def check_env():
    required = ["LLM_API_KEY"] if config.DEMO_MODE else [
        "LLM_API_KEY", "SHEET_ID"]
    missing = [name for name in required if not getattr(config, name)]
    if missing:
        raise RuntimeError("в .env нет переменных: " + ", ".join(missing))
    if config.DEMO_MODE:
        return ("LLM_API_KEY на месте. Google не настроен — включён "
                "демо-режим на локальном файле")
    return "переменные на месте"


def check_llm():
    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": "Ответь одним словом: работает"}],
    )
    reply = (response.choices[0].message.content or "").strip()
    return f"модель {config.LLM_MODEL} отвечает: {reply[:40]}"


def check_sheets():
    service = build("sheets", "v4", credentials=google_creds(),
                    cache_discovery=False)
    meta = service.spreadsheets().get(spreadsheetId=config.SHEET_ID).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    for need in ("projects", "meetings", "tasks"):
        if need not in titles:
            raise RuntimeError(f"нет листа '{need}'. Найдены: {titles}")
    rows = service.spreadsheets().values().get(
        spreadsheetId=config.SHEET_ID,
        range="tasks!A2:H").execute().get("values", [])
    return f"таблица '{meta['properties']['title']}', задач в реестре: {len(rows)}"


check("1. Файл .env", check_env)
check("2. Модель", check_llm)
if config.DEMO_MODE:
    print("\n3. Реестр")
    print(f"{OK} демо-режим: локальный файл, Google не требуется")
    results.append(("3. Реестр", True))
else:
    check("3. Google Sheets", check_sheets)

failed = [name for name, ok in results if not ok]
print("\n" + "-" * 50)
if failed:
    print("Не прошли проверку:")
    for name in failed:
        print(f"  - {name}")
    sys.exit(1)
print("Все доступы работают. Запускайте: uvicorn web:app --reload")
if config.DEMO_MODE:
    print("Реестр — локальный файл demo_workspace.xlsx. Чтобы подключить "
          "Google Таблицу, заполните SHEET_ID и credentials.json.")
