"""Инструменты агента: описания для модели и их реализация.

Два инструмента на чтение и три на запись. Записывающие проверяют входные
данные и возвращают человекочитаемый результат, чтобы модель могла честно
отчитаться, что именно сделано.
"""

import datetime as dt

import calendar_api
import sheets


def _today():
    return dt.date.today()


def _clean(task):
    """Служебные поля наружу не отдаём — они только путают модель."""
    return {k: v for k, v in task.items() if not k.startswith("_")}


# --- Чтение ----------------------------------------------------------------

def get_project_context(client):
    """Всё, что известно по проекту: карточка, встречи, открытые задачи."""
    project = sheets.find_project(client)
    if not project:
        known = ", ".join(p["client"] for p in sheets.get_projects())
        return {"error": f"Проект не найден. Известные клиенты: {known}"}

    pid = project["project_id"]
    open_tasks = sheets.get_tasks(pid, only_open=True)

    return {
        "project": _clean(project),
        "meetings": [_clean(m) for m in sheets.get_meetings(pid)[-4:]],
        "open_tasks": [_clean(t) for t in open_tasks],
        "closed_count": len(sheets.get_tasks(pid)) - len(open_tasks),
    }


def list_overdue_tasks(client=None):
    """Просроченные и ближайшие задачи. Без клиента — по всем проектам."""
    project_id = None
    if client:
        project = sheets.find_project(client)
        if not project:
            return {"error": f"Проект '{client}' не найден"}
        project_id = project["project_id"]

    names = {p["project_id"]: p["client"] for p in sheets.get_projects()}
    today = _today()
    overdue, upcoming, undated = [], [], []

    for task in sheets.get_tasks(project_id, only_open=True):
        item = dict(_clean(task), client=names.get(task["project_id"], ""))
        due = sheets.parse_date(task["due"])
        if due is None:
            undated.append(item)
        elif due < today:
            overdue.append(dict(item, days_late=(today - due).days))
        else:
            upcoming.append(item)

    return {
        "today": today.isoformat(),
        "overdue": overdue,
        "upcoming": upcoming[:5],
        "without_due": undated,
    }


# --- Запись ----------------------------------------------------------------

def add_tasks(client, tasks):
    """Добавляет задачи в реестр. Вызывать только после подтверждения."""
    project = sheets.find_project(client)
    if not project:
        return {"error": f"Проект '{client}' не найден"}

    bad_dates = [t["task"] for t in tasks if not sheets.is_valid_date(t.get("due"))]
    if bad_dates:
        return {"error": "Срок должен быть в формате ГГГГ-ММ-ДД. "
                         "Некорректно у задач: " + "; ".join(bad_dates)}

    created_on = _today().isoformat()
    rows, skipped = [], []

    for task in tasks:
        existing = sheets.similar_open_task(project["project_id"], task["task"])
        if existing:
            skipped.append({
                "proposed": task["task"],
                "existing_id": existing["task_id"],
                "existing_task": existing["task"],
                "existing_owner": existing["owner"],
                "existing_due": existing["due"],
            })
            continue
        rows.append({
            "project_id": project["project_id"],
            "task": task["task"],
            "owner": task.get("owner", ""),
            "due": task["due"],
            "status": "Новая",
            "created": created_on,
            "source_meeting": task.get("source_meeting", ""),
        })

    ids = sheets.append_tasks(rows) if rows else []
    result = {"created": ids, "project": project["client"], "count": len(ids)}
    if skipped:
        # Модель обязана рассказать об этом человеку, а не проглотить.
        result["skipped_as_duplicates"] = skipped
    return result


def update_task(task_id, due=None, status=None):
    """Меняет срок или статус существующей задачи."""
    if due is not None and not sheets.is_valid_date(due):
        return {"error": "Срок должен быть в формате ГГГГ-ММ-ДД"}
    allowed = ("Новая", "В работе", "Выполнена")
    if status is not None and status not in allowed:
        return {"error": "Статус должен быть одним из: " + ", ".join(allowed)}

    task = sheets.update_task(task_id, due=due, status=status)
    if not task:
        return {"error": f"Задача {task_id} не найдена"}
    return {"updated": task["task_id"], "task": task["task"],
            "due": task["due"], "status": task["status"]}


def create_deadline(title, date, description="", time=None):
    """Ставит событие в календарь. Вызывать только после подтверждения."""
    if not sheets.is_valid_date(date):
        return {"error": "Дата должна быть в формате ГГГГ-ММ-ДД"}

    event = calendar_api.create_event(
        title=title, date=date, description=description, time=time)
    return {"created": title, "date": date, "link": event["link"]}


# --- Описания для модели ---------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_project_context",
            "description": (
                "Поднять всю историю по проекту: карточку, последние встречи "
                "с договорённостями и открытые задачи. Использовать перед "
                "подготовкой к встрече и всегда, когда пользователь упоминает "
                "клиента по названию."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client": {
                        "type": "string",
                        "description": "Название клиента или проекта, как назвал пользователь",
                    }
                },
                "required": ["client"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_overdue_tasks",
            "description": (
                "Просроченные и ближайшие задачи. Без указания клиента — "
                "сводка по всем проектам. Использовать для вопросов вида "
                "«что горит», «что просрочено», «что у меня на неделе». "
                "Отдельно возвращает задачи без срока — их нельзя считать "
                "просроченными."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client": {
                        "type": "string",
                        "description": "Необязательно. Ограничить одним клиентом",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_tasks",
            "description": (
                "Записать НОВЫЕ задачи в реестр проекта. Вызывать только "
                "после явного подтверждения человеком. Сначала показать "
                "формулировки текстом и дождаться согласия. "
                "Если задача уже есть в реестре, инструмент её пропустит и "
                "вернёт в поле skipped_as_duplicates — обязательно сообщи "
                "об этом человеку с номером существующей задачи."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "description": "Клиент или проект"},
                    "tasks": {
                        "type": "array",
                        "description": "Список задач",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {
                                    "type": "string",
                                    "description": "Формулировка задачи, глагол в начале",
                                },
                                "owner": {
                                    "type": "string",
                                    "description": (
                                        "Ответственный. Для задач на стороне клиента "
                                        "добавить пометку '(заказчик)'"
                                    ),
                                },
                                "due": {
                                    "type": "string",
                                    "description": "Срок строго в формате ГГГГ-ММ-ДД",
                                },
                            },
                            "required": ["task", "owner", "due"],
                        },
                    },
                },
                "required": ["client", "tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Изменить срок или статус СУЩЕСТВУЮЩЕЙ задачи по её номеру. "
                "Использовать, когда договорённость уже есть в реестре, но "
                "изменились сроки, или когда задачу пора закрыть. "
                "Вызывать только после подтверждения человеком."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Номер задачи, например T-004"},
                    "due": {"type": "string", "description": "Новый срок ГГГГ-ММ-ДД"},
                    "status": {
                        "type": "string",
                        "enum": ["Новая", "В работе", "Выполнена"],
                        "description": "Новый статус",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_deadline",
            "description": (
                "Поставить событие в рабочий календарь: дедлайн или встречу. "
                "Вызывать только после подтверждения пользователем."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название события"},
                    "date": {"type": "string", "description": "Дата ГГГГ-ММ-ДД"},
                    "time": {
                        "type": "string",
                        "description": "Необязательно. Время ЧЧ:ММ. Без него — событие на весь день",
                    },
                    "description": {"type": "string", "description": "Необязательно. Детали"},
                },
                "required": ["title", "date"],
            },
        },
    },
]

HANDLERS = {
    "get_project_context": get_project_context,
    "list_overdue_tasks": list_overdue_tasks,
    "add_tasks": add_tasks,
    "update_task": update_task,
    "create_deadline": create_deadline,
}

WRITE_TOOLS = {"add_tasks", "update_task", "create_deadline"}
