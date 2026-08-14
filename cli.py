"""Консольный запуск ассистента — для разработки и отладки.

    python cli.py

Показывает каждый вызов инструмента: что вызвано, с чем и что вернулось.
Ввод многострочный: пустая строка отправляет сообщение.
"""

import json
import sys

import agent
import config

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def show_tool_call(name, args, result):
    short_args = json.dumps(args, ensure_ascii=False)[:120]
    print(f"{DIM}  → {name}({short_args}){RESET}")

    if isinstance(result, dict) and "error" in result:
        print(f"{DIM}    ошибка: {result['error']}{RESET}")
    else:
        preview = json.dumps(result, ensure_ascii=False, default=str)
        if len(preview) > 200:
            preview = preview[:200] + "…"
        print(f"{DIM}    {preview}{RESET}")


def show_wait(seconds):
    print(f"{DIM}  ждём {seconds:.0f} с — исчерпан лимит запросов в минуту{RESET}")


def read_message():
    """Читает многострочный ввод. Отправка — пустой строкой.

    Иначе вставка абзаца из буфера превращается в десяток отдельных
    сообщений, и ассистент разбирает обрывки вместо целого текста.
    """
    print(f"{BOLD}вы:{RESET} ", end="", flush=True)
    lines = []
    while True:
        line = input()
        if not line.strip():
            if lines:
                return "\n".join(lines)
            print(f"{BOLD}вы:{RESET} ", end="", flush=True)
            continue
        lines.append(line)


def main():
    config.require("LLM_API_KEY", "SHEET_ID", "CALENDAR_ID")

    print(f"{BOLD}Ассистент внедренца{RESET}  "
          f"{DIM}модель: {config.LLM_MODEL} · выход: Ctrl+C{RESET}")
    print(f"{DIM}Ввод многострочный — отправка пустой строкой (Enter дважды){RESET}\n")
    print(f"{DIM}Попробуйте: «подготовь меня к встрече с ТехноСнабом» "
          f"или «что горит на этой неделе»{RESET}\n")

    history = []
    while True:
        try:
            text = read_message()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        history.append({"role": "user", "content": text})
        try:
            history = agent.run(history, on_tool_call=show_tool_call,
                                on_wait=show_wait)
        except Exception as e:
            print(f"\n  Сбой: {type(e).__name__}: {e}\n", file=sys.stderr)
            history.pop()
            continue

        print(f"\n{agent.last_reply(history)}\n")


if __name__ == "__main__":
    main()
