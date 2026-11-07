"""Message formats shared by the server, the WebXR client and the tools.

All messages are JSON objects with a "type" field. See README.md (Interface)
for the full table. Validation is deliberately strict: anything malformed is
rejected rather than guessed at, because commands move the robot.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

# Robot states
DISARMED = "DISARMED"
ARMED = "ARMED"
HOLDING = "HOLDING"

OPERATOR_CONTROLS = {"arm", "disarm", "stop"}
DASHBOARD_CONTROLS = {
    "stop", "disarm", "set_delay_ms", "cut_link", "reset_task", "start_run",
    "set_scenario", "set_profile",
    # scenario-specific (routed to the world): arm / rescue disasters
    "intruder", "raise_water", "lower_water", "person_on_road", "hazard",
}
# Operator messages handled by the scenario world (rescue rover)
WORLD_MESSAGES = {"goto", "cancel_goto"}


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    seq: int
    t_op: float
    clutch: bool
    vx: float
    vy: float
    vz: float
    gripper: bool  # True = closed


def _num(d: dict, key: str, lo: float | None = None, hi: float | None = None) -> float:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ProtocolError(f"{key} must be a finite number")
    v = float(v)
    if lo is not None and v < lo or hi is not None and v > hi:
        raise ProtocolError(f"{key} out of range")
    return v


def _bool(d: dict, key: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise ProtocolError(f"{key} must be a boolean")
    return v


def parse(raw: str | bytes) -> dict:
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProtocolError(f"bad json: {e}") from e
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("message must be an object with a string 'type'")
    return msg


def parse_command(msg: dict) -> Command:
    seq = msg.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ProtocolError("seq must be a non-negative integer")
    return Command(
        seq=seq,
        t_op=_num(msg, "t_op"),
        clutch=_bool(msg, "clutch"),
        vx=_num(msg, "vx", -1.0, 1.0),
        vy=_num(msg, "vy", -1.0, 1.0),
        vz=_num(msg, "vz", -1.0, 1.0),
        gripper=_bool(msg, "gripper"),
    )


def parse_control(msg: dict, allowed: set[str]) -> dict:
    action = msg.get("action")
    if action not in allowed:
        raise ProtocolError(f"control action {action!r} not allowed here")
    out = {"action": action}
    if action == "set_delay_ms":
        out["value"] = _num(msg, "value", 0, 2000)
    elif action in ("cut_link", "intruder"):
        out["value"] = _bool(msg, "value")
    elif action == "start_run":
        pid = msg.get("participant", "")
        label = msg.get("label", "")
        if not isinstance(pid, str) or not isinstance(label, str):
            raise ProtocolError("participant/label must be strings")
        out["participant"] = "".join(c for c in pid if c.isalnum() or c in "-_")[:32]
        out["label"] = "".join(c for c in label if c.isalnum() or c in "-_ ")[:64]
    elif action in ("set_scenario", "set_profile", "hazard"):
        v = msg.get("value")
        if not isinstance(v, str) or not v or len(v) > 32 or not all(c.isalnum() or c == "_" for c in v):
            raise ProtocolError(f"{action} value must be a short identifier")
        out["value"] = v
    elif action == "stop":
        src = msg.get("source", "operator")
        out["source"] = src if src in ("operator", "dashboard", "ai") else "operator"
    return out


def parse_world_msg(msg: dict, bounds: float) -> dict:
    """goto {x, z} inside +-bounds metres, or cancel_goto."""
    t = msg["type"]
    if t == "goto":
        return {"type": t, "x": _num(msg, "x", -bounds, bounds), "z": _num(msg, "z", -bounds, bounds)}
    if t == "cancel_goto":
        return {"type": t}
    raise ProtocolError(f"unknown world message {t!r}")


def dumps(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))
