"""Demo: a commuter EV with a wearing motor bearing, from HEALTHY to CRITICAL.

Plays the agent's side of the conversation using the same tool functions the
MCP server exposes. No LLM or API key needed.

    python demo/run_demo.py                # scripted answers
    python demo/run_demo.py --interactive  # you answer the yes/no questions
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prognos_mcp.tools import PrognosAssistant  # noqa: E402

VEHICLE = "EV-DEMO-01"
START = datetime(2026, 11, 23, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))   # a Monday morning

# (drive cycle to jump to, scripted answer to the booking question)
CHECKPOINTS = [(30, None), (75, "no"), (116, "no"), (136, "yes")]

BAR = {"HEALTHY": "🟢", "MONITOR": "🟡", "WARNING": "🟠", "CRITICAL": "🔴"}


def say(who: str, text: str) -> None:
    prefix = f"{who:>7} │ "
    for i, line in enumerate(textwrap.wrap(text, 88) or [""]):
        print((prefix if i == 0 else " " * 8 + "│ ") + line)


def tool(name: str) -> None:
    print(f"        │   ↳ tool: {name}")


def ask(question: str, scripted: str, interactive: bool) -> bool:
    say("agent", question)
    answer = input("    you │ ").strip().lower() if interactive else scripted
    if not interactive:
        say("you", answer)
    return answer in {"y", "yes", "ok", "sure", "book it"}


def fmt(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a %d %b, %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactive", action="store_true")
    args = ap.parse_args()

    a = PrognosAssistant(now=START)
    profile = a.engine.profile(VEHICLE)
    print(f"\nPrognos MCP demo · {profile.nickname} ({profile.model}) · mock engine\n" + "─" * 100)

    last_cycle = profile.start_cycle
    for cycle, scripted in CHECKPOINTS:
        # Move the clock forward in line with how much the owner drives.
        days = (cycle - last_cycle) / profile.cycles_per_day
        a.set_now(a.now() + timedelta(days=days))
        a.engine.set_cycle(VEHICLE, cycle)
        last_cycle = cycle

        print(f"\n[{a.now():%a %d %b %Y}] drive cycle {cycle}")
        tool("get_vehicle_health")
        h = a.get_vehicle_health(VEHICLE)
        print(f"        │   {BAR[h['status']]} score {h['health_score']} · {h['status']} · urgency {h['urgency']}")

        if h["urgency"] == "none":
            say("agent", "Your car is in good shape. Nothing to do.")
            continue

        tool("explain_fault")
        e = a.explain_fault(VEHICLE)["explanation"]
        tool("get_remaining_use_time")
        r = a.get_remaining_use_time(VEHICLE)
        tool("find_free_slots")
        f = a.find_free_slots(VEHICLE)

        if h["urgency"] == "urgent":
            say("agent", f"⚠️  {h['action']} {e['what']} {e['risk']} {r['summary']}")
        else:
            say("agent", f"{f['window']['tone']} {e['what']} {e['signs']} {r['summary']}")

        if not f["slots"]:
            say("agent", f["message"])
            continue
        say("agent", f"Service window: before {fmt(f['window']['deadline'])} "
                     f"(based on {f['window']['basis']}). Times that work with your calendar:")
        for i, s in enumerate(f["slots"], 1):
            tag = " (emergency bay)" if s["emergency"] else ""
            if s["calendar_conflicts"]:
                tag += f" · clashes with {', '.join(s['calendar_conflicts'])}"
            print(f"        │   {i}. {fmt(s['start'])}{tag}")

        pick = f["slots"][0]
        tool("propose_booking")
        p = a.propose_booking(VEHICLE, pick["slot_id"])
        if not p["ok"]:
            say("agent", "Couldn't propose that slot: " + "; ".join(p["problems"]))
            continue
        yes = ask(p["ask_user"], scripted, args.interactive)
        tool("confirm_booking")
        c = a.confirm_booking(p["proposal"]["proposal_id"], user_confirmed=yes)
        if c.get("booked"):
            b = c["booking"]
            say("agent", f"Booked ✔ {b['booking_ref']} on {fmt(b['start'])}. Added to your calendar "
                         f"with 30 minutes travel either side. {e['fix']}")
            break
        say("agent", "No problem, I won't book anything. I'll keep watching and check again.")

    print("\n" + "─" * 100)
    tool("get_audit_log")
    for entry in a.get_audit_log()["entries"]:
        extra = f" · {entry['booking_ref']}" if "booking_ref" in entry else ""
        print(f"        │   {fmt(entry['at'])} · {entry['action']:<8} · {entry['slot_id']}{extra}")
    print()


if __name__ == "__main__":
    main()
