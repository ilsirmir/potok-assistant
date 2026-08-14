"""Чтение и запись реестра проектов в Google Sheets.

Таблица играет роль базы: три листа — projects, meetings, tasks.
Здесь только доступ к данным, никакой логики ассистента.
"""

import datetime as dt
import re
import time
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config

PROJECTS_COLUMNS = ["project_id", "client", "project", "stage",
                    "manager", "client_contact", "started"]
MEETINGS_COLUMNS = ["meeting_id", "date", "project_id", "participants",
                    "summary", "agreements"]
TASKS_COLUMNS = ["task_id", "project_id", "task", "owner",
                 "due", "status", "created", "source_meeting"]

# Колонки листа tasks в терминах таблицы — нужны для точечного обновления.
TASK_CELL = {"due": "E", "status": "F"}

CACHE_TTL = 30
_cache = {}


# --- Работа с датами -------------------------------------------------------

def parse_date(value):
    """Дата из ячейки. Понимает 2026-08-14 и 14.08.2026, иначе None.

    Реестр редактируют руками, и формат в ячейке гарантировать нельзя.
    Всё, что не разобралось, считается задачей без срока — это честнее,
    чем молча признать её просроченной.
    """
    value = (value or "").strip()
    if not value:
        return None
    for pattern, order in ((r"^(\d{4})-(\d{2})-(\d{2})$", (0, 1, 2)),
                           (r"^(\d{2})\.(\d{2})\.(\d{4})$", (2, 1, 0))):
        match = re.match(pattern, value)
        if match:
            parts = match.groups()
            try:
                return dt.date(int(parts[order[0]]), int(parts[order[1]]),
                               int(parts[order[2]]))
            except ValueError:
                return None
    return None


def is_valid_date(value):
    return parse_date(value) is not None


def format_date(value):
    """Дата для показа человеку: ДД.ММ.ГГГГ.

    В таблице и в аргументах инструментов даты живут в ISO — так их удобно
    сравнивать и сортировать. Человеку показывается только этот формат,
    чтобы в одном сообщении не встречались два вида записи.
    """
    parsed = parse_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else (value or "")


# --- Доступ к таблице ------------------------------------------------------

@lru_cache(maxsize=1)
def _service():
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS, scopes=config.SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _read(sheet_name, columns):
    """Список словарей. Добавляет _row — номер строки в таблице.

    Результат кэшируется на CACHE_TTL секунд: один вопрос про проект
    иначе означает четыре обращения к API и заметную паузу.
    """
    now = time.monotonic()
    cached = _cache.get(sheet_name)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    last_column = chr(ord("A") + len(columns) - 1)
    values = _service().spreadsheets().values().get(
        spreadsheetId=config.SHEET_ID,
        range=f"{sheet_name}!A2:{last_column}",
    ).execute().get("values", [])

    rows = []
    for offset, row in enumerate(values):
        if not any(cell.strip() for cell in row):
            continue
        padded = row + [""] * (len(columns) - len(row))
        record = dict(zip(columns, padded))
        record["_row"] = offset + 2
        rows.append(record)

    _cache[sheet_name] = (now, rows)
    return rows


def _invalidate(sheet_name=None):
    if sheet_name:
        _cache.pop(sheet_name, None)
    else:
        _cache.clear()


def get_projects():
    return _read("projects", PROJECTS_COLUMNS)


def get_meetings(project_id=None):
    rows = _read("meetings", MEETINGS_COLUMNS)
    if project_id:
        rows = [r for r in rows if r["project_id"] == project_id]
    return sorted(rows, key=lambda r: r["date"])


def get_tasks(project_id=None, only_open=False):
    rows = _read("tasks", TASKS_COLUMNS)
    if project_id:
        rows = [r for r in rows if r["project_id"] == project_id]
    if only_open:
        rows = [r for r in rows if r["status"] != "Выполнена"]
    # Задачи без срока — в конец, а не в начало.
    return sorted(rows, key=lambda r: (parse_date(r["due"]) or dt.date.max))


# --- Поиск -----------------------------------------------------------------

def _normalize(text):
    return (text or "").lower().replace("ё", "е")


def _words(text):
    return [w for w in re.split(r"[^\w]+", _normalize(text)) if len(w) > 3]


def find_project(hint):
    """Поиск проекта по обрывку названия клиента или проекта.

    Заказчика называют как придётся: «Ромашка», «с Ромашкой», «ромашка групп».
    Русские склонения ломают поиск по вхождению подстроки, поэтому
    сравниваем слова по общему началу.
    """
    hint_norm = _normalize(hint).strip()
    if not hint_norm:
        return None

    projects = get_projects()
    for p in projects:
        if hint_norm in (_normalize(p["project_id"]), _normalize(p["client"])):
            return p

    hint_words = _words(hint_norm)
    for p in projects:
        haystack = _normalize(f"{p['client']} {p['project']}")
        if hint_norm in haystack:
            return p
        for hw in hint_words:
            for word in _words(haystack):
                size = min(len(hw), len(word), 6)
                if size >= 4 and hw[:size] == word[:size]:
                    return p
    return None


def similar_open_task(project_id, text):
    """Похожая открытая задача или None.

    Страховка от дублей на случай, если модель проигнорирует правило
    в промпте: сравниваем по доле общих значимых слов.
    """
    new_words = set(_words(text))
    if not new_words:
        return None

    best, best_score = None, 0.0
    for task in get_tasks(project_id, only_open=True):
        words = set(_words(task["task"]))
        if not words:
            continue
        score = len(new_words & words) / len(new_words | words)
        if score > best_score:
            best, best_score = task, score
    return best if best_score >= 0.5 else None


# --- Запись ----------------------------------------------------------------

def _next_task_id():
    numbers = [int(t["task_id"].split("-")[1])
               for t in _read("tasks", TASKS_COLUMNS)
               if re.match(r"^T-\d+$", t["task_id"])]
    return max(numbers, default=0) + 1


def append_tasks(rows):
    """rows — список словарей с ключами из TASKS_COLUMNS без task_id.

    Возвращает список присвоенных идентификаторов.
    """
    next_num = _next_task_id()
    values, ids = [], []

    for i, row in enumerate(rows):
        task_id = f"T-{next_num + i:03d}"
        ids.append(task_id)
        record = dict(row, task_id=task_id)
        values.append([record.get(col, "") for col in TASKS_COLUMNS])

    _service().spreadsheets().values().append(
        spreadsheetId=config.SHEET_ID,
        range="tasks!A:H",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()

    _invalidate("tasks")
    return ids


def find_task(task_id):
    task_id = (task_id or "").strip().upper()
    for task in _read("tasks", TASKS_COLUMNS):
        if task["task_id"].upper() == task_id:
            return task
    return None


def update_task(task_id, **fields):
    """Меняет срок и/или статус существующей задачи.

    Возвращает обновлённую задачу или None, если такой задачи нет.
    """
    task = find_task(task_id)
    if not task:
        return None

    data = [
        {"range": f"tasks!{TASK_CELL[name]}{task['_row']}", "values": [[value]]}
        for name, value in fields.items()
        if name in TASK_CELL and value is not None
    ]
    if not data:
        return task

    _service().spreadsheets().values().batchUpdate(
        spreadsheetId=config.SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()

    _invalidate("tasks")
    return find_task(task_id)
