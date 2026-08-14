"""Проверка доступов перед запуском ассистента.

    python check_setup.py

Ничего не меняет необратимо: в календаре создаётся тестовое событие
и тут же удаляется, в таблицу запись не производится.
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
    missing = [name for name in ("LLM_API_KEY", "SHEET_ID", "CALENDAR_ID")
               if not getattr(config, name)]
    if missing:
        raise RuntimeError("в .env нет переменных: " + ", ".join(missing))
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


def check_calendar():
    service = build("calendar", "v3", credentials=google_creds(),
                    cache_discovery=False)
    info = service.calendars().get(calendarId=config.CALENDAR_ID).execute()

    start = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    event = service.events().insert(calendarId=config.CALENDAR_ID, body={
        "summary": "Тестовое событие (будет удалено)",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + dt.timedelta(hours=1)).isoformat()},
    }).execute()
    service.events().delete(calendarId=config.CALENDAR_ID,
                            eventId=event["id"]).execute()
    return f"календарь '{info['summary']}', запись и удаление работают"


check("1. Файл .env", check_env)
check("2. Модель", check_llm)
check("3. Google Sheets", check_sheets)
check("4. Google Calendar", check_calendar)

failed = [name for name, ok in results if not ok]
print("\n" + "-" * 50)
if failed:
    print("Не прошли проверку:")
    for name in failed:
        print(f"  - {name}")
    sys.exit(1)
print("Все доступы работают. Запускайте: uvicorn web:app --reload")
