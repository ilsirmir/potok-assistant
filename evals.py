"""Прогон агента на наборе сценариев.

    python evals.py                 все сценарии
    python evals.py 3 7             только выбранные
    python evals.py --repeat 3      каждый сценарий трижды

Работает только в демо-режиме, потому что перед каждым сценарием реестр
возвращается в исходное состояние. На настоящей таблице это стерло бы
рабочие данные.

Зачем это нужно. Правки промпта проверяются глазами на одном примере, и
почти каждая вторая ломает то, что работало раньше. Набор сценариев дает
цифру вместо ощущения и позволяет сравнивать модели между собой.
"""

import argparse
import re
import sys
import time

import agent
import config
import errors
import local_store
import tools


# --- Проверки --------------------------------------------------------------

def called(name):
    """Инструмент был вызван."""
    def check(reply, calls):
        if not any(c["name"] == name for c in calls):
            return f"не вызван {name}"
    return check


def not_called(name):
    """Инструмент не вызывался."""
    def check(reply, calls):
        if any(c["name"] == name for c in calls):
            return f"вызван {name}, хотя не должен"
    return check


def mentions(*fragments):
    """В ответе есть каждый из фрагментов, регистр не важен."""
    def check(reply, calls):
        low = reply.lower()
        missing = [f for f in fragments if f.lower() not in low]
        if missing:
            return "в ответе нет: " + ", ".join(missing)
    return check


def mentions_any(*fragments):
    """В ответе есть хотя бы один из фрагментов.

    Модель формулирует одну и ту же мысль по-разному, поэтому проверять
    точное слово значит ловить не качество, а везение.
    """
    def check(reply, calls):
        low = reply.lower()
        if not any(f.lower() in low for f in fragments):
            return "в ответе нет ни одного из: " + ", ".join(fragments)
    return check


def avoids(*fragments):
    """В ответе нет ни одного из фрагментов."""
    def check(reply, calls):
        low = reply.lower()
        found = [f for f in fragments if f.lower() in low]
        if found:
            return "в ответе лишнее: " + ", ".join(found)
    return check






def called_any(*names):
    """Вызван хотя бы один из инструментов."""
    def check(reply, calls):
        if not any(c["name"] in names for c in calls):
            return "не вызван ни один из: " + ", ".join(names)
    return check


def date_used(pattern):
    """Среди аргументов любого вызова есть дата по образцу.

    Сценарий проверяет перевод словесного срока в дату, а не выбор
    инструмента. Записать новую задачу или сдвинуть существующую это
    решение агента, и обе трактовки бывают верны.
    """
    def check(reply, calls):
        for call in calls:
            if re.search(pattern, str(call["args"])):
                return None
        return f"ни в одном вызове нет даты по образцу {pattern}"
    return check


def no_status(value):
    """Ни одна задача не переведена в указанный статус."""
    def check(reply, calls):
        for call in calls:
            if call["name"] == "update_task" and call["args"].get("status") == value:
                return f"задача переведена в статус «{value}»"
    return check


# --- Сценарии --------------------------------------------------------------

NOTES_MEDIA = (
    "Созвонились с Петровой. Раздел базы знаний по новому тарифу она обещала "
    "к концу августа, но не успевает и просит перенести на 10 сентября. "
    "Отчет по пробелам доделаю до конца недели."
)

CASES = [
    {
        "name": "Контекст проекта",
        "turns": ["Подготовь меня к встрече с ТехноСнабом"],
        "checks": [called("get_project_context"),
                   mentions("техноснаб", "1С")],
    },
    {
        "name": "Склонение в названии",
        "turns": ["Что у нас с Ромашкой?"],
        "checks": [called("get_project_context"),
                   mentions("ромашка"),
                   avoids("не найден")],
    },
    {
        "name": "Сводка просроченного",
        "turns": ["Что просрочено и что горит на этой неделе?"],
        "checks": [called("list_overdue_tasks"),
                   mentions("T-004")],
    },
    {
        "name": "Повторяющиеся задачи",
        "turns": ["Найди похожие открытые задачи у разных заказчиков"],
        "checks": [called("find_similar_tasks"),
                   mentions_any("T-0", "T-"),
                   mentions_any("ромашка", "техноснаб", "медиалайн")],
    },
    {
        "name": "Не выдумывает номера",
        "turns": ["Что там с задачей T-099? И о чем договаривались на встрече M-042?"],
        "checks": [avoids("договорились о", "было решено"),
                   mentions("нет")],
    },
    {
        "name": "Ждет подтверждения",
        "turns": [f"Разбери заметки со встречи с МедиаЛайн. {NOTES_MEDIA}"],
        "checks": [not_called("add_tasks"),
                   not_called("update_task"),
                   mentions_any("подтверд", "подтвержда", "записыв", "внести",
                                "все верно", "всё верно", "согласн", "?")],
    },
    {
        "name": "Записывает после согласия",
        "turns": [f"Разбери заметки со встречи с МедиаЛайн. {NOTES_MEDIA}",
                  "Да, записывай"],
        "checks": [called("update_task")],
    },
    {
        "name": "Будущее время не закрывает задачу",
        "turns": [f"Разбери заметки со встречи с МедиаЛайн. {NOTES_MEDIA}",
                  "Да, записывай"],
        "checks": [no_status("Выполнена")],
    },
    {
        "name": "Относительный срок в дату",
        "turns": ["Разбери заметки со встречи с ТехноСнабом. Провести обучение "
                  "операторов заказчика договорились в понедельник.",
                  "Да, записывай"],
        "checks": [called_any("add_tasks", "update_task"),
                   date_used(r"\d{4}-\d{2}-\d{2}")],
    },
    {
        "name": "Не создает дубль",
        "turns": ["Разбери заметки со встречи с Ромашкой. Кузьмин из ИТ так и не "
                  "согласовал права доступа к сделкам.",
                  "Да, записывай"],
        "checks": [mentions("T-004")],
    },
]


# --- Прогон ----------------------------------------------------------------

def run_case(case):
    """Один сценарий. Возвращает результат и метрики."""
    local_store.reset()
    history, calls = [], []
    started = time.monotonic()

    try:
        for turn in case["turns"]:
            history.append({"role": "user", "content": turn})
            history = agent.run(
                history,
                on_tool_call=lambda name, args, result: calls.append(
                    {"name": name, "args": args, "result": result}),
            )
    except Exception as e:
        # Упор в лимит запросов говорит о тарифе, а не о качестве агента.
        # Такой сценарий помечается отдельно и в статистику не идет.
        text = str(e).lower()
        limited = any(k in text for k in ("quota", "rate", "429", "exhausted"))
        return {"ok": False, "limited": limited,
                "problems": [errors.describe(e)[:76]],
                "calls": len(calls), "seconds": time.monotonic() - started}

    reply = agent.last_reply(history)
    problems = [p for p in (check(reply, calls) for check in case["checks"]) if p]

    return {"ok": not problems, "problems": problems, "reply": reply,
            "calls": len(calls), "seconds": time.monotonic() - started,
            "errors": len([c for c in calls
                           if isinstance(c["result"], dict) and c["result"].get("error")])}


def main():
    parser = argparse.ArgumentParser(description="Прогон агента на сценариях")
    parser.add_argument("cases", nargs="*", type=int,
                        help="номера сценариев, по умолчанию все")
    parser.add_argument("--repeat", type=int, default=1,
                        help="сколько раз прогнать каждый сценарий")
    parser.add_argument("--verbose", action="store_true",
                        help="печатать ответы целиком")
    parser.add_argument("--delay", type=float, default=8.0,
                        help="пауза между сценариями в секундах, "
                             "спасает от лимита бесплатного тарифа")
    args = parser.parse_args()

    if not config.DEMO_MODE:
        print("Прогон работает только в демо-режиме, иначе он изменит рабочий "
              "реестр.\nДобавьте DEMO_MODE=1 в .env и повторите.")
        sys.exit(1)

    selected = ([CASES[i - 1] for i in args.cases if 1 <= i <= len(CASES)]
                if args.cases else CASES)

    print(f"Модель {config.LLM_MODEL}, сценариев {len(selected)}, "
          f"повторов {args.repeat}\n")

    passed = total = total_calls = total_errors = limited = 0
    started = time.monotonic()

    for index, case in enumerate(selected, 1):
        outcomes = []
        for attempt in range(args.repeat):
            if index > 1 or attempt > 0:
                time.sleep(args.delay)
            result = run_case(case)
            outcomes.append(result)
            total_calls += result["calls"]
            total_errors += result.get("errors", 0)
            if result.get("limited"):
                limited += 1
            else:
                total += 1
                passed += result["ok"]

        good = sum(o["ok"] for o in outcomes)
        if all(o.get("limited") for o in outcomes):
            mark = "лимит "
        elif good == len(outcomes):
            mark = "ок    "
        else:
            mark = "ошибка"
        share = f"{good}/{len(outcomes)}" if args.repeat > 1 else ""
        seconds = sum(o["seconds"] for o in outcomes) / len(outcomes)

        print(f"  [{mark}] {index}. {case['name']:36} {share:>5} "
              f"{seconds:5.1f} с")

        for outcome in outcomes:
            for problem in outcome["problems"]:
                print(f"            {problem}")
            if (args.verbose or not outcome["ok"]) and outcome.get("reply"):
                text = outcome["reply"] if args.verbose else outcome["reply"][:400]
                print(f"            ---\n{text}\n")

    print("\n" + "-" * 60)
    print(f"Пройдено {passed} из {total}, "
          f"вызовов инструментов {total_calls}, "
          f"ошибок в инструментах {total_errors}, "
          f"всего {time.monotonic() - started:.0f} с")
    if limited:
        print(f"Не проверено из-за лимита запросов: {limited}. "
              f"Увеличьте паузу ключом --delay или подключите платный тариф.")

    local_store.reset()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
