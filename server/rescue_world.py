"""Disaster-rescue scenarios: a 6-wheel skid-steer rescue rover in a town hit
by a flood, earthquake, wildfire or landslide (server/disasters.py).

Modes (only while the safety layer is ARMED):
  MANUAL  clutch held: left stick = throttle, right stick = turn
  AUTO    a goal is set and the clutch is released: follow the A* route
  IDLE    armed, no goal, no clutch: brake to a stop
When not ARMED (DISARMED / HOLDING) the rover brakes hard ("HOLD") and keeps
its route, which resumes after re-arming.

Geofence (enforced here, never in the client): map bounds, buildings and
BLOCKED cells (deep water, rubble, fire, thick mud). SLOW cells limit speed.
Vehicle protection: in a wildfire the rover overheats near the fire and
stops itself (hazard reason "overheat").

Heading convention: yaw = 0 faces +Z; forward = (sin yaw, cos yaw);
positive yaw rate turns left.
"""
from __future__ import annotations

import math
import random

from . import nav
from .disasters import DISASTERS
from .protocol import Command
from .town import BLOCKED, BUILDING, CAMP, HALF, SLOW, N, Town, cell_of, in_bounds

# Rover limits (skid steer: it can turn on the spot)
VMAX = 8.0            # m/s forward
VREV = 3.0            # m/s reverse
ACCEL = 3.0           # m/s^2
BRAKE = 6.0           # m/s^2 normal braking
EBRAKE = 15.0         # m/s^2 when not armed (hold)
WMAX = 1.2            # rad/s turn rate
WACC = 4.0            # rad/s^2
FRONT = 1.5           # m from centre to bumper (collision probe)
BOUND = HALF - 3.0

# Mission
LOAD_RADIUS = 11.0    # m from the victim's house centre (approach spot <= 8 m + stop tolerance)
LOAD_S = 2.0
CAPACITY = 2
UNLOAD_SPEED = 1.0
GOAL_TOL = 2.5
LOOKAHEAD = 6.0
HAZARD_S = 4.0       # the person stays on the road this long
HAZARD_AHEAD = 8.0   # m ahead of the rover centre (6.5 m from the bumper)
HIT_MARGIN = 1.0     # closer than this to the person counts as "would have hit"

SCENARIOS = list(DISASTERS)


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def approach(cur: float, target: float, rate: float) -> float:
    return cur + max(-rate, min(rate, target - cur))


class RescueWorld:
    client_path = "/rescue/"
    dashboard_path = "/rescue-dashboard/"
    bounds = BOUND

    def __init__(self, scenario: str = "flood", seed: int = 7) -> None:
        cls = DISASTERS[scenario]
        self.name = scenario
        self.town = Town(seed, **cls.TERRAIN)
        self.map = self.town                    # alias used by tests and tools
        self.hazard = cls(self.town)
        self._update_classes()
        self.town.choose_victims(self.hazard.victim_ok, self.plannable,
                                 lambda x, z: nav.plan(self.cost, CAMP[:2], (x, z))[0] is not None)
        self.events: list = []
        self.t = 0.0
        self.reset()

    # ----------------------------------------------------------- state
    def reset(self) -> None:
        self.hazard.reset()
        self._update_classes()
        self.x, self.z = CAMP[0] - 4.0, CAMP[1] - 4.0
        self.yaw = math.pi          # facing -Z (north, into town)
        self.v = 0.0
        self.w = 0.0
        self.mode = "IDLE"
        self.goal: tuple[float, float] | None = None
        self.path: list[tuple[float, float]] = []
        self.path_id = 0
        self.wp = 1
        self.victims = [{"id": i, "status": "waiting", "load": 0.0} for i in range(len(self.town.victims))]
        self.onboard: list[int] = []
        self.rescued = 0
        self.task_start: float | None = None
        self.task_time: float | None = None
        self.person: tuple[float, float] | None = None
        self.person_until = 0.0
        self.ai_stop_at = 0.0
        self.stop_test: dict | None = None      # running AI-stop measurement
        self.last_stop: dict | None = None      # last finished one (shown on HUD/dashboard)
        self.stop_results: list[dict] = []      # drained by the server
        self.bump_t = -10.0
        self.replan_t = -10.0
        self.recover_until = -1.0
        self.events.append((self.t, "reset", self.name))

    def _update_classes(self) -> None:
        self.cls = self.hazard.classes()
        self.cost = nav.cost_grid(self.cls, self.town.road_cell)

    def plannable(self, i: int, j: int) -> bool:
        return in_bounds(i, j) and self.cost[j * N + i] < nav.INF

    def cls_at(self, x: float, z: float) -> int:
        i, j = cell_of(x, z)
        if not in_bounds(i, j):
            return BUILDING
        return self.cls[j * N + i]

    def approach_spot(self, vid: int) -> tuple[float, float] | None:
        return self.town.approach_spot(self.town.victims[vid], self.plannable)

    @property
    def level(self) -> float:
        return self.hazard.level

    # ----------------------------------------------------------- interface
    def hello(self) -> dict:
        m = self.town.to_client()
        m["victims"] = [[round(b.x, 2), round(b.z, 2), round(b.h, 2)] for b in self.town.victims]
        m["victim_buildings"] = [self.town.buildings.index(b) for b in self.town.victims]
        m["load_radius"] = LOAD_RADIUS
        m["water_level"] = self.hazard.level
        m["deep"], m["shallow"] = 0.8, 0.2
        m["hazard"] = {"type": self.name, "title": self.hazard.title, **self.hazard.static()}
        layer = self.hazard.layer()
        if layer is not None:
            m["layer_v"], m["layer"] = layer
        return {"map": m, "title": self.hazard.title, "controls": self.hazard.controls}

    def layer(self):
        return self.hazard.layer()

    def operator_msg(self, msg: dict, armed: bool) -> str | None:
        if msg["type"] == "cancel_goto":
            self._clear_goal("cancelled")
            return "Route cancelled"
        return self.set_goal(msg["x"], msg["z"], armed)

    def set_goal(self, x: float, z: float, armed: bool = True) -> str:
        # Tapping a survivor's house sends the rover to the best spot next to it
        for vid, b in enumerate(self.town.victims):
            if b.contains(x, z, pad=1.5) and self.victims[vid]["status"] == "waiting":
                spot = self.approach_spot(vid)
                if spot is not None:
                    x, z = spot
                break
        pts, goal = nav.plan(self.cost, (self.x, self.z), (x, z))
        if pts is None:
            self.events.append((self.t, "no_route", f"{x:.0f},{z:.0f}"))
            return f"No route there ({self.hazard.blocked_label.lower()} or blocked)"
        moved = math.dist(goal, (x, z)) > 1.5
        self.goal = goal
        self.path = pts
        self.wp = 1
        self.path_id += 1
        self.events.append((self.t, "goto", f"{goal[0]:.1f},{goal[1]:.1f}"))
        dist = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        return (f"Route planned: {dist:.0f} m" + (" (nearest reachable point)" if moved else "")
                + ("" if armed else " — press A to arm"))

    def _clear_goal(self, why: str) -> None:
        if self.goal is not None:
            self.events.append((self.t, "route_end", why))
        self.goal = None
        self.path = []
        self.path_id += 1

    def _route_blocked(self) -> bool:
        """True if the remaining route now crosses a cell the planner can't use."""
        if not self.path:
            return False
        pts = [(self.x, self.z)] + self.path[self.wp:]
        walked = 0.0
        for a, b in zip(pts, pts[1:]):
            seg = math.dist(a, b)
            steps = max(1, int(seg))
            for s in range(1, steps + 1):
                walked += seg / steps
                if walked < 4.0:
                    continue                      # ignore the rover's own surroundings
                x, z = a[0] + (b[0] - a[0]) * s / steps, a[1] + (b[1] - a[1]) * s / steps
                i, j = cell_of(x, z)
                if in_bounds(i, j) and self.cost[j * N + i] == nav.INF:
                    return True
        return False

    def _hazard_changed(self) -> None:
        self._update_classes()
        if self.goal is not None and self._route_blocked():
            if self.set_goal(*self.goal).startswith("No route"):
                self._clear_goal("route cut off")
            else:
                self.events.append((self.t, "replanned", self.hazard.blocked_label.lower()))

    def dashboard_msg(self, c: dict) -> None:
        a = c["action"]
        if a == "person_on_road":
            # The camera frame goes up to the AI server and the stop command comes back down:
            # the server passes in that pipeline latency for the active network (ai_latency_s).
            lat = float(c.get("ai_latency_s", 0.0))
            self.person = (self.x + math.sin(self.yaw) * HAZARD_AHEAD, self.z + math.cos(self.yaw) * HAZARD_AHEAD)
            self.person_until = self.t + HAZARD_S
            self.ai_stop_at = self.t + lat
            self.stop_test = {"t0": self.t, "lat": lat, "v0": abs(self.v), "x0": self.x, "z0": self.z,
                              "min_gap": math.inf, "profile": c.get("profile", "")}
            self.events.append((self.t, "person_on_road", f"{self.person[0]:.1f},{self.person[1]:.1f} ai_latency={lat * 1000:.0f}ms"))
            return
        action = c.get("value") if a == "hazard" else a      # raise_water etc. also accepted directly
        detail = self.hazard.control(action, self)
        if detail:
            self.events.append((self.t, "hazard", f"{action}: {detail}"))
            self._hazard_changed()

    def hazard_reason(self) -> str | None:
        if self.person is not None and self.t >= self.ai_stop_at:
            return "ai"                          # the stop command has reached the robot
        if getattr(self.hazard, "trip_pending", False):
            self.hazard.trip_pending = False      # one-shot: re-arming and driving away is allowed
            return "overheat"
        return None

    def detections(self, rng: random.Random) -> list[dict]:
        out = []
        if self.person is not None:
            out.append({"label": "person", "pos": [round(self.person[0], 2), round(self.person[1], 2)],
                        "conf": round(min(0.99, rng.gauss(0.9, 0.03)), 2)})
        for v, b in zip(self.victims, self.town.victims):
            if v["status"] == "waiting" and math.hypot(b.x - self.x, b.z - self.z) < 40:
                out.append({"label": "survivor", "pos": [round(b.x, 2), round(b.z, 2)],
                            "conf": round(min(0.99, rng.gauss(0.85, 0.05)), 2)})
        return out

    # ----------------------------------------------------------- physics
    def step(self, dt: float, armed: bool, cmd: Command | None) -> None:
        self.t += dt
        self._track_stop_test()
        if self.person is not None and self.t > self.person_until:
            self.person = None
            self.events.append((self.t, "person_on_road", "cleared"))
        if self.hazard.step(dt, self):
            self._hazard_changed()
            self._check_threats()
        was_hot = self.hazard.overheated
        self.hazard.rover_effect(self.x, self.z, dt)
        if self.hazard.overheated != was_hot:
            self.events.append((self.t, "overheat" if self.hazard.overheated else "cooled", f"{self.hazard.heat:.0f}%"))

        here = self.cls_at(self.x, self.z)
        vcap = VMAX * (self.hazard.slow_factor if here == SLOW else 1.0) * (0.5 if self.hazard.overheated else 1.0)
        target_v, target_w, decel = 0.0, 0.0, BRAKE

        if not armed:
            self.mode = "HOLD"
            decel = EBRAKE
        elif cmd is not None and cmd.clutch:
            if self.goal is not None:
                self._clear_goal("takeover")
            self.mode = "MANUAL"
            throttle = -cmd.vz                      # stick forward = -1 = drive forward
            target_v = throttle * (vcap if throttle >= 0 else VREV)
            target_w = -cmd.vx * WMAX               # stick right = turn right
        elif self.goal is not None:
            self.mode = "AUTO"
            if self.t < self.recover_until:         # back away from what we hit
                target_v = -1.5
            else:
                target_v, target_w = self._pursuit(vcap)
        else:
            self.mode = "IDLE"

        speeding_up = abs(target_v) > abs(self.v) and target_v * self.v >= 0
        self.v = approach(self.v, target_v, (ACCEL if speeding_up else decel) * dt)
        if abs(self.v) > vcap:                      # drove into a slow area at speed
            self.v = math.copysign(max(vcap, abs(self.v) - EBRAKE * dt), self.v)
        self.w = approach(self.w, target_w if armed else 0.0, (WACC if armed else WACC * 3) * dt)

        yaw = wrap(self.yaw + self.w * dt)
        nx = self.x + self.v * math.sin(yaw) * dt
        nz = self.z + self.v * math.cos(yaw) * dt
        probe = FRONT if self.v >= 0 else -FRONT
        px, pz = nx + probe * math.sin(yaw), nz + probe * math.cos(yaw)
        if abs(self.v) < 1e-4 or (self._can_enter(nx, nz, here) and self._can_enter(px, pz, here)):
            moved = abs(self.v) > 1e-4 or abs(self.w) > 1e-4
            self.x, self.z, self.yaw = nx, nz, yaw
            if moved and self.task_start is None and self.task_time is None:
                self.task_start = self.t
                self.events.append((self.t, "task_start", ""))
        else:
            self.yaw = yaw                          # may still turn on the spot
            self.v = 0.0
            if self.t - self.bump_t > 1.0:
                self.events.append((self.t, "blocked", f"{self.x:.1f},{self.z:.1f}"))
            self.bump_t = self.t
            if self.mode == "AUTO" and self.goal is not None:
                if math.dist((self.x, self.z), self.goal) < 6.0:
                    self._clear_goal("arrived")     # close enough; the goal hugs an obstacle
                elif self.t - self.replan_t > 1.0:
                    self.replan_t = self.t
                    self.recover_until = self.t + 0.8
                    if self.set_goal(*self.goal).startswith("No route"):
                        self._clear_goal("blocked")
        self._rescue(dt)

    def _track_stop_test(self) -> None:
        st = self.stop_test
        if st is None or self.person is None:
            return
        bx, bz = self.x + math.sin(self.yaw) * FRONT, self.z + math.cos(self.yaw) * FRONT
        st["min_gap"] = min(st["min_gap"], math.dist((bx, bz), self.person))
        stopped = self.t >= self.ai_stop_at and abs(self.v) < 0.05
        if not (stopped or self.t - st["t0"] > HAZARD_S - 0.1):
            return
        travelled = math.dist((st["x0"], st["z0"]), (self.x, self.z))
        if st["v0"] < 1.0:
            result = "stationary"                 # nothing to measure: drive first, then try again
        elif st["min_gap"] < HIT_MARGIN:
            result = "would have hit"
        else:
            result = "stopped"
        self.last_stop = {
            "result": result, "reaction_ms": round(st["lat"] * 1000), "speed_kmh": round(st["v0"] * 3.6, 1),
            "travelled_m": round(travelled, 2), "margin_m": round(max(0.0, st["min_gap"]), 2),
            "blind_m": round(st["v0"] * st["lat"], 2),      # driven before the stop even arrived
            "profile": st["profile"], "t": round(self.t, 2),
        }
        self.stop_results.append(dict(self.last_stop))
        self.events.append((self.t, "stop_test", f"{result} margin={self.last_stop['margin_m']}m "
                                                 f"reaction={self.last_stop['reaction_ms']}ms v={self.last_stop['speed_kmh']}km/h"))
        self.stop_test = None

    def _can_enter(self, x: float, z: float, here: int) -> bool:
        if abs(x) > BOUND or abs(z) > BOUND:
            return False
        c = self.cls_at(x, z)
        if c == BUILDING:
            return False
        if c == BLOCKED and here != BLOCKED:      # may drive *out* of a hazard that grew around it
            return False
        return True

    def _check_threats(self) -> None:
        for v, b in zip(self.victims, self.town.victims):
            if v["status"] == "waiting" and self.hazard.threatens(b):
                v["status"] = "airlifted"
                v["load"] = 0.0
                self.events.append((self.t, "airlifted", str(v["id"])))
        self._check_done()

    def _pursuit(self, vcap: float) -> tuple[float, float]:
        remaining = math.dist((self.x, self.z), self.goal)
        if remaining < GOAL_TOL:
            self._clear_goal("arrived")
            return 0.0, 0.0
        tx, tz = self._carrot()
        dx, dz = tx - self.x, tz - self.z
        fx, fz = math.sin(self.yaw), math.cos(self.yaw)
        lx, lz = math.cos(self.yaw), -math.sin(self.yaw)     # left of the heading
        alpha = math.atan2(dx * lx + dz * lz, dx * fx + dz * fz)
        w = max(-WMAX, min(WMAX, 2.5 * alpha))
        if abs(alpha) > 0.6:
            return 0.0, w                                     # turn on the spot first
        v = vcap * max(0.2, math.cos(alpha) - abs(alpha))
        # comfortable deceleration so the rover reaches the goal at ~0.3 m/s
        v = min(v, math.sqrt(2 * BRAKE * 0.6 * max(0.0, remaining - GOAL_TOL)) + 0.3)
        return v, w

    def _carrot(self) -> tuple[float, float]:
        """Pure-pursuit target: the point LOOKAHEAD metres further along the
        route from the rover's projection onto its current segment."""
        p = self.path
        pos = (self.x, self.z)

        def proj(a, b):
            L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
            if L2 < 1e-9:
                return 1.0
            return ((pos[0] - a[0]) * (b[0] - a[0]) + (pos[1] - a[1]) * (b[1] - a[1])) / L2

        def seg_dist(a, b):
            t = min(1.0, max(0.0, proj(a, b)))
            return math.dist(pos, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))

        # Track whichever of the next few segments the rover is closest to
        # (it may cut a corner), never going backwards along the route.
        best_k, best_d = self.wp, seg_dist(p[self.wp - 1], p[self.wp])
        for k in range(self.wp + 1, min(len(p), self.wp + 4)):
            d = seg_dist(p[k - 1], p[k])
            if d <= best_d:
                best_k, best_d = k, d
        self.wp = best_k
        while self.wp < len(p) - 1 and proj(p[self.wp - 1], p[self.wp]) >= 1.0:
            self.wp += 1
        a, b = p[self.wp - 1], p[self.wp]
        t = min(1.0, max(0.0, proj(a, b)))
        pt = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        remaining, k = LOOKAHEAD, self.wp
        while k < len(p):
            d = math.dist(pt, p[k])
            if d >= remaining:
                f = remaining / d
                return (pt[0] + (p[k][0] - pt[0]) * f, pt[1] + (p[k][1] - pt[1]) * f)
            remaining -= d
            pt = p[k]
            k += 1
        return p[-1]

    def _rescue(self, dt: float) -> None:
        slow = abs(self.v) < 0.3
        for v, b in zip(self.victims, self.town.victims):
            if v["status"] != "waiting":
                continue
            near = math.hypot(b.x - self.x, b.z - self.z) <= LOAD_RADIUS
            if near and slow and len(self.onboard) < CAPACITY:
                v["load"] = min(1.0, v["load"] + dt / LOAD_S)
                if v["load"] >= 1.0:
                    v["status"] = "onboard"
                    self.onboard.append(v["id"])
                    self.events.append((self.t, "loaded", str(v["id"])))
            elif v["load"] > 0 and not near:
                v["load"] = 0.0
        if self.onboard and abs(self.v) < UNLOAD_SPEED and math.hypot(self.x - CAMP[0], self.z - CAMP[1]) <= CAMP[2]:
            for vid in self.onboard:
                self.victims[vid]["status"] = "rescued"
                self.rescued += 1
                self.events.append((self.t, "rescued", str(vid)))
            self.onboard = []
            self._check_done()

    def _check_done(self) -> None:
        resolved = all(v["status"] in ("rescued", "airlifted") for v in self.victims)
        if resolved and self.task_time is None and self.task_start is not None:
            self.task_time = self.t - self.task_start
            self.events.append((self.t, "task_complete", f"{self.task_time:.2f} rescued={self.rescued}"))

    # ----------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        elapsed = self.task_time if self.task_time is not None else (
            None if self.task_start is None else self.t - self.task_start)
        here = self.cls_at(self.x, self.z)
        h = self.hazard
        return {
            "scenario": self.name, "title": h.title, "stage": h.stage(),
            "rover": {"x": round(self.x, 3), "z": round(self.z, 3), "y": round(self.town.ground_at(self.x, self.z), 3),
                      "yaw": round(self.yaw, 4), "v": round(self.v, 2), "w": round(self.w, 3)},
            "mode": self.mode,
            "terrain": {SLOW: "slow", BLOCKED: "blocked"}.get(here, "clear"),
            "terrain_label": {SLOW: h.slow_label, BLOCKED: h.blocked_label}.get(here, ""),
            "blocked": self.t - self.bump_t < 0.8,
            "goal": None if self.goal is None else [round(self.goal[0], 2), round(self.goal[1], 2)],
            "path_id": self.path_id,
            "path": [[round(x, 1), round(z, 1)] for x, z in self.path[max(0, self.wp - 1):]],
            "victims": [{"id": v["id"], "status": v["status"], "load": round(v["load"], 2)} for v in self.victims],
            "onboard": len(self.onboard), "capacity": CAPACITY,
            "score": self.rescued, "target": len(self.victims),
            "airlifted": sum(v["status"] == "airlifted" for v in self.victims),
            "task_s": None if elapsed is None else round(elapsed, 1),
            "done": self.task_time is not None,
            "level": h.level,
            "hz": h.dynamic(),
            "hazard": None if self.person is None else [round(self.person[0], 2), round(self.person[1], 2)],
            "ai_pending": self.person is not None and self.t < self.ai_stop_at,
            "stop_test": None if self.last_stop is None else {**self.last_stop, "age": round(self.t - self.last_stop["t"], 1)},
            "t": round(self.t, 2),
        }
