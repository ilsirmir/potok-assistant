"""Понятные сообщения об ошибках.

Библиотеки возвращают текст, написанный для разработчика. Человеку, который
видит проект впервые, строка вида BadRequestError 400 FAILED_PRECONDITION не
говорит ничего, а причина обычно простая и решается за минуту.
"""

import re

# Порядок важен, проверки идут сверху вниз до первого совпадения.
PATTERNS = [
    (r"location is not supported",
     "Провайдер модели не обслуживает ваш регион. Включите VPN или укажите "
     "в .env другого провайдера, например DeepSeek или GigaChat."),

    (r"api key not valid|invalid api key|incorrect api key|missing credentials",
     "Ключ модели не принят. Проверьте LLM_API_KEY в файле .env, он мог "
     "скопироваться с лишними пробелами или кавычками."),

    (r"quota|rate.?limit|resource_exhausted|429",
     "Исчерпан лимит запросов к модели. Подождите минуту или подключите "
     "платный тариф, на бесплатном доступно всего несколько запросов в минуту."),

    (r"no longer available|not found.*model|model.*not found|does not exist",
     "Такой модели у провайдера нет. Названия моделей меняются, посмотрите "
     "актуальное имя в личном кабинете и поправьте LLM_MODEL в .env."),

    (r"thought_signature",
     "Провайдер отклонил историю диалога. Начните разговор заново кнопкой "
     "«Начать заново» или командой /reset."),

    (r"credentials\.json|service_account|default credentials",
     "Не найден файл credentials.json с ключом сервисного аккаунта Google. "
     "Положите его рядом с web.py или удалите SHEET_ID из .env, чтобы "
     "проект работал на локальных демо-данных."),

    (r"permission|forbidden|403",
     "Нет доступа к таблице. Выдайте сервисному аккаунту права редактора, "
     "его адрес лежит в credentials.json в поле client_email."),

    (r"not.*supported.*office|must not be an office file",
     "На Диске лежит файл Excel, а не Google Таблица. Откройте его и "
     "выберите «Сохранить как Google Таблицы», затем возьмите новый "
     "идентификатор из адресной строки."),

    (r"timeout|timed out|connection|network|unreachable|getaddrinfo",
     "Нет связи с сервисом. Проверьте интернет, а если провайдер модели "
     "недоступен из вашей сети, включите VPN."),

    (r"unauthorized|401",
     "Доступ отклонен. Проверьте ключи в файле .env."),
]


def describe(error):
    """Понятное объяснение ошибки. Технический текст добавляется в скобках."""
    raw = f"{type(error).__name__}: {error}"
    lowered = raw.lower()

    for pattern, message in PATTERNS:
        if re.search(pattern, lowered):
            return message

    # Незнакомая ошибка. Показываем как есть, иначе человек останется без
    # единственной зацепки для поиска причины.
    return f"Непредвиденная ошибка. {raw[:300]}"


def details(error):
    """Технический текст для консоли, куда смотрит разработчик."""
    return f"{type(error).__name__}: {error}"
