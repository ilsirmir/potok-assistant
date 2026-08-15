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
        return {"error": f"Проект '{client}' не найден. В реестре есть только "
                         f"эти клиенты: {known}. Повтори вызов с одним из них "
                         f"или скажи человеку, что такого проекта нет."}

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
            known = ", ".join(p["client"] for p in sheets.get_projects())
            return {"error": f"Проект '{client}' не найден. В реестре есть "
                             f"только эти клиенты: {known}."}
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

    bad_dates = [f"«{t['task']}» со сроком «{t.get('due') or 'пусто'}»"
                 for t in tasks if not sheets.is_valid_date(t.get("due"))]
    if bad_dates:
        return {"error": "Задачи не записаны. Срок нужен в формате "
                         "ГГГГ-ММ-ДД, например 2026-08-17. Исправь и повтори "
                         "вызов. Неверно у задач: " + "; ".join(bad_dates)}

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
        return {"error": f"Задача не изменена. Срок «{due}» не подходит, "
                         f"нужен формат ГГГГ-ММ-ДД, например 2026-08-17. "
                         f"Исправь и повтори вызов."}
    allowed = ("Новая", "В работе", "Выполнена")
    if status is not None and status not in allowed:
        return {"error": f"Задача не изменена. Статус «{status}» недопустим, "
                         f"выбери один из этих: {', '.join(allowed)}."}

    task = sheets.update_task(task_id, due=due, status=status)
    if not task:
        return {"error": f"Задачи {task_id} нет в реестре. Вызови "
                         f"get_project_context и возьми настоящий номер, "
                         f"а если подходящей задачи нет, скажи об этом "
                         f"человеку вместо того, чтобы угадывать."}
    return {"updated": task["task_id"], "task": task["task"],
            "due": task["due"], "status": task["status"]}


# --- Описания для модели ---------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_project_context",
            "description": (
                "Поднять историю проекта. Возвращает карточку, прогресс, "
                "последние встречи с договорённостями, открытые задачи с "
                "пометкой days_late у просроченных и задачи, закрытые после "
                "прошлой встречи.\n"
                "Вызывать: перед подготовкой к встрече, при упоминании "
                "клиента по названию, и ОБЯЗАТЕЛЬНО перед разбором заметок, "
                "чтобы сверить их с тем, что уже в реестре.\n"
                "Не вызывать: для сводки по всем проектам сразу, для этого "
                "есть list_overdue_tasks без аргументов."
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
                "Просроченные и ближайшие задачи по всем проектам или по "
                "одному. Возвращает три отдельных списка: overdue с полем "
                "days_late, upcoming и without_due. Задачи без срока НЕ "
                "являются просроченными, не смешивай их.\n"
                "Вызывать: на вопросы «что горит», «что просрочено», «что у "
                "меня на неделе».\n"
                "Не вызывать: когда нужна история проекта или договорённости "
                "встреч, для этого есть get_project_context."
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
                "Возвращает группы задач, близких по смыслу, с полем "
                "overdue_count у каждой группы. По нему определяй, с какой "
                "темы выгоднее начать.\n"
                "Вызывать: на вопросы «что повторяется между проектами», "
                "«где можно сделать шаблон».\n"
                "Не вызывать: для поиска дублей внутри одного проекта, это "
                "делает add_tasks автоматически."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_tasks",
            "description": (
                "Записать НОВЫЕ задачи в реестр проекта.\n"
                "Вызывать: только после того, как человек явно согласился со "
                "списком, который ты ему показал. Слова «да», «записывай», "
                "«подтверждаю» это согласие.\n"
                "Не вызывать: пока согласия нет; для задачи, которая уже "
                "есть в реестре, ей меняют срок через update_task; для "
                "работы, которая по заметкам уже выполнена.\n"
                "Похожие задачи инструмент отсеет сам и вернёт в поле "
                "skipped_as_duplicates. Обязательно скажи об этом человеку "
                "и назови номер существующей задачи."
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
                                    "description": (
                                        "Срок строго в формате ГГГГ-ММ-ДД, "
                                        "например 2026-08-17. Словесные "
                                        "формулировки вроде «в понедельник» "
                                        "переводи в дату сам, ориентиры дат "
                                        "есть в системном сообщении"
                                    ),
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
                "Изменить срок или статус существующей задачи по её номеру. "
                "Вызывать только после подтверждения человеком.\n"
                "Менять срок: когда в заметках названа конкретная новая дата "
                "или когда встреча по этой теме назначена на другой день.\n"
                "Ставить статус «Выполнена»: только если в заметках работа "
                "описана в ПРОШЕДШЕМ времени — «закончил», «отправил», "
                "«согласовали», «вопрос снят».\n"
                "Не вызывать: если новая дата не названа, вместо этого "
                "спроси у человека, на какое число сдвинуть; если работа "
                "названа в будущем времени — «доделаю», «пришлю», "
                "«подготовлю», «до конца недели», такая задача не выполнена "
                "и закрывать её нельзя."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string",
                                "description": "Номер задачи, например T-004"},
                    "due": {"type": "string",
                            "description": ("Новый срок в формате ГГГГ-ММ-ДД. "
                                            "Указывай только если дата названа "
                                            "в заметках")},
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
