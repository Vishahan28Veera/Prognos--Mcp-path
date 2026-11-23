"""Mock service centre booking system.

A real deployment would call the dealer's or service chain's booking API.
The mock offers 90-minute bays Monday to Saturday, with some already taken,
plus a daily emergency slot that is only offered for CRITICAL vehicles.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

SLOT_MINUTES = 90
OPEN_HOURS = [time(9, 0), time(10, 30), time(12, 0), time(14, 0), time(15, 30), time(17, 0)]


@dataclass
class Slot:
    slot_id: str
    centre: str
    start: datetime
    end: datetime
    emergency: bool = False
    booked_by: str | None = None

    def to_dict(self) -> dict:
        return {"slot_id": self.slot_id, "centre": self.centre, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "emergency": self.emergency}


class MockServiceCentre:
    def __init__(self, now: datetime, name: str = "Prognos Partner EV Service, Peelamedu",
                 tz: str = "Asia/Kolkata", seed: int = 5, days: int = 60):
        self.name = name
        self.tz = ZoneInfo(tz)
        self.now = now.astimezone(self.tz)
        self.slots: dict[str, Slot] = {}
        self.bookings: dict[str, dict] = {}
        rng = random.Random(seed)
        for d in range(days):
            day = self.now.date() + timedelta(days=d)
            if day.weekday() == 6:      # closed Sundays
                continue
            for t in OPEN_HOURS:
                start = datetime.combine(day, t, self.tz)
                sid = f"SC-{start:%Y%m%d-%H%M}"
                slot = Slot(sid, name, start, start + timedelta(minutes=SLOT_MINUTES))
                if rng.random() < 0.45:
                    slot.booked_by = "another customer"
                self.slots[sid] = slot
            # One emergency bay per day, late afternoon, held back for critical cases.
            es = datetime.combine(day, time(18, 30), self.tz)
            eid = f"SC-{es:%Y%m%d-%H%M}-E"
            self.slots[eid] = Slot(eid, name, es, es + timedelta(minutes=SLOT_MINUTES), emergency=True)

    def available(self, start: datetime, end: datetime, include_emergency: bool = False) -> list[Slot]:
        return sorted(
            (s for s in self.slots.values()
             if s.booked_by is None and s.start >= start and s.end <= end
             and (include_emergency or not s.emergency)),
            key=lambda s: s.start,
        )

    def get(self, slot_id: str) -> Slot:
        if slot_id not in self.slots:
            raise KeyError(f"Unknown slot '{slot_id}'")
        return self.slots[slot_id]

    def book(self, slot_id: str, vehicle_id: str, note: str) -> dict:
        slot = self.get(slot_id)
        if slot.booked_by is not None:
            raise ValueError(f"Slot {slot_id} is no longer available")
        slot.booked_by = vehicle_id
        ref = f"BK-{len(self.bookings) + 1:04d}"
        self.bookings[ref] = {"booking_ref": ref, "vehicle_id": vehicle_id, "note": note, **slot.to_dict()}
        return self.bookings[ref]
