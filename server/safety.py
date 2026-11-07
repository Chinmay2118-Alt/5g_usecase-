"""Robot-side safety state machine.

This is the part that would run on the robot computer in the real project.
It is pure logic with an injected clock so it can be unit-tested exactly.

Rules (from the project document, Safety design):
  * Watchdog: no valid command for WATCHDOG_S -> HOLDING.
  * Out-of-order / duplicate sequence numbers are rejected.
  * Motion only while ARMED *and* the clutch is held.
  * HOLDING / DISARMED -> ARMED only by an explicit arm with the clutch released.
  * A stop from any source (operator, dashboard, AI) -> HOLDING.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import ARMED, DISARMED, HOLDING, Command

# 180 ms timeout + a 10 ms check tick keeps the worst case under the 200 ms target.
WATCHDOG_S = 0.180


@dataclass
class Decision:
    accepted: bool
    reason: str  # "ok" | "out_of_order" | "not_armed" | "clutch_off"
    motion: bool


@dataclass
class Safety:
    watchdog_s: float = WATCHDOG_S
    state: str = DISARMED
    reason: str = "startup"
    last_seq: int = -1
    last_valid_t: float | None = None
    last_clutch: bool = False
    hold_latency_ms: float | None = None  # last-command -> HOLD time of the latest watchdog trip
    events: list = field(default_factory=list)  # (t, event, detail) drained by the server

    # ---- inputs -------------------------------------------------------
    def new_session(self, now: float) -> None:
        """A (re)connected operator starts its own sequence numbers."""
        self.last_seq = -1
        self.last_clutch = False
        if self.state == ARMED:
            self._hold(now, "operator_reconnected")

    def on_command(self, cmd: Command, now: float) -> Decision:
        if cmd.seq <= self.last_seq:
            return Decision(False, "out_of_order", False)
        self.last_seq = cmd.seq
        self.last_valid_t = now
        self.last_clutch = cmd.clutch
        if self.state != ARMED:
            return Decision(True, "not_armed", False)
        if not cmd.clutch:
            return Decision(True, "clutch_off", False)
        return Decision(True, "ok", True)

    def arm(self, now: float) -> tuple[bool, str]:
        if self.state == ARMED:
            return True, "already_armed"
        if self.last_clutch:
            return False, "release_clutch_first"
        if self.last_valid_t is None or now - self.last_valid_t > self.watchdog_s:
            return False, "no_live_commands"
        self._set(now, ARMED, "armed")
        self.hold_latency_ms = None
        return True, "armed"

    def disarm(self, now: float, source: str = "operator") -> None:
        if self.state != DISARMED:
            self._set(now, DISARMED, f"disarmed_by_{source}")

    def stop(self, now: float, source: str) -> None:
        if self.state == ARMED:
            self._hold(now, f"stop_{source}")

    def extend_watchdog(self, seconds: float) -> None:
        """Grace period when the injected delay is raised, so the gap it creates
        in command arrivals is not mistaken for a lost link."""
        if self.last_valid_t is not None and seconds > 0:
            self.last_valid_t += seconds

    # ---- periodic -----------------------------------------------------
    def tick(self, now: float) -> None:
        if self.state != ARMED:
            return
        if self.last_valid_t is None or now - self.last_valid_t > self.watchdog_s:
            self.hold_latency_ms = (
                None if self.last_valid_t is None else (now - self.last_valid_t) * 1000.0
            )
            self._hold(now, "watchdog")

    def motion_allowed(self, cmd: Command | None) -> bool:
        return self.state == ARMED and cmd is not None and cmd.clutch

    # ---- internals ----------------------------------------------------
    def _hold(self, now: float, reason: str) -> None:
        self._set(now, HOLDING, reason)

    def _set(self, now: float, state: str, reason: str) -> None:
        self.state = state
        self.reason = reason
        self.events.append((now, "state", f"{state}:{reason}"))
