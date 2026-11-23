"""Mock Prognos engine.

This module stands in for the real Prognos backend (fault classifier, health
score engine and RUL estimator), which is the team's proprietary IP and is
NOT part of this repository.

The mock exposes the same *shape* of output the real backend gives a client:
a 0-100 health score, a status band, per-subsystem scores, a fault hypothesis
or confirmed fault, and a remaining-useful-life (RUL) estimate in drive
cycles with a range. The numbers come from hand-written scenario curves, not
from any model or scoring formula. Nothing here reflects how the real engine
computes its results.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

ENGINE_VERSION = "mock-0.1"

# Public status bands shown to users. The agent's urgency policy keys off these.
STATUS_BANDS = [
    (80, "HEALTHY", "No action needed."),
    (60, "MONITOR", "Book a check at your next convenient service."),
    (40, "WARNING", "Get it inspected soon. Avoid fast charging and hard acceleration."),
    (0, "CRITICAL", "Stop driving when safe and contact service."),
]

SUBSYSTEMS = ("battery", "motor", "power_electronics")

FAULT_SUBSYSTEM = {
    "battery_degradation": "battery",
    "cell_imbalance": "battery",
    "battery_overheating": "battery",
    "motor_bearing_wear": "motor",
    "motor_overheating": "motor",
    "inverter_fault": "power_electronics",
}


@dataclass
class Scenario:
    """A scripted health trajectory for one demo vehicle."""

    vehicle_id: str
    nickname: str
    model: str
    cycles_per_day: float          # how much the owner drives; used to turn cycles into days
    start_cycle: int
    score_curve: list[tuple[int, float]]   # (cycle, overall score) keypoints
    fault: str | None = None
    advisory_cycle: int | None = None      # model starts to suspect the fault
    confirm_cycle: int | None = None       # fault confirmed
    threshold_cycle: int | None = None     # key signal reaches its threshold line
    key_signal: str | None = None
    key_signal_curve: list[tuple[int, float]] = field(default_factory=list)
    key_signal_unit: str = ""
    seed: int = 7


def _interp(curve: list[tuple[int, float]], x: float) -> float:
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return curve[-1][1]


def status_for(score: float) -> tuple[str, str]:
    for floor, status, action in STATUS_BANDS:
        if score >= floor:
            return status, action
    return STATUS_BANDS[-1][1], STATUS_BANDS[-1][2]


DEMO_SCENARIOS: dict[str, Scenario] = {
    # A daily commuter whose motor bearing is wearing out. Goes HEALTHY ->
    # MONITOR -> WARNING -> CRITICAL over ~120 drive cycles.
    "EV-DEMO-01": Scenario(
        vehicle_id="EV-DEMO-01",
        nickname="Daily commuter",
        model="Compact EV hatchback",
        cycles_per_day=2.0,
        start_cycle=30,
        score_curve=[(0, 95), (40, 93), (55, 88), (70, 76), (95, 66), (115, 52), (130, 41), (140, 33), (160, 24)],
        fault="motor_bearing_wear",
        advisory_cycle=45,
        confirm_cycle=52,
        threshold_cycle=140,
        key_signal="motor_vibration",
        key_signal_curve=[(0, 0.9), (45, 1.3), (70, 2.0), (115, 3.4), (140, 4.5), (160, 5.4)],
        key_signal_unit="mm/s RMS",
        seed=11,
    ),
    # A healthy vehicle. The agent should stay quiet.
    "EV-DEMO-02": Scenario(
        vehicle_id="EV-DEMO-02",
        nickname="Weekend car",
        model="Mid-size EV SUV",
        cycles_per_day=0.6,
        start_cycle=80,
        score_curve=[(0, 96), (300, 93)],
        seed=23,
    ),
    # Slow cell imbalance. Sits in MONITOR for a long time: a gentle nudge, not an alarm.
    "EV-DEMO-03": Scenario(
        vehicle_id="EV-DEMO-03",
        nickname="Family runabout",
        model="Compact EV sedan",
        cycles_per_day=1.5,
        start_cycle=120,
        score_curve=[(0, 94), (60, 86), (120, 75), (200, 66), (260, 58)],
        fault="cell_imbalance",
        advisory_cycle=80,
        confirm_cycle=90,
        threshold_cycle=260,
        key_signal="cell_voltage_spread_mv",
        key_signal_curve=[(0, 18), (80, 38), (120, 52), (200, 78), (260, 100)],
        key_signal_unit="mV",
        seed=31,
    ),
}


class MockPrognosEngine:
    """Same call surface as the real backend client, backed by scripted scenarios."""

    def __init__(self, scenarios: dict[str, Scenario] | None = None):
        self.scenarios = scenarios or DEMO_SCENARIOS
        self.cycle = {vid: s.start_cycle for vid, s in self.scenarios.items()}

    # ---- vehicle state ---------------------------------------------------

    def list_vehicles(self) -> list[dict]:
        return [
            {"vehicle_id": s.vehicle_id, "nickname": s.nickname, "model": s.model,
             "current_cycle": self.cycle[s.vehicle_id]}
            for s in self.scenarios.values()
        ]

    def profile(self, vehicle_id: str) -> Scenario:
        if vehicle_id not in self.scenarios:
            raise KeyError(f"Unknown vehicle '{vehicle_id}'. Known: {', '.join(self.scenarios)}")
        return self.scenarios[vehicle_id]

    def set_cycle(self, vehicle_id: str, cycle: int) -> None:
        self.profile(vehicle_id)
        self.cycle[vehicle_id] = max(0, int(cycle))

    def drive(self, vehicle_id: str, cycles: int = 1) -> int:
        self.profile(vehicle_id)
        self.cycle[vehicle_id] += max(0, int(cycles))
        return self.cycle[vehicle_id]

    # ---- health ----------------------------------------------------------

    def assess(self, vehicle_id: str) -> dict:
        s = self.profile(vehicle_id)
        c = self.cycle[vehicle_id]
        rng = random.Random(f"{s.seed}-{c}")

        score = round(max(0.0, min(100.0, _interp(s.score_curve, c) + rng.uniform(-1.0, 1.0))), 1)
        status, action = status_for(score)

        hypothesis, confirmed, reasons = "normal", None, []
        if s.fault and s.advisory_cycle is not None and c >= s.advisory_cycle:
            hypothesis = s.fault
            if s.confirm_cycle is not None and c >= s.confirm_cycle:
                confirmed = s.fault
            else:
                reasons.append(f"Possible {s.fault.replace('_', ' ')}: inspection advised (provisional).")

        # A confirmed fault never shows as HEALTHY.
        if confirmed and status == "HEALTHY":
            status, action = "MONITOR", STATUS_BANDS[1][2]

        threshold_reached = bool(s.threshold_cycle is not None and confirmed and c >= s.threshold_cycle)
        if threshold_reached:
            status, action = "CRITICAL", STATUS_BANDS[3][2]
            reasons.append(f"{s.key_signal} has reached its threshold line.")

        subsystems = {}
        affected = FAULT_SUBSYSTEM.get(s.fault) if s.fault else None
        for name in SUBSYSTEMS:
            if name == affected:
                sub = max(0.0, score - 6 + rng.uniform(-1, 1))
            else:
                sub = min(100.0, 93 + rng.uniform(-2, 3))
            subsystems[name] = {"score": round(sub, 1), "coverage": "full"}

        sensors = {}
        if s.key_signal:
            val = _interp(s.key_signal_curve, c) * (1 + rng.uniform(-0.03, 0.03))
            sensors[s.key_signal] = {"value": round(val, 2), "unit": s.key_signal_unit}
            if confirmed:
                reasons.append(f"{s.key_signal} is {val:.1f} {s.key_signal_unit} and rising.")

        return {
            "vehicle_id": s.vehicle_id,
            "nickname": s.nickname,
            "cycle": c,
            "health_score": score,
            "status": status,
            "action": action,
            "subsystems": subsystems,
            "top_hypothesis": hypothesis,
            "confirmed_fault": confirmed,
            "threshold_reached": threshold_reached,
            "key_sensors": sensors,
            "reasons": reasons,
            "data_source": "mock",
            "engine_version": ENGINE_VERSION,
        }

    # ---- remaining useful life ------------------------------------------

    def remaining_useful_life(self, vehicle_id: str) -> dict:
        """RUL in drive cycles. States mirror the real backend's public contract."""
        s = self.profile(vehicle_id)
        c = self.cycle[vehicle_id]
        base = {"vehicle_id": vehicle_id, "fault": None, "rul_unit": "cycles",
                "rul_estimate": None, "rul_low": None, "rul_high": None}

        if not s.fault or s.confirm_cycle is None or c < s.confirm_cycle:
            return {**base, "rul_status": "before_onset"}
        base["fault"] = s.fault
        if c >= s.threshold_cycle:
            return {**base, "rul_status": "threshold_reached", "rul_estimate": 0, "rul_low": 0, "rul_high": 0}
        if c < s.confirm_cycle + 8:
            return {**base, "rul_status": "collecting"}

        rng = random.Random(f"rul-{s.seed}-{c}")
        est = max(1, round((s.threshold_cycle - c) * (1 + rng.uniform(-0.08, 0.08))))
        # Illustrative range only. The real range is sized from validation errors.
        return {**base, "rul_status": "estimating", "rul_estimate": est,
                "rul_low": max(1, round(est * 0.8)), "rul_high": round(est * 1.5)}
