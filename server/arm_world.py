"""Adapter that plugs the tabletop-arm sim (server/sim.py) into the server's
world interface. See server/main.py (World) for the interface."""
from __future__ import annotations

import random

from .protocol import Command
from .sim import GEOMETRY, Sim

INTRUDER_S = 3.0


class ArmWorld:
    name = "arm"
    client_path = "/xr/"
    dashboard_path = "/dashboard/"
    bounds = 1.0

    def __init__(self) -> None:
        self.sim = Sim()
        self.intruder_until = 0.0

    @property
    def events(self) -> list:
        return self.sim.events

    def hello(self) -> dict:
        return {"geometry": GEOMETRY}

    def step(self, dt: float, armed: bool, cmd: Command | None) -> None:
        s = self.sim
        if s.intruder and s.t > self.intruder_until:
            s.intruder = False
            s.events.append((s.t, "intruder", "cleared"))
        if armed and cmd is not None and cmd.clutch:
            s.step(dt, (cmd.vx, cmd.vy, cmd.vz), cmd.gripper)
        else:
            s.step(dt, None, None)

    def hazard_reason(self) -> str | None:
        return "ai" if self.sim.intruder else None

    def layer(self):
        return None

    def detections(self, rng: random.Random) -> list[dict]:
        return self.sim.detections(rng)

    def snapshot(self) -> dict:
        return self.sim.snapshot()

    def reset(self) -> None:
        self.sim.reset()

    def operator_msg(self, msg: dict, armed: bool) -> str | None:
        return "not supported in the arm scenario"

    def dashboard_msg(self, c: dict) -> None:
        if c["action"] == "intruder":
            self.sim.intruder = c["value"]
            self.intruder_until = self.sim.t + (INTRUDER_S if c["value"] else 0.0)
            if c["value"]:
                self.sim.events.append((self.sim.t, "intruder", "person in workspace"))
