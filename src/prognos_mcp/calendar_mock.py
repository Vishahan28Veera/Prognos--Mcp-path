"""Calendar providers.

`MockCalendar` fakes a busy person's week so the agent has something real to
work around. `GoogleCalendarProvider` is the next step and shows which Google
Calendar API calls would back the same interface.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


@dataclass
class Event:
    title: str
    start: datetime
    end: datetime
    source: str = "user"

    def to_dict(self) -> dict:
        return {"title": self.title, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "source": self.source}


class CalendarProvider(Protocol):
    tz: ZoneInfo

    def busy(self, start: datetime, end: datetime) -> list[Event]: ...
    def is_free(self, start: datetime, end: datetime) -> bool: ...
    def create_event(self, title: str, start: datetime, end: datetime, description: str = "") -> Event: ...


class MockCalendar:
    """A deterministic fake week: office hours on weekdays, a few personal plans."""

    def __init__(self, now: datetime, tz: str = "Asia/Kolkata", seed: int = 3, days: int = 60):
        self.tz = ZoneInfo(tz)
        self.now = now.astimezone(self.tz)
        self.events: list[Event] = []
        rng = random.Random(seed)
        day0 = self.now.date()
        for d in range(days):
            day = day0 + timedelta(days=d)
            at = lambda h, m=0: datetime.combine(day, time(h, m), self.tz)  # noqa: E731
            if day.weekday() < 5:
                self.events.append(Event("Work", at(9, 30), at(18, 0)))
                if rng.random() < 0.35:
                    self.events.append(Event("Gym", at(19, 0), at(20, 0)))
                if rng.random() < 0.2:
                    self.events.append(Event("Dinner with friends", at(20, 0), at(22, 0)))
            else:
                if rng.random() < 0.5:
                    self.events.append(Event("Family lunch", at(12, 30), at(14, 30)))
                if rng.random() < 0.3:
                    self.events.append(Event("Cricket", at(7, 0), at(9, 30)))

    def busy(self, start: datetime, end: datetime) -> list[Event]:
        return sorted((e for e in self.events if e.start < end and e.end > start), key=lambda e: e.start)

    def is_free(self, start: datetime, end: datetime) -> bool:
        return not self.busy(start, end)

    def create_event(self, title: str, start: datetime, end: datetime, description: str = "") -> Event:
        ev = Event(title, start, end, source="prognos-agent")
        self.events.append(ev)
        return ev


class GoogleCalendarProvider:
    """Next step: the same interface backed by Google Calendar.

    Planned mapping:
      busy()/is_free()  -> POST /calendar/v3/freeBusy   (scope: calendar.freebusy)
      create_event()    -> POST /calendar/v3/calendars/primary/events (scope: calendar.events)

    Only free/busy is read, never event titles, so the agent learns when the
    user is free without seeing what they are doing. Event creation happens
    only from `confirm_booking`, after the user says yes.
    """

    def __init__(self, credentials_path: str, tz: str = "Asia/Kolkata"):
        self.tz = ZoneInfo(tz)
        self.credentials_path = credentials_path

    def busy(self, start: datetime, end: datetime) -> list[Event]:
        raise NotImplementedError("Google Calendar integration is on the roadmap.")

    def is_free(self, start: datetime, end: datetime) -> bool:
        raise NotImplementedError("Google Calendar integration is on the roadmap.")

    def create_event(self, title: str, start: datetime, end: datetime, description: str = "") -> Event:
        raise NotImplementedError("Google Calendar integration is on the roadmap.")
