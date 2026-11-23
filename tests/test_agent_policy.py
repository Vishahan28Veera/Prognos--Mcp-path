"""Safety and policy tests for the agent layer (mock engine)."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prognos_mcp.tools import PrognosAssistant  # noqa: E402

NOW = datetime(2026, 11, 23, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def make(cycle: int, vehicle: str = "EV-DEMO-01") -> PrognosAssistant:
    a = PrognosAssistant(now=NOW)
    a.engine.set_cycle(vehicle, cycle)
    return a


def test_healthy_vehicle_gets_no_slots():
    a = make(30)
    assert a.get_vehicle_health("EV-DEMO-01")["urgency"] == "none"
    assert a.find_free_slots("EV-DEMO-01")["slots"] == []


def test_confirmed_fault_is_never_healthy():
    a = make(53)
    h = a.get_vehicle_health("EV-DEMO-01")
    assert h["confirmed_fault"] == "motor_bearing_wear"
    assert h["status"] != "HEALTHY"


def test_no_rul_before_fault_is_confirmed():
    assert make(47).get_remaining_use_time("EV-DEMO-01")["rul_status"] == "before_onset"


def test_slots_respect_calendar_and_deadline():
    a = make(116)
    f = a.find_free_slots("EV-DEMO-01")
    deadline = datetime.fromisoformat(f["window"]["deadline"])
    assert f["slots"]
    for s in f["slots"]:
        start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
        assert end <= deadline
        assert a.calendar.is_free(start, end)


def test_critical_window_is_tight_and_allows_emergency_bay():
    a = make(136)
    w = a.find_free_slots("EV-DEMO-01")["window"]
    assert w["urgency"] == "urgent" and w["allow_emergency"] and w["days_available"] <= 2


def test_propose_books_nothing():
    a = make(116)
    slot = a.find_free_slots("EV-DEMO-01")["slots"][0]
    a.propose_booking("EV-DEMO-01", slot["slot_id"])
    assert a.centre.get(slot["slot_id"]).booked_by is None
    assert a.audit == []


def test_decline_books_nothing():
    a = make(116)
    slot = a.find_free_slots("EV-DEMO-01")["slots"][0]
    pid = a.propose_booking("EV-DEMO-01", slot["slot_id"])["proposal"]["proposal_id"]
    r = a.confirm_booking(pid, user_confirmed=False)
    assert r["booked"] is False and a.centre.get(slot["slot_id"]).booked_by is None


def test_confirm_books_once_and_adds_calendar_event():
    a = make(116)
    slot = a.find_free_slots("EV-DEMO-01")["slots"][0]
    pid = a.propose_booking("EV-DEMO-01", slot["slot_id"])["proposal"]["proposal_id"]
    assert a.confirm_booking(pid, user_confirmed=True)["booked"]
    assert any(e.source == "prognos-agent" for e in a.calendar.events)
    assert a.confirm_booking(pid, user_confirmed=True)["ok"] is False   # no double booking


def test_cannot_propose_for_healthy_vehicle_or_busy_time():
    a = make(30)
    any_slot = next(iter(a.centre.slots))
    assert a.propose_booking("EV-DEMO-01", any_slot)["ok"] is False


def test_emergency_bay_reserved_for_critical():
    a = make(116)
    bay = next(s for s in a.centre.slots.values() if s.emergency and s.start > NOW)
    r = a.propose_booking("EV-DEMO-01", bay.slot_id)
    assert r["ok"] is False
