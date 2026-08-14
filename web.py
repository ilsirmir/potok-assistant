"""Веб-интерфейс ассистента.

    uvicorn web:app --reload
    открыть http://127.0.0.1:8000

История диалога хранится в памяти процесса: прототип рассчитан на одного
пользователя. Перезапуск сервера очищает переписку.
"""

import datetime as dt
import sys
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import config
import documents
import sheets
import tools

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Ассистент внедренца")
lock = Lock()

# У каждого проекта своя переписка. Иначе разговоры о трёх заказчиках
# смешиваются в одной ленте, и модель начинает путать контексты.
# Ключ "" — общая лента для вопросов не про конкретный проект.
sessions = {}
active = {"project_id": None}


def current_history():
    return sessions.setdefault(active["project_id"] or "", [])


class Message(BaseModel):
    text: str


class Project(BaseModel):
    project_id: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/status")
def status():
    # Ошибку чтения таблицы не глушим: пустой список выглядит как «проектов
    # нет», хотя на деле это сбой доступа, и искать его потом дороже.
    result = {"model": config.LLM_MODEL, "active": active["project_id"],
              "demo": config.DEMO_MODE, "projects": []}
    try:
        today = dt.date.today()
        projects = []
        for project in sheets.get_projects():
            open_tasks = sheets.get_tasks(project["project_id"], only_open=True)
            overdue = [t for t in open_tasks
                       if sheets.parse_date(t["due"])
                       and sheets.parse_date(t["due"]) < today]
            projects.append({
                "id": project["project_id"],
                "client": project["client"],
                "stage": project["stage"],
                "open": len(open_tasks),
                "overdue": len(overdue),
            })
        result["projects"] = projects
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        print(f"Не удалось прочитать проекты: {e}", file=sys.stderr)
    return result


@app.get("/api/overview")
def overview():
    """Сводка по всем проектам для общей ленты."""
    today = dt.date.today()
    try:
        projects = sheets.get_projects()
        all_tasks = sheets.get_tasks()
    except Exception as e:
        # Без настроенных доступов интерфейс должен объяснить причину,
        # а не показать пустую страницу с ошибкой сервера.
        print(f"Не удалось прочитать реестр: {e}", file=sys.stderr)
        return {"error": f"{type(e).__name__}: {e}"}

    open_tasks = [t for t in all_tasks if t["status"] != "Выполнена"]
    overdue = [t for t in open_tasks
               if sheets.parse_date(t["due"]) and sheets.parse_date(t["due"]) < today]
    done = [t for t in all_tasks if t["status"] == "Выполнена"]

    names = {p["project_id"]: p["client"] for p in projects}
    upcoming = sorted(
        [t for t in open_tasks
         if sheets.parse_date(t["due"]) and sheets.parse_date(t["due"]) >= today],
        key=lambda t: sheets.parse_date(t["due"]))[:4]

    return {
        "projects": len(projects),
        "open": len(open_tasks),
        "overdue": len(overdue),
        "done": len(done),
        "total": len(all_tasks),
        "meetings": len(sheets.get_meetings()),
        "upcoming": [{
            "id": t["task_id"],
            "client": names.get(t["project_id"], ""),
            "task": t["task"],
            "due": sheets.format_date(t["due"]),
            "days": sheets.working_days_left(t["due"]),
        } for t in upcoming],
        "attention": [{
            "id": t["task_id"],
            "client": names.get(t["project_id"], ""),
            "task": t["task"],
            "due": sheets.format_date(t["due"]),
            "late": (today - sheets.parse_date(t["due"])).days,
            "owner": t["owner"],
        } for t in sorted(overdue, key=lambda t: sheets.parse_date(t["due"]))],
    }


@app.post("/api/project")
def set_project(choice: Project):
    """Смена активного проекта. Возвращает карточку для показа в чате.

    Карточка собирается кодом, а не моделью: цифры точные, ответ мгновенный
    и не тратит квоту. Модель подключается там, где нужна интерпретация.
    """
    active["project_id"] = choice.project_id or None
    if not active["project_id"]:
        return {"active": None, "card": None}

    try:
        project = next((p for p in sheets.get_projects()
                        if p["project_id"] == active["project_id"]), None)
    except Exception as e:
        print(f"Не удалось прочитать проект: {e}", file=sys.stderr)
        return {"active": None, "card": None, "error": f"{type(e).__name__}: {e}"}

    if not project:
        active["project_id"] = None
        return {"active": None, "card": None}

    pid = project["project_id"]
    open_tasks = sheets.get_tasks(pid, only_open=True)
    today = dt.date.today()
    overdue = [t for t in open_tasks
               if sheets.parse_date(t["due"]) and sheets.parse_date(t["due"]) < today]
    progress = sheets.project_progress(pid)

    # История: последняя встреча и что закрыто после неё. Даты закрытия
    # в реестре нет, поэтому ориентируемся на срок задачи — приблизительно,
    # но достаточно, чтобы показать движение.
    meetings = sheets.get_meetings(pid)
    last = meetings[-1] if meetings else None
    since = sheets.parse_date(last["date"]) if last else None
    closed_since = [
        t for t in sheets.get_tasks(pid)
        if t["status"] == "Выполнена"
        and (not since or (sheets.parse_date(t["due"]) or today) >= since)
    ]

    return {
        "active": pid,
        "card": {
            "meetings_count": len(meetings),
            "last_meeting": ({
                "date": sheets.format_date(last["date"]),
                "summary": last["summary"][:160],
            } if last else None),
            "closed_since": len(closed_since),
            "client": project["client"],
            "project": project["project"],
            "stage": project["stage"],
            "contact": project["client_contact"],
            "started": sheets.format_date(project.get("started")),
            "deadline": sheets.deadline_note(project.get("planned_finish")),
            "percent": progress["percent"],
            "done": progress["done"],
            "total": progress["total"],
            "open": len(open_tasks),
            "overdue": len(overdue),
        },
    }


@app.post("/api/reset")
def reset():
    """Очищает переписку текущей ленты, остальные не трогает."""
    current_history().clear()
    return {"ok": True}


def with_project(text):
    """Привязывает сообщение к выбранному проекту."""
    if not active["project_id"]:
        return text
    project = next((p for p in sheets.get_projects()
                    if p["project_id"] == active["project_id"]), None)
    if not project:
        return text
    return f"Речь о проекте «{project['client']}».\n\n{text}"


def run_turn(text):
    """Один ход диалога. Общий код для текста и для загруженного файла."""
    text = with_project(text)
    actions = []

    def record(name, args, result):
        actions.append({
            "name": name,
            "args": args,
            "result": result,
            "write": name in tools.WRITE_TOOLS,
            "failed": isinstance(result, dict) and "error" in result,
        })

    waits = []

    # Прототип держит одну общую историю на процесс. Блокировка не даёт
    # двум одновременным запросам перемешать переписку.
    with lock:
        history = current_history()
        history.append({"role": "user", "content": text})
        try:
            result = agent.run(history, on_tool_call=record,
                               on_wait=lambda s: waits.append(s))
        except Exception as e:
            history.pop()
            return {"error": f"{type(e).__name__}: {e}", "actions": actions}
        history[:] = result
        reply = agent.last_reply(history)

    return {
        "reply": reply,
        "actions": actions,
        "waited": round(sum(waits)) or None,
    }


@app.post("/api/chat")
def chat(message: Message):
    return run_turn(message.text)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), text: str = Form("")):
    """Загрузка документа: извлекаем текст и отправляем агенту как сообщение."""
    try:
        content = documents.as_message(file.filename, await file.read())
    except documents.UnsupportedFile as e:
        return {"error": f"Не смог прочитать «{file.filename}»: {e}",
                "actions": []}
    except Exception as e:
        return {"error": f"Ошибка при чтении файла: {e}", "actions": []}

    prompt = f"{text.strip()}\n\n{content}" if text.strip() else content
    result = run_turn(prompt)
    result["filename"] = file.filename
    return result


app.mount("/static", StaticFiles(directory=STATIC), name="static")
