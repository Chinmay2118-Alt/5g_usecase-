"""Kinematic simulation of a tabletop arm, three cubes and a bin.

Frame (shared with the WebXR client): metres, Y up, robot base at the origin on
the table top, +Z points from the robot towards the operator, +X is to the
operator's right. The client renders these coordinates as they are.

The arm is a base yaw + 2-link planar arm + a wrist that keeps the gripper
pointing straight down, solved analytically each step.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Geometry
SHOULDER_H = 0.10   # shoulder pivot height above the table
L1 = 0.30           # upper arm
L2 = 0.30           # forearm
L3 = 0.08           # wrist to gripper tip
CUBE = 0.04         # cube edge
HALF = CUBE / 2

# Safety limits enforced here, on the "robot" side
VMAX = 0.20                                   # m/s at full stick
WORKSPACE = {"x": (-0.30, 0.30), "y": (0.02, 0.30), "z": (0.12, 0.42)}
GRASP_RADIUS = 0.04

BIN = {"x": 0.20, "z": 0.26, "half": 0.065, "wall": 0.07}
HOME = (0.0, 0.20, 0.25)
CUBE_START = [
    ("red", (-0.16, HALF, 0.22)),
    ("green", (-0.02, HALF, 0.34)),
    ("blue", (-0.10, HALF, 0.16)),
]
TARGET_SCORE = 2
GRAVITY = 9.81


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def ik(x: float, y: float, z: float) -> list[float]:
    """Joint angles [yaw, shoulder, elbow, wrist] for a gripper tip at (x, y, z).

    shoulder = elevation of the upper arm above horizontal (rad)
    elbow    = bend of the forearm relative to the upper arm (rad, >0 bends down)
    wrist    = rotation that makes the gripper point straight down
    """
    yaw = math.atan2(x, z)
    r = math.hypot(x, z)
    h = y + L3 - SHOULDER_H           # wrist sits L3 above the tip
    d = math.hypot(r, h)
    d = clamp(d, 1e-4, L1 + L2 - 1e-4)
    cos_e = clamp((d * d - L1 * L1 - L2 * L2) / (2 * L1 * L2), -1.0, 1.0)
    elbow = math.acos(cos_e)
    shoulder = math.atan2(h, r) + math.atan2(L2 * math.sin(elbow), L1 + L2 * math.cos(elbow))
    wrist = math.pi / 2 + (shoulder - elbow)
    return [yaw, shoulder, elbow, wrist]


def fk(j: list[float]) -> tuple[float, float, float]:
    """Gripper tip position for joint angles from ik() (used by tests)."""
    yaw, sh, el, _ = j
    r = L1 * math.cos(sh) + L2 * math.cos(sh - el)
    h = SHOULDER_H + L1 * math.sin(sh) + L2 * math.sin(sh - el) - L3
    return (r * math.sin(yaw), h, r * math.cos(yaw))


@dataclass
class Cube:
    id: int
    color: str
    pos: list[float]
    vy: float = 0.0
    held: bool = False
    falling: bool = False
    in_bin: bool = False


def over_bin(x: float, z: float) -> bool:
    return abs(x - BIN["x"]) < BIN["half"] - HALF * 0.5 and abs(z - BIN["z"]) < BIN["half"] - HALF * 0.5


@dataclass
class Sim:
    tip: list[float] = field(default_factory=lambda: list(HOME))
    grip_closed: bool = False
    grip_open_amount: float = 1.0      # 1 open .. 0 closed, animated
    cubes: list[Cube] = field(default_factory=list)
    held: int | None = None
    score: int = 0
    t: float = 0.0
    task_start: float | None = None
    task_time: float | None = None
    intruder: bool = False
    events: list = field(default_factory=list)  # (sim_t, event, detail)

    def __post_init__(self) -> None:
        if not self.cubes:
            self.reset()

    def reset(self) -> None:
        self.tip = list(HOME)
        self.grip_closed = False
        self.grip_open_amount = 1.0
        self.cubes = [Cube(i, c, list(p)) for i, (c, p) in enumerate(CUBE_START)]
        self.held = None
        self.score = 0
        self.task_start = None
        self.task_time = None
        self.events.append((self.t, "reset", ""))

    # ------------------------------------------------------------------
    def step(self, dt: float, vel: tuple[float, float, float] | None, gripper: bool | None) -> None:
        """Advance dt seconds. vel/gripper are None when motion is not allowed:
        the arm then holds its pose and the gripper keeps its state."""
        self.t += dt
        if vel is not None:
            before = tuple(self.tip)
            for i, axis in enumerate("xyz"):
                lo, hi = WORKSPACE[axis]
                self.tip[i] = clamp(self.tip[i] + clamp(vel[i], -1, 1) * VMAX * dt, lo, hi)
            if self.task_start is None and self.task_time is None and before != tuple(self.tip):
                self.task_start = self.t
                self.events.append((self.t, "task_start", ""))
        if gripper is not None and gripper != self.grip_closed:
            self.grip_closed = gripper
            if gripper:
                self._try_grasp()
            else:
                self._release()

        target = 0.0 if self.grip_closed else 1.0
        rate = 5.0 * dt
        self.grip_open_amount += clamp(target - self.grip_open_amount, -rate, rate)

        for c in self.cubes:
            if c.held:
                c.pos = [self.tip[0], max(HALF, self.tip[1] - HALF + 0.005), self.tip[2]]
            elif c.falling:
                c.vy -= GRAVITY * dt
                c.pos[1] += c.vy * dt
                floor = HALF + 0.006 if over_bin(c.pos[0], c.pos[2]) else HALF
                if c.pos[1] <= floor:
                    c.pos[1] = floor
                    c.vy = 0.0
                    c.falling = False
                    self._landed(c)

    def _try_grasp(self) -> None:
        if self.held is not None:
            return
        best, best_d = None, GRASP_RADIUS
        for c in self.cubes:
            if c.in_bin or c.falling:
                continue
            d = math.dist(self.tip, [c.pos[0], c.pos[1] + HALF - 0.005, c.pos[2]])
            if d < best_d:
                best, best_d = c, d
        if best is not None:
            best.held = True
            self.held = best.id
            self.events.append((self.t, "grasp", best.color))

    def _release(self) -> None:
        if self.held is None:
            return
        c = self.cubes[self.held]
        c.held = False
        c.falling = True
        c.vy = 0.0
        self.held = None
        self.events.append((self.t, "release", c.color))

    def _landed(self, c: Cube) -> None:
        if over_bin(c.pos[0], c.pos[2]) and not c.in_bin:
            c.in_bin = True
            self.score += 1
            self.events.append((self.t, "scored", c.color))
            if self.score >= TARGET_SCORE and self.task_time is None and self.task_start is not None:
                self.task_time = self.t - self.task_start
                self.events.append((self.t, "task_complete", f"{self.task_time:.2f}"))
        else:
            self.events.append((self.t, "dropped", c.color))

    # ------------------------------------------------------------------
    def detections(self, rng: random.Random) -> list[dict]:
        """Simulated edge-AI output: ground truth + noise. Clearly a stand-in
        for YOLO/ArUco on the MEC (see README)."""
        out = []
        for c in self.cubes:
            out.append({
                "label": f"{c.color} cube",
                "pos": [round(p + rng.gauss(0, 0.003), 4) for p in c.pos],
                "conf": round(clamp(rng.gauss(0.92, 0.03), 0, 0.99), 2),
            })
        if self.intruder:
            out.append({"label": "person", "pos": [0.0, 0.15, 0.30], "conf": 0.88})
        return out

    def snapshot(self) -> dict:
        elapsed = None
        if self.task_time is not None:
            elapsed = self.task_time
        elif self.task_start is not None:
            elapsed = self.t - self.task_start
        return {
            "tip": [round(v, 4) for v in self.tip],
            "joints": [round(a, 4) for a in ik(*self.tip)],
            "grip": round(self.grip_open_amount, 3),
            "cubes": [
                {"id": c.id, "color": c.color, "pos": [round(v, 4) for v in c.pos],
                 "held": c.held, "in_bin": c.in_bin}
                for c in self.cubes
            ],
            "score": self.score,
            "target": TARGET_SCORE,
            "task_s": None if elapsed is None else round(elapsed, 2),
            "done": self.task_time is not None,
            "intruder": self.intruder,
        }


GEOMETRY = {
    "shoulder_h": SHOULDER_H, "l1": L1, "l2": L2, "l3": L3, "cube": CUBE,
    "bin": BIN, "workspace": WORKSPACE,
}
