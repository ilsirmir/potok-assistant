"""Веб-интерфейс ассистента.

    uvicorn web:app --reload
    открыть http://127.0.0.1:8000

История диалога хранится в памяти процесса: прототип рассчитан на одного
пользователя. Перезапуск сервера очищает переписку.
"""

from pathlib import Path
from threading import Lock

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import config
import documents
import tools

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Ассистент внедренца")
history = []
lock = Lock()


class Message(BaseModel):
    text: str


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/status")
def status():
    return {"model": config.LLM_MODEL}


@app.post("/api/reset")
def reset():
    history.clear()
    return {"ok": True}


def run_turn(text):
    """Один ход диалога. Общий код для текста и для загруженного файла."""
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
        history.append({"role": "user", "content": text})
        try:
            result = agent.run(history, on_tool_call=record,
                               on_wait=lambda s: waits.append(s))
        except Exception as e:
            history.pop()
            return {"error": f"{type(e).__name__}: {e}", "actions": actions}
        history[:] = result

    return {
        "reply": agent.last_reply(history),
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
