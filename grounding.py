"""Проверка ответа модели на выдуманные идентификаторы.

Самая частая галлюцинация в этом сценарии — сослаться на задачу или
встречу, которой нет: «как договорились в M-009», «закроем вместе с T-021».
Звучит уверенно, проверяется трудно, доверие разрушает мгновенно.

Промпт такие вещи снижает, но не исключает. Поэтому ответ перед показом
человеку сверяется с реестром: все упомянутые номера должны существовать.
"""

import re

import sheets

REFERENCE = re.compile(r"\b([TMP])-(\d{1,4})\b", re.IGNORECASE)


def known_ids():
    """Все существующие идентификаторы. Читается из кэша sheets."""
    ids = set()
    for task in sheets.get_tasks():
        ids.add(task["task_id"].upper())
    for meeting in sheets.get_meetings():
        ids.add(meeting["meeting_id"].upper())
    for project in sheets.get_projects():
        ids.add(project["project_id"].upper())
    return ids


def find_unknown(text):
    """Упомянутые в тексте идентификаторы, которых нет в реестре."""
    if not text:
        return []

    mentioned = {f"{m.group(1).upper()}-{int(m.group(2)):03d}"
                 for m in REFERENCE.finditer(text)}
    if not mentioned:
        return []

    return sorted(mentioned - known_ids())


# Модель иногда вставляет в русские слова символы других письменностей —
# «проسрочено» вместо «просрочено». Выглядит как испорченный шрифт, а на
# деле сбой генерации. Промптом не лечится, поэтому чистим на выходе.
FOREIGN = re.compile(
    r"[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F\u0900-\u097F"
    r"\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]+")


def clean_text(text):
    """Убирает случайные символы чужих алфавитов из русского текста."""
    return FOREIGN.sub("", text or "")


CORRECTION = (
    "Проверка реестра: идентификаторов {ids} не существует. "
    "Ты сослался на то, чего нет. Перепиши предыдущий ответ: убери "
    "несуществующие номера, при необходимости вызови get_project_context "
    "и возьми настоящие. Если подходящей задачи в реестре нет — так и "
    "напиши, не подставляй номер наугад."
)


def correction_message(unknown):
    return {"role": "user", "content": CORRECTION.format(ids=", ".join(unknown))}
