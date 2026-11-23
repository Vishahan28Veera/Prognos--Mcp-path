"""Tool implementations shared by the MCP server and the demo script.

Every function returns plain JSON-serialisable dicts so an LLM can read them.

Safety model ("less magic, more utility"):
  * Read tools (health, RUL, explain, free slots) have no side effects.
  * `propose_booking` creates a short-lived proposal and changes nothing.
  * `confirm_booking` is the only tool that writes. It needs a live proposal
    id and an explicit `user_confirmed=True`, and it re-checks that the slot
    is still free and still inside the safe window before booking.
  * Every write is recorded in an audit log the user can read back.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import planner
from .calendar_mock import CalendarProvider, MockCalendar
from .mock_engine import MockPrognosEngine
from .service_centre import MockServiceCentre

PROPOSAL_TTL = timedelta(minutes=15)


class PrognosAssistant:
    def __init__(self, engine: MockPrognosEngine | None = None, calendar: CalendarProvider | None = None,
                 centre: MockServiceCentre | None = None, now: datetime | None = None,
                 tz: str = "Asia/Kolkata"):
        self.tz = ZoneInfo(tz)
        self._fixed_now = now.astimezone(self.tz) if now else None
        base = self.now()
        self.engine = engine or MockPrognosEngine()
        self.calendar = calendar or MockCalendar(base, tz=tz)
        self.centre = centre or MockServiceCentre(base, tz=tz)
        self.proposals: dict[str, dict] = {}
        self.audit: list[dict] = []

    def now(self) -> datetime:
        return self._fixed_now or datetime.now(self.tz)

    def set_now(self, now: datetime) -> None:
        """Demo and tests: pin the clock."""
        self._fixed_now = now.astimezone(self.tz)

    # ------------------------------------------------------------------ read

    def list_vehicles(self) -> dict:
        return {"vehicles": self.engine.list_vehicles()}

    def get_vehicle_health(self, vehicle_id: str) -> dict:
        h = self.engine.assess(vehicle_id)
        h["urgency"] = planner.POLICY[h["status"]].level
        return h

    def get_remaining_use_time(self, vehicle_id: str) -> dict:
        rul = self.engine.remaining_useful_life(vehicle_id)
        cpd = self.engine.profile(vehicle_id).cycles_per_day
        out = dict(rul)
        out["owner_drive_cycles_per_day"] = cpd
        if rul["rul_status"] == "estimating":
            out["days_estimate"] = round(planner.cycles_to_days(rul["rul_estimate"], cpd), 1)
            out["days_low"] = round(planner.cycles_to_days(rul["rul_low"], cpd), 1)
            out["days_high"] = round(planner.cycles_to_days(rul["rul_high"], cpd), 1)
            out["summary"] = (f"About {rul['rul_estimate']} drive cycles left "
                              f"({rul['rul_low']}-{rul['rul_high']}), roughly {out['days_low']:.0f}-"
                              f"{out['days_high']:.0f} days at your usual driving.")
        else:
            out["summary"] = {
                "before_onset": "No confirmed fault, so there is nothing to count down.",
                "collecting": "Fault confirmed. Gathering a few more drives before estimating time left.",
                "no_trend": "The signal is not moving toward its limit right now.",
                "threshold_reached": "The limit has been reached. Service is needed now.",
            }[rul["rul_status"]]
        out["note"] = "Days are a conversion from drive cycles at this owner's average usage, not a prediction in days."
        return out

    def explain_fault(self, vehicle_id: str) -> dict:
        h = self.engine.assess(vehicle_id)
        fault = h["confirmed_fault"] or (h["top_hypothesis"] if h["top_hypothesis"] != "normal" else None)
        return {"vehicle_id": vehicle_id, "fault": fault, "confirmed": bool(h["confirmed_fault"]),
                "status": h["status"], "explanation": planner.explain(fault), "evidence": h["reasons"]}

    def find_free_slots(self, vehicle_id: str, limit: int = 3) -> dict:
        h = self.engine.assess(vehicle_id)
        rul = self.engine.remaining_useful_life(vehicle_id)
        cpd = self.engine.profile(vehicle_id).cycles_per_day
        window = planner.booking_window(h, rul, cpd, self.now())
        slots = planner.match_slots(self.calendar, self.centre, self.now(), window, limit=limit)
        return {
            "vehicle_id": vehicle_id,
            "status": h["status"],
            "window": window,
            "slots": slots,
            "message": window["tone"] if slots or window["urgency"] == "none"
            else "No slot fits both your calendar and the safe window. Ask the user which event could move.",
        }

    # ----------------------------------------------------------------- write

    def propose_booking(self, vehicle_id: str, slot_id: str) -> dict:
        h = self.engine.assess(vehicle_id)
        rul = self.engine.remaining_useful_life(vehicle_id)
        window = planner.booking_window(h, rul, self.engine.profile(vehicle_id).cycles_per_day, self.now())
        slot = self.centre.get(slot_id)
        problems = self._check_slot(slot, window)
        if problems:
            return {"ok": False, "problems": problems}
        clashes = planner.conflicts(self.calendar, slot)

        pid = "P-" + uuid.uuid4().hex[:8]
        fault = h["confirmed_fault"] or h["top_hypothesis"]
        proposal = {
            "proposal_id": pid,
            "vehicle_id": vehicle_id,
            "slot": slot.to_dict(),
            "reason": f"{h['status']}: {fault.replace('_', ' ') if fault and fault != 'normal' else 'check-up'}",
            "calendar_conflicts": clashes,
            "expires_at": (self.now() + PROPOSAL_TTL).isoformat(),
            "state": "pending",
        }
        self.proposals[pid] = proposal
        return {"ok": True, "proposal": proposal,
                "ask_user": f"Book {slot.centre} on {slot.start:%a %d %b at %H:%M} for your "
                            f"{self.engine.profile(vehicle_id).nickname.lower()}?"
                            + (f" It clashes with: {', '.join(clashes)}." if clashes else "") + " (yes/no)"}

    def confirm_booking(self, proposal_id: str, user_confirmed: bool) -> dict:
        p = self.proposals.get(proposal_id)
        if not p:
            return {"ok": False, "problems": ["Unknown proposal. Call propose_booking first."]}
        if p["state"] != "pending":
            return {"ok": False, "problems": [f"Proposal is already {p['state']}."]}
        if self.now() > datetime.fromisoformat(p["expires_at"]):
            p["state"] = "expired"
            return {"ok": False, "problems": ["Proposal expired. Find slots again."]}
        if not user_confirmed:
            p["state"] = "declined"
            self._log("declined", p)
            return {"ok": True, "booked": False, "message": "Okay, nothing was booked."}

        slot = self.centre.get(p["slot"]["slot_id"])
        h = self.engine.assess(p["vehicle_id"])
        rul = self.engine.remaining_useful_life(p["vehicle_id"])
        window = planner.booking_window(h, rul, self.engine.profile(p["vehicle_id"]).cycles_per_day, self.now())
        problems = self._check_slot(slot, window)
        if problems:
            p["state"] = "stale"
            return {"ok": False, "problems": problems + ["Find slots again."]}

        booking = self.centre.book(slot.slot_id, p["vehicle_id"], p["reason"])
        event = self.calendar.create_event(
            f"EV service: {p['reason']}", slot.start - planner.TRAVEL_BUFFER, slot.end + planner.TRAVEL_BUFFER,
            description=f"Booking {booking['booking_ref']} at {slot.centre}")
        p["state"] = "booked"
        self._log("booked", p, booking_ref=booking["booking_ref"])
        return {"ok": True, "booked": True, "booking": booking, "calendar_event": event.to_dict()}

    def get_audit_log(self) -> dict:
        return {"entries": self.audit}

    # ------------------------------------------------------------------ demo

    def simulate_drive(self, vehicle_id: str, cycles: int) -> dict:
        """Demo only: advance a mock vehicle by some drive cycles."""
        c = self.engine.drive(vehicle_id, cycles)
        return {"vehicle_id": vehicle_id, "cycle": c}

    # --------------------------------------------------------------- helpers

    def _check_slot(self, slot, window: dict) -> list[str]:
        problems = []
        if window["urgency"] == "none":
            problems.append("The vehicle does not currently need a service.")
            return problems
        if slot.booked_by is not None:
            problems.append("That slot is no longer available.")
        if slot.start < self.now() + planner.LEAD_TIME:
            problems.append("That slot starts too soon to reach.")
        if window["deadline"] and slot.end > datetime.fromisoformat(window["deadline"]):
            problems.append("That slot is after the safe service window.")
        if slot.emergency and not window["allow_emergency"]:
            problems.append("Emergency bays are reserved for critical vehicles.")
        # Calendar clashes block non-urgent bookings. For urgent ones the clash is
        # shown to the user in the proposal and they decide.
        if window["urgency"] != "urgent" and planner.conflicts(self.calendar, slot):
            problems.append("You have something on your calendar at that time.")
        return problems

    def _log(self, action: str, proposal: dict, **extra) -> None:
        self.audit.append({"at": self.now().isoformat(), "action": action,
                           "proposal_id": proposal["proposal_id"], "vehicle_id": proposal["vehicle_id"],
                           "slot_id": proposal["slot"]["slot_id"], **extra})
