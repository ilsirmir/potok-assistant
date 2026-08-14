"""Telegram-бот — второй интерфейс к тому же агенту.

    python bot.py

Использует long polling, поэтому не требует внешнего адреса и запускается
где угодно. История диалога хранится в памяти процесса, по одной на чат.
"""

import html
import re
import sys
import time

import requests

import agent
import config
import sheets
import tools

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # Telegram режет сообщения длиннее 4096 символов

TOOL_LABELS = {
    "get_project_context": "поднял контекст проекта",
    "list_overdue_tasks": "проверил задачи",
    "add_tasks": "записал задачи в реестр",
    "update_task": "обновил задачу",
    "create_deadline": "поставил событие в календарь",
    "grounding_check": "поймал ссылку на несуществующую задачу",
}

histories = {}


def call(method, **params):
    response = requests.post(API.format(token=config.TELEGRAM_TOKEN, method=method),
                             json=params, timeout=70)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "неизвестная ошибка Telegram"))
    return data["result"]


def to_html(text):
    """Markdown модели → безопасный HTML для Telegram.

    Полноценный MarkdownV2 требует экранировать полтора десятка символов,
    и любая ошибка роняет отправку. Проще экранировать всё и вернуть
    только жирный шрифт с курсивом.
    """
    text = html.escape(text or "")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)
    return text


def send(chat_id, text, as_html=True):
    for start in range(0, len(text), LIMIT):
        chunk = text[start:start + LIMIT]
        params = {"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True}
        if as_html:
            params["parse_mode"] = "HTML"
        try:
            call("sendMessage", **params)
        except RuntimeError:
            # Если разметка всё же не понравилась — отправляем как есть.
            call("sendMessage", chat_id=chat_id, text=chunk)


def allowed(user_id):
    """Бот публичен по юзернейму, поэтому пускаем только владельца."""
    if not config.ALLOWED_USER_ID:
        return True
    return str(user_id) == str(config.ALLOWED_USER_ID)


def handle(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()

    if not allowed(user_id):
        send(chat_id, "Этот бот приватный.", as_html=False)
        return
    if not text:
        send(chat_id, "Пришлите текст — вопрос или заметки со встречи.",
             as_html=False)
        return

    if text in ("/start", "/help"):
        histories.pop(chat_id, None)
        send(chat_id, to_html(
            "Помощник руководителя проектов внедрения.\n\n"
            "Что умею:\n"
            "- подготовить к встрече и напомнить прошлые договорённости\n"
            "- показать, что просрочено и что горит\n"
            "- разобрать заметки со встречи и записать задачи в реестр\n\n"
            "Просто напишите: **подготовь меня к встрече с ТехноСнабом**\n"
            "или пришлите заметки текстом.\n\n"
            "/reset — начать диалог заново"))
        return

    if text == "/reset":
        histories.pop(chat_id, None)
        send(chat_id, "Диалог очищен.", as_html=False)
        return

    call("sendChatAction", chat_id=chat_id, action="typing")

    actions = []
    history = histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": text})

    try:
        result = agent.run(
            history,
            on_tool_call=lambda name, args, res: actions.append((name, res)),
            on_wait=lambda s: call("sendChatAction", chat_id=chat_id,
                                   action="typing"),
        )
    except Exception as e:
        history.pop()
        send(chat_id, f"Сбой: {type(e).__name__}: {e}", as_html=False)
        return

    histories[chat_id] = result
    send(chat_id, to_html(agent.last_reply(result)))

    # Совершённые действия показываем отдельной строкой: в чате не видно
    # журнала, а понимать, что именно изменилось, человеку необходимо.
    done = []
    for name, res in actions:
        if name not in tools.WRITE_TOOLS:
            continue
        label = TOOL_LABELS.get(name, name)
        if isinstance(res, dict) and res.get("error"):
            done.append(f"не удалось: {label} — {res['error']}")
        elif name == "add_tasks" and res.get("created"):
            done.append(f"{label}: {', '.join(res['created'])}")
        elif name == "create_deadline":
            done.append(f"{label}: {res.get('created')} — "
                        f"{sheets.format_date(res.get('date'))}")
        elif name == "update_task":
            done.append(f"{label}: {res.get('updated')} — срок "
                        f"{sheets.format_date(res.get('due'))}, "
                        f"статус «{res.get('status')}»")
    if done:
        send(chat_id, to_html("**Сделано:**\n" + "\n".join(f"- {d}" for d in done)))

def main():
    config.require("TELEGRAM_TOKEN", "LLM_API_KEY", "SHEET_ID", "CALENDAR_ID")

    me = call("getMe")
    print(f"Бот @{me['username']} запущен. Остановить: Ctrl+C")
    if not config.ALLOWED_USER_ID:
        print("Внимание: ALLOWED_USER_ID не задан, бот отвечает всем подряд.")

    offset = None
    while True:
        try:
            updates = call("getUpdates", offset=offset, timeout=50,
                           allowed_updates=["message"])
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"Ошибка опроса: {e}", file=sys.stderr)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            if "message" in update:
                try:
                    handle(update["message"])
                except Exception as e:
                    print(f"Ошибка обработки: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
