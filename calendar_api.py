"""Создание событий в Google Calendar."""

import datetime as dt
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config
import local_store


@lru_cache(maxsize=1)
def _service():
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS, scopes=config.SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_event(title, date, description="", duration_minutes=30, time=None):
    """Создаёт событие. Без времени — на весь день.

    date — строка YYYY-MM-DD, time — HH:MM или None.
    """
    if config.DEMO_MODE:
        return local_store.add_event(title, date, description, time)

    body = {"summary": title, "description": description}

    if time:
        start = dt.datetime.fromisoformat(f"{date}T{time}")
        end = start + dt.timedelta(minutes=duration_minutes)
        body["start"] = {"dateTime": start.isoformat(), "timeZone": config.TIMEZONE}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": config.TIMEZONE}
    else:
        day = dt.date.fromisoformat(date)
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (day + dt.timedelta(days=1)).isoformat()}

    event = _service().events().insert(
        calendarId=config.CALENDAR_ID, body=body).execute()

    return {"id": event["id"], "link": event.get("htmlLink", "")}
