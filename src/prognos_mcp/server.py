"""Prognos MCP server.

Run with:  python -m prognos_mcp.server        (stdio transport)

Works with the MCP Python SDK 1.x (FastMCP) and 2.x (MCPServer).
All data is mock data: the real Prognos engine is not part of this repo.
"""

from __future__ import annotations

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .tools import PrognosAssistant

INSTRUCTIONS = """\
You help an EV owner keep their car healthy without thinking about it.
1. Check get_vehicle_health. If urgency is "none", say so briefly and stop.
2. Explain the problem in plain words with explain_fault. No jargon, no scare tactics.
3. Use get_remaining_use_time to say how long they have, in days and drive cycles.
4. Call find_free_slots. For "suggest" urgency, offer gently; for "urgent", lead with safety and the soonest slot.
5. Call propose_booking for the slot the user picks, show them the ask_user text, and wait.
6. Call confirm_booking with user_confirmed=true ONLY after the user clearly says yes in this conversation.
Never book without an explicit yes. Never invent slots or health numbers.
"""

mcp = _Server("prognos", instructions=INSTRUCTIONS)
assistant = PrognosAssistant()


@mcp.tool()
def list_vehicles() -> dict:
    """List the user's vehicles known to Prognos."""
    return assistant.list_vehicles()


@mcp.tool()
def get_vehicle_health(vehicle_id: str) -> dict:
    """Current health score (0-100), status band, subsystem scores, suspected or confirmed fault, and urgency."""
    return assistant.get_vehicle_health(vehicle_id)


@mcp.tool()
def get_remaining_use_time(vehicle_id: str) -> dict:
    """Remaining useful life before the confirmed fault reaches its limit, in drive cycles with a range, plus a days conversion."""
    return assistant.get_remaining_use_time(vehicle_id)


@mcp.tool()
def explain_fault(vehicle_id: str) -> dict:
    """Plain-language explanation of the current fault: what it is, signs, risk, the fix, and what to do until then."""
    return assistant.explain_fault(vehicle_id)


@mcp.tool()
def find_free_slots(vehicle_id: str, limit: int = 3) -> dict:
    """Service-centre slots inside the safe service window where the user's calendar is free. Read-only."""
    return assistant.find_free_slots(vehicle_id, limit)


@mcp.tool()
def propose_booking(vehicle_id: str, slot_id: str) -> dict:
    """Create a booking proposal for one slot. Books nothing. Returns a proposal_id and a question to ask the user."""
    return assistant.propose_booking(vehicle_id, slot_id)


@mcp.tool()
def confirm_booking(proposal_id: str, user_confirmed: bool) -> dict:
    """Book the proposed slot and add it to the calendar. Call only after the user explicitly says yes."""
    return assistant.confirm_booking(proposal_id, user_confirmed)


@mcp.tool()
def get_audit_log() -> dict:
    """Every booking or decline the agent has made, for the user to review."""
    return assistant.get_audit_log()


@mcp.tool()
def simulate_drive(vehicle_id: str, cycles: int = 10) -> dict:
    """DEMO ONLY: advance a mock vehicle by N drive cycles so its health changes."""
    return assistant.simulate_drive(vehicle_id, cycles)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
