"""Инструменты агента: описания для модели и их реализация.

Три инструмента на чтение и два на запись. Записывающие проверяют входные
данные и возвращают человекочитаемый результат, чтобы модель могла честно
отчитаться, что именно сделано.
"""

import datetime as dt

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
    today = _today()
    all_tasks = sheets.get_tasks(pid)
    open_tasks = [t for t in all_tasks if t["status"] != "Выполнена"]

    # Просрочку считаем здесь и кладём прямо в задачу: модель хуже
    # сравнивает даты, чем код, а дублировать список просроченных отдельно
    # значит удвоить контекст и дать повод запутаться.
    open_with_late = []
    for task in open_tasks:
        due = sheets.parse_date(task["due"])
        item = _clean(task)
        if due and due < today:
            item["days_late"] = (today - due).days
        open_with_late.append(item)

    # Закрытые задачи нужны, чтобы показать, что сделано с прошлой встречи.
    meetings = sheets.get_meetings(pid)
    last_meeting = sheets.parse_date(meetings[-1]["date"]) if meetings else None
    closed = [
        _clean(t) for t in all_tasks
        if t["status"] == "Выполнена"
        and (not last_meeting or (sheets.parse_date(t["due"]) or today) >= last_meeting)
    ]

    return {
        "project": _clean(project),
        "closed_since_last_meeting": closed,
        "progress": dict(
            sheets.project_progress(pid),
            planned_finish=project.get("planned_finish", ""),
            deadline_note=sheets.deadline_note(project.get("planned_finish")),
            working_days_left=sheets.working_days_left(project.get("planned_finish")),
        ),
        "meetings": [_clean(m) for m in meetings[-4:]],
        "open_tasks": open_with_late,
        "closed_count": len(all_tasks) - len(open_tasks),
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

def find_similar_tasks():
    """Похожие открытые задачи у разных заказчиков."""
    groups = sheets.similar_task_groups()
    if not groups:
        return {"groups": [], "note": "Похожих задач между проектами не найдено"}

    # Просрочку считаем здесь: по ней модель определяет, с какой темы
    # выгоднее начать.
    today = _today()
    enriched = []
    for group in groups:
        tasks = []
        for task in group:
            due = sheets.parse_date(task["due"])
            tasks.append(dict(
                task,
                days_late=(today - due).days if due and due < today else 0,
            ))
        enriched.append({
            "tasks": tasks,
            "overdue_count": len([t for t in tasks if t["days_late"]]),
        })

    return {"groups": enriched, "count": len(enriched)}


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
            "name": "find_similar_tasks",
            "description": (
                "Найти похожие открытые задачи у РАЗНЫХ заказчиков. "
                "Использовать для вопросов вида «что повторяется между "
                "проектами», «где можно сделать шаблон». Возвращает группы "
                "задач, сформулированных близко по смыслу."
            ),
            "parameters": {"type": "object", "properties": {}},
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
]

HANDLERS = {
    "get_project_context": get_project_context,
    "list_overdue_tasks": list_overdue_tasks,
    "find_similar_tasks": find_similar_tasks,
    "add_tasks": add_tasks,
    "update_task": update_task,
}

WRITE_TOOLS = {"add_tasks", "update_task"}
