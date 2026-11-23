"""Agent-side policy: urgency, booking window, slot matching and plain-language explanations.

This is the part of the system that belongs in the open. It only consumes the
engine's public outputs (status, confirmed fault, RUL in cycles).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .calendar_mock import CalendarProvider
from .service_centre import MockServiceCentre, Slot

TRAVEL_BUFFER = timedelta(minutes=30)   # time to get to and from the service centre
LEAD_TIME = timedelta(hours=2)          # never offer a slot that starts in the next 2 hours


@dataclass
class UrgencyPolicy:
    level: str          # none | suggest | soon | urgent
    max_days: float | None
    tone: str
    prefer_weekends: bool
    allow_emergency: bool


POLICY = {
    "HEALTHY": UrgencyPolicy("none", None, "Everything looks fine. No service needed.", False, False),
    "MONITOR": UrgencyPolicy("suggest", 21, "Nothing urgent. A check in the next few weeks would be sensible.", True, False),
    "WARNING": UrgencyPolicy("soon", 7, "This needs a look within the week.", False, False),
    "CRITICAL": UrgencyPolicy("urgent", 2, "This is urgent. Book the soonest slot and avoid driving until it is checked.", False, True),
    "NOT_ASSESSED": UrgencyPolicy("none", None, "Not enough sensor data to assess the vehicle.", False, False),
}


FAULT_EXPLANATIONS = {
    "motor_bearing_wear": {
        "what": "The bearings that let the motor spin smoothly are wearing out.",
        "signs": "Vibration from the motor is rising, which you may feel or hear as a hum or rumble.",
        "risk": "If ignored, the bearing can seize and the motor can be damaged, which is far more expensive to fix.",
        "fix": "A technician inspects the motor and usually replaces the bearing. Typically a few hours of work.",
        "until_then": "Drive gently and avoid long high-speed trips.",
    },
    "motor_overheating": {
        "what": "The motor is running hotter than it should.",
        "signs": "Winding temperature keeps climbing under normal driving.",
        "risk": "Sustained heat ages the motor insulation and can lead to failure.",
        "fix": "Check coolant flow, fans and the motor itself.",
        "until_then": "Avoid heavy loads, steep climbs and very hot midday drives.",
    },
    "battery_degradation": {
        "what": "The battery is holding less charge than it used to.",
        "signs": "State of health is trending down faster than normal ageing.",
        "risk": "Range keeps shrinking. Near the warranty line it may qualify for a claim.",
        "fix": "A battery health test, and a warranty check if it is close to the line.",
        "until_then": "Prefer slow charging and avoid keeping it at 100% for long.",
    },
    "cell_imbalance": {
        "what": "Some cells in the battery pack are drifting apart in voltage.",
        "signs": "The gap between the highest and lowest cell voltage is growing.",
        "risk": "Usable range drops, and a large imbalance can stress the weaker cells.",
        "fix": "A balancing service and a check of the battery management system.",
        "until_then": "Normal driving is fine. An occasional full, slow charge can help.",
    },
    "battery_overheating": {
        "what": "The battery pack is getting too hot.",
        "signs": "Pack temperature is outside its safe range.",
        "risk": "Overheating batteries are a safety risk.",
        "fix": "Inspect the battery cooling system and the pack.",
        "until_then": "Stop fast charging. If it gets worse, stop driving.",
    },
    "inverter_fault": {
        "what": "The power electronics that drive the motor are showing signs of stress.",
        "signs": "Inverter temperature or current is outside its normal range.",
        "risk": "An inverter failure can leave the car unable to drive.",
        "fix": "Inspect the inverter, its cooling and connections.",
        "until_then": "Avoid hard acceleration.",
    },
}


def explain(fault: str | None) -> dict:
    if not fault or fault == "normal":
        return {"what": "No fault detected in the available data."}
    return FAULT_EXPLANATIONS.get(fault, {"what": f"Detected: {fault.replace('_', ' ')}."})


def cycles_to_days(cycles: float | None, cycles_per_day: float) -> float | None:
    if cycles is None or cycles_per_day <= 0:
        return None
    return cycles / cycles_per_day


def booking_window(health: dict, rul: dict, cycles_per_day: float, now: datetime) -> dict:
    """Decide how soon the vehicle should be serviced.

    The deadline is the tighter of the status policy and the *low* end of the RUL
    range converted to days (with one day of margin). Using the low end keeps the
    plan conservative: the agent books before the pessimistic estimate, not the
    average one.
    """
    policy = POLICY.get(health["status"], POLICY["NOT_ASSESSED"])
    if policy.level == "none":
        return {"urgency": "none", "tone": policy.tone, "deadline": None, "basis": "status"}

    days = policy.max_days
    basis = "status"
    if rul.get("rul_status") == "threshold_reached":
        days, basis = 1, "threshold reached"
    elif rul.get("rul_status") == "estimating":
        low_days = cycles_to_days(rul["rul_low"], cycles_per_day)
        if low_days is not None and low_days - 1 < days:
            days, basis = max(1.0, low_days - 1), "remaining useful life (low end)"

    deadline = now + timedelta(days=days)
    if policy.level == "urgent":
        # Always leave room for at least the rest of today and all of tomorrow.
        end_of_tomorrow = (now + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)
        deadline = max(deadline, end_of_tomorrow)

    return {
        "urgency": policy.level,
        "tone": policy.tone,
        "deadline": deadline.isoformat(),
        "days_available": round((deadline - now).total_seconds() / 86400, 1),
        "basis": basis,
        "prefer_weekends": policy.prefer_weekends,
        "allow_emergency": policy.allow_emergency,
    }


def conflicts(calendar: CalendarProvider, slot: Slot) -> list[str]:
    """Calendar events that overlap the slot plus travel time."""
    return [f"{e.title} {e.start:%H:%M}-{e.end:%H:%M}"
            for e in calendar.busy(slot.start - TRAVEL_BUFFER, slot.end + TRAVEL_BUFFER)]


def match_slots(calendar: CalendarProvider, centre: MockServiceCentre, now: datetime,
                window: dict, limit: int = 3) -> list[dict]:
    """Service-centre slots inside the window where the user is free (including travel time).

    For urgent cases, if nothing fits the calendar, the soonest slots are returned
    anyway with their calendar conflicts listed: safety beats convenience, but the
    user still decides.
    """
    if window["urgency"] == "none":
        return []
    start = now + LEAD_TIME
    end = datetime.fromisoformat(window["deadline"])
    candidates = centre.available(start, end, include_emergency=window["allow_emergency"])
    fits = [s for s in candidates if not conflicts(calendar, s)]

    if window["prefer_weekends"]:
        fits.sort(key=lambda s: (s.start.weekday() < 5, s.start))
    else:
        fits.sort(key=lambda s: s.start)       # soonest first

    if not fits and window["urgency"] == "urgent":
        return [{**s.to_dict(), "calendar_conflicts": conflicts(calendar, s)} for s in candidates[:limit]]
    return [{**s.to_dict(), "calendar_conflicts": []} for s in fits[:limit]]
