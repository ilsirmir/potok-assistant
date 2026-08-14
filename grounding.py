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


CORRECTION = (
    "Проверка реестра: идентификаторов {ids} не существует. "
    "Ты сослался на то, чего нет. Перепиши предыдущий ответ: убери "
    "несуществующие номера, при необходимости вызови get_project_context "
    "и возьми настоящие. Если подходящей задачи в реестре нет — так и "
    "напиши, не подставляй номер наугад."
)


def correction_message(unknown):
    return {"role": "user", "content": CORRECTION.format(ids=", ".join(unknown))}
