"""Telegram-бот — второй интерфейс к тому же агенту.

    python bot.py

Long polling, поэтому не нужен внешний адрес. История диалога хранится
в памяти процесса, по одной на чат.
"""

import html
import re
import sys
import time

import requests

import agent
import config
import documents
import errors
import sheets
import tools

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # Telegram режет сообщения длиннее 4096 символов
SHEET_URL = "https://docs.google.com/spreadsheets/d/{id}/edit"

# В меню только то, чем пользуются ежедневно. Похожие задачи нужны раз
# в месяц при планировании — им место в командах, а не на главном экране.
MENU = {"keyboard": [
    [{"text": "Что горит"}, {"text": "Проекты"}],
    [{"text": "Подготовка к встрече"}],
], "resize_keyboard": True}

COMMANDS = [
    {"command": "start", "description": "Что умеет помощник"},
    {"command": "tasks", "description": "Что просрочено и что горит"},
    {"command": "projects", "description": "Список проектов"},
    {"command": "similar", "description": "Похожие задачи между проектами"},
    {"command": "reset", "description": "Начать диалог заново"},
]

TOOL_LABELS = {
    "get_project_context": "поднял контекст проекта",
    "list_overdue_tasks": "проверил задачи",
    "find_similar_tasks": "сравнил задачи между проектами",
    "add_tasks": "записал задачи в реестр",
    "update_task": "обновил задачу",
    "grounding_check": "поймал ссылку на несуществующую задачу",
}

GREETING = (
    "Помощник руководителя проектов внедрения.\n\n"
    "Что умею:\n"
    "- подготовить к встрече и напомнить прошлые договорённости\n"
    "- показать, что просрочено и что горит\n"
    "- разобрать заметки со встречи и записать задачи в реестр\n"
    "- найти повторяющиеся задачи у разных заказчиков\n"
    "- прочитать документ: пришлите PDF, DOCX или TXT файлом\n\n"
    "Удобнее всего начать с кнопки «Проекты»: выберите заказчика, "
    "и дальше все заметки и файлы будут относиться к нему.\n\n"
    "Пользуйтесь кнопками внизу или пишите словами."
)

# Переписка разделена по проектам, как и в веб-версии: ключ — пара
# (чат, проект). Общая история на три проекта заставляет модель смешивать
# контексты, и ошибки получаются правдоподобными, а потому незаметными.
histories = {}

# Выбранный проект держится за чатом: человек указывает заказчика один раз,
# дальше все заметки и файлы относятся к нему, пока проект не сменят.
active = {}


def history_of(chat_id):
    return histories.setdefault((chat_id, active.get(chat_id, "")), [])


def forget(chat_id):
    """Забывает переписку по всем проектам этого чата."""
    for key in [k for k in histories if k[0] == chat_id]:
        histories.pop(key)


# --- Telegram API ----------------------------------------------------------

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
    text = re.sub(r"^\s*[-*·]\s+", "• ", text, flags=re.MULTILINE)
    return text


def send(chat_id, text, as_html=True, markup=None):
    chunks = [text[i:i + LIMIT] for i in range(0, len(text), LIMIT)] or [""]
    for index, chunk in enumerate(chunks):
        params = {"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": True}
        if as_html:
            params["parse_mode"] = "HTML"
        # Клавиатуру вешаем только на последнее сообщение серии.
        if markup and index == len(chunks) - 1:
            params["reply_markup"] = markup
        try:
            call("sendMessage", **params)
        except RuntimeError:
            params.pop("parse_mode", None)
            call("sendMessage", **params)


def allowed(user_id):
    """Бот публичен по юзернейму, поэтому пускаем только владельца."""
    if not config.ALLOWED_USER_ID:
        return True
    return str(user_id) == str(config.ALLOWED_USER_ID)


# --- Клавиатуры ------------------------------------------------------------

def project_keyboard(action):
    """Инлайн-кнопки со списком проектов.

    Выбор кнопкой вместо набора названия заодно снимает вопрос склонений:
    в агента уходит точное имя клиента из реестра.
    """
    rows = [[{"text": p["client"], "callback_data": f"{action}:{p['project_id']}"}]
            for p in sheets.get_projects()]
    return {"inline_keyboard": rows} if rows else None


def project_actions(project_id):
    """Что можно сделать с выбранным проектом."""
    return {"inline_keyboard": [
        [{"text": "Подготовить к встрече", "callback_data": f"prep:{project_id}"}],
        [{"text": "Задачи", "callback_data": f"tasks:{project_id}"}],
        [{"text": "Внести заметки или файл", "callback_data": f"notes:{project_id}"}],
        [{"text": "Другой проект", "callback_data": "list:all"}],
    ]}


def project_card(project):
    progress = sheets.project_progress(project["project_id"])
    started = sheets.format_date(project.get("started"))
    done = ("задач нет" if progress["percent"] is None else
            f"{progress['percent']}% ({progress['done']} из {progress['total']} задач)")

    return (f"**{project['client']}** — {project['project']}\n"
            f"Этап: {project['stage']} · выполнено: {done}\n"
            f"Сроки: {started} — {sheets.deadline_note(project.get('planned_finish'))}\n"
            f"Контакт: {project['client_contact']}")


def find_project_by_id(project_id):
    return next((p for p in sheets.get_projects()
                 if p["project_id"] == project_id), None)


def after_reply_keyboard(reply, actions):
    """Кнопки под ответом: подтверждение или ссылка на результат."""
    buttons = []

    for name, result in actions:
        if name in ("add_tasks", "update_task") and config.SHEET_ID:
            link = SHEET_URL.format(id=config.SHEET_ID)
            if not any(b[0].get("url") == link for b in buttons):
                buttons.append([{"text": "Открыть реестр", "url": link}])

    # Ассистент ждёт подтверждения — не заставляем печатать «да».
    if not buttons and re.search(r"[Пп]одтвержда(ете|йте)|[Пп]одтвердите", reply or ""):
        buttons.append([
            {"text": "Записать", "callback_data": "confirm:yes"},
            {"text": "Отменить", "callback_data": "confirm:no"},
        ])

    return {"inline_keyboard": buttons} if buttons else None


# --- Работа с агентом ------------------------------------------------------

def ask(chat_id, text):
    """Один ход диалога: отправляет текст агенту и печатает ответ."""
    call("sendChatAction", chat_id=chat_id, action="typing")

    actions = []
    history = history_of(chat_id)
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
        print(errors.details(e), file=sys.stderr)
        send(chat_id, errors.describe(e), as_html=False)
        return

    histories[(chat_id, active.get(chat_id, ""))] = result
    reply = agent.last_reply(result)
    send(chat_id, to_html(reply), markup=after_reply_keyboard(reply, actions))

    # Совершённые действия показываем отдельной строкой: в чате нет журнала,
    # а понимать, что именно изменилось, человеку необходимо.
    done = []
    for name, res in actions:
        if name not in tools.WRITE_TOOLS or not isinstance(res, dict):
            continue
        label = TOOL_LABELS.get(name, name)
        if res.get("error"):
            done.append(f"не удалось: {label} — {res['error']}")
        elif name == "add_tasks" and res.get("created"):
            done.append(f"{label}: {', '.join(res['created'])}")
        elif name == "update_task":
            done.append(f"{label}: {res.get('updated')} — срок "
                        f"{sheets.format_date(res.get('due'))}, "
                        f"статус «{res.get('status')}»")
    if done:
        send(chat_id, to_html("**Сделано:**\n" + "\n".join(f"- {d}" for d in done)))


def show_projects(chat_id):
    """Список проектов берём из таблицы напрямую.

    Тратить вызов модели на то, что можно прочитать из реестра, незачем:
    ответ приходит мгновенно и не расходует квоту.
    """
    projects = sheets.get_projects()
    if not projects:
        send(chat_id, "В реестре нет проектов.", as_html=False)
        return

    text = "\n\n".join(project_card(p) for p in projects)
    send(chat_id, to_html(text + "\n\nВыберите проект, чтобы работать с ним:"),
         markup=project_keyboard("open"))


# --- Обработка сообщений ---------------------------------------------------

def download(file_id):
    info = call("getFile", file_id=file_id)
    url = (f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}"
           f"/{info['file_path']}")
    return requests.get(url, timeout=60).content


def read_document(chat_id, document, caption):
    """Скачивает вложение и превращает в текст сообщения для агента."""
    name = document.get("file_name", "документ")
    if document.get("file_size", 0) > 20 * 1024 * 1024:
        send(chat_id, "Файл больше 20 МБ — Telegram не отдаёт такие ботам.",
             as_html=False)
        return None

    call("sendChatAction", chat_id=chat_id, action="typing")
    try:
        text = documents.as_message(name, download(document["file_id"]))
    except documents.UnsupportedFile as e:
        send(chat_id, f"Не смог прочитать «{name}». {e}", as_html=False)
        return None
    except Exception as e:
        print(errors.details(e), file=sys.stderr)
        send(chat_id, errors.describe(e), as_html=False)
        return None

    return f"{caption}\n\n{text}" if caption else text


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    if not allowed(message["from"]["id"]):
        send(chat_id, "Этот бот приватный.", as_html=False)
        return

    is_document = bool(message.get("document"))
    if is_document:
        text = read_document(chat_id, message["document"],
                             (message.get("caption") or "").strip())
        if not text:
            return

    if not text:
        send(chat_id, "Пришлите текст или документ — вопрос, заметки "
                      "со встречи или файл с ТЗ.", as_html=False)
        return

    if text in ("/start", "/help"):
        forget(chat_id)
        active.pop(chat_id, None)
        send(chat_id, to_html(GREETING), markup=MENU)
        return

    if text == "/reset":
        forget(chat_id)
        active.pop(chat_id, None)
        send(chat_id, "Диалог очищен.", as_html=False, markup=MENU)
        return

    if text in ("/projects", "Проекты"):
        show_projects(chat_id)
        return

    if text in ("/similar", "Похожие задачи"):
        ask(chat_id, "Найди похожие открытые задачи у разных заказчиков. "
                     "По каждой группе скажи, что в них общего и что можно "
                     "сделать один раз шаблоном, а что придётся "
                     "персонализировать под конкретного клиента.")
        return

    if text in ("/tasks", "Что горит"):
        ask(chat_id, "Что просрочено и что горит на этой неделе?")
        return

    if text == "Подготовка к встрече":
        keyboard = project_keyboard("prep")
        if keyboard:
            send(chat_id, "С каким заказчиком встреча?", as_html=False,
                 markup=keyboard)
        else:
            send(chat_id, "В реестре нет проектов.", as_html=False)
        return

    # Свободный текст и файлы привязываются к выбранному проекту, чтобы
    # не приходилось называть заказчика в каждом сообщении.
    project = find_project_by_id(active.get(chat_id, ""))
    if project:
        text = f"Речь о проекте «{project['client']}».\n\n{text}"

    ask(chat_id, text)


def handle_callback(query):
    chat_id = query["message"]["chat"]["id"]
    data = query.get("data", "")

    if not allowed(query["from"]["id"]):
        call("answerCallbackQuery", callback_query_id=query["id"],
             text="Бот приватный")
        return

    call("answerCallbackQuery", callback_query_id=query["id"])
    # Кнопки одноразовые: убираем их, чтобы нельзя было нажать дважды.
    try:
        call("editMessageReplyMarkup", chat_id=chat_id,
             message_id=query["message"]["message_id"],
             reply_markup={"inline_keyboard": []})
    except RuntimeError:
        pass

    if data == "list:all":
        keyboard = project_keyboard("open")
        if keyboard:
            send(chat_id, "Какой проект?", as_html=False, markup=keyboard)
        return

    if ":" in data and data.split(":", 1)[0] in ("open", "prep", "tasks",
                                                 "notes"):
        action, project_id = data.split(":", 1)
        project = find_project_by_id(project_id)
        if not project:
            send(chat_id, "Проект не найден.", as_html=False)
            return

        active[chat_id] = project_id
        client = project["client"]

        if action == "open":
            send(chat_id, to_html(project_card(project)),
                 markup=project_actions(project_id))
        elif action == "prep":
            ask(chat_id, f"Подготовь меня к встрече с «{client}»")
        elif action == "tasks":
            ask(chat_id, f"Покажи открытые задачи по проекту «{client}»")
        elif action == "notes":
            send(chat_id, to_html(
                f"Работаем по проекту **{client}**.\n\n"
                "Пришлите заметки со встречи текстом или документом — "
                "разберу и предложу задачи. Можно просто задать вопрос "
                "по проекту.\n\n"
                "Чтобы сменить проект, нажмите «Проекты»."))
        return

    if data == "confirm:yes":
        ask(chat_id, "Да, подтверждаю. Вноси изменения.")

    if data == "confirm:no":
        send(chat_id, "Отменил, ничего не записываю.", as_html=False)


# --- Запуск ----------------------------------------------------------------

def main():
    config.require("TELEGRAM_TOKEN", "LLM_API_KEY")

    me = call("getMe")
    call("setMyCommands", commands=COMMANDS)

    # Описание видно до нажатия Start, короткое — в профиле бота.
    # Ставится программно, чтобы не настраивать руками у @BotFather.
    try:
        call("setMyDescription", description=(
            "Помощник руководителя проектов внедрения AI-решений. "
            "Разбирает заметки со встреч, ведёт реестр задач и готовит "
            "к следующим встречам."))
        call("setMyShortDescription", short_description=(
            "Заметки со встречи → задачи в реестре проекта"))
    except RuntimeError as e:
        print(f"Не удалось обновить описание бота: {e}", file=sys.stderr)
    print(f"Бот @{me['username']} запущен. Остановить: Ctrl+C")
    if not config.ALLOWED_USER_ID:
        print("Внимание: ALLOWED_USER_ID не задан, бот отвечает всем подряд.")

    offset = None
    while True:
        try:
            updates = call("getUpdates", offset=offset, timeout=50,
                           allowed_updates=["message", "callback_query"])
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"Ошибка опроса: {e}", file=sys.stderr)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
            except Exception as e:
                print(f"Ошибка обработки: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
