"""Веб-интерфейс ассистента.

    uvicorn web:app --reload
    открыть http://127.0.0.1:8000

История диалога хранится в памяти процесса: прототип рассчитан на одного
пользователя. Перезапуск сервера очищает переписку.
"""

from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import config
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


@app.post("/api/chat")
def chat(message: Message):
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
        history.append({"role": "user", "content": message.text})
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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
