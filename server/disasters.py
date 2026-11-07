"""The disasters that can hit the town (server/town.py).

Each disaster turns the town into cell classes (CLEAR / SLOW / BLOCKED /
BUILDING) for the planner and the rover, decides which houses hold
survivors, and reacts to the dashboard's hazard buttons.

Interface (used by server/rescue_world.py):
  name, title, slow_factor, slow_label, blocked_label, controls, TERRAIN
  reset()                      back to the initial state
  level                        water level for rendering (m)
  classes() -> list[int]       per-cell classes
  step(dt, world) -> bool      advance; True if classes changed
  control(action, world) -> str | None   hazard button; returns an event detail
  victim_ok(building) -> bool  can this house hold a survivor?
  threatens(building) -> bool  survivor must be airlifted (fire reached them)
  rover_effect(x, z, dt)       e.g. heat near fire; sets .overheated
  static() -> dict             sent once in 'hello'
  dynamic() -> dict            small, sent in every state
  layer() -> (version, b64) | None   per-cell state for big dynamic hazards
  stage() -> str               one-line status for HUD and dashboard
"""
from __future__ import annotations

import base64
import heapq
import math
import random

from .town import (BLOCKED, BUILDING, CAMP, CLEAR, N, ROADS_X, ROADS_Z, SLOW, BRIDGES,
                   Building, Town, cell_center, cell_of, in_bounds, river_z)

RIVER_LEVEL = 0.3            # normal river level when there is no flood
_NEIGH8 = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def seg_dist(px: float, pz: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, az = a
    bx, bz = b
    L2 = (bx - ax) ** 2 + (bz - az) ** 2
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (pz - az) * (bz - az)) / L2))
    return math.hypot(px - (ax + (bx - ax) * t), pz - (az + (bz - az) * t))


def road_points(step: float = 8.0) -> list[tuple[float, float]]:
    """Points along every road centre line, away from bridges and the camp."""
    pts = []
    for rx in ROADS_X:
        z = -112.0
        while z <= 112:
            pts.append((rx, z))
            z += step
    for rz in ROADS_Z:
        x = -112.0
        while x <= 112:
            pts.append((x, rz))
            x += step
    out = []
    for x, z in pts:
        near_bridge = any(abs(x - bx) < 6 for bx, _ in BRIDGES) and abs(z - river_z(x)) < 40
        if near_bridge or math.hypot(x - CAMP[0], z - CAMP[1]) < CAMP[2] + 22:
            continue
        out.append((x, z))
    return out


class Hazard:
    name = ""
    title = ""
    slow_factor = 0.5
    slow_label = "SLOW"
    blocked_label = "BLOCKED"
    controls: list[dict] = []
    TERRAIN: dict = {}

    def __init__(self, town: Town) -> None:
        self.town = town
        self.overheated = False
        self.heat = 0.0
        self.base = town.water_classes(RIVER_LEVEL)
        self.reset()

    level = RIVER_LEVEL

    def reset(self) -> None:
        pass

    def classes(self) -> list[int]:
        return list(self.base)

    def step(self, dt: float, world) -> bool:
        return False

    def control(self, action: str, world) -> str | None:
        return None

    def victim_ok(self, b: Building) -> bool:
        return True

    def threatens(self, b: Building) -> bool:
        return False

    def rover_effect(self, x: float, z: float, dt: float) -> None:
        pass

    def static(self) -> dict:
        return {}

    def dynamic(self) -> dict:
        return {}

    def layer(self):
        return None

    def stage(self) -> str:
        return ""


# ---------------------------------------------------------------- flood
class Flood(Hazard):
    name = "flood"
    title = "Flood"
    slow_factor = 0.4
    slow_label = "SHALLOW WATER"
    blocked_label = "DEEP WATER"
    LEVELS = [2.0, 2.7, 3.5]
    controls = [{"action": "raise_water", "label": "Raise water ▲"},
                {"action": "lower_water", "label": "Lower water ▼"}]

    def reset(self) -> None:
        self.idx = 0

    @property
    def level(self) -> float:
        return self.LEVELS[self.idx]

    def classes(self) -> list[int]:
        return self.town.water_classes(self.level)

    def control(self, action: str, world) -> str | None:
        new = self.idx + {"raise_water": 1, "lower_water": -1}.get(action, 0)
        if new != self.idx and 0 <= new < len(self.LEVELS):
            self.idx = new
            return f"water {self.level:.1f} m"
        return None

    def victim_ok(self, b: Building) -> bool:
        return self.LEVELS[0] - self.town.elevation(b.x, b.z) > 0.15

    def stage(self) -> str:
        return f"Water {self.level:.1f} m · stage {self.idx + 1}/{len(self.LEVELS)}"

    def dynamic(self) -> dict:
        return {"stage_idx": self.idx}


# ---------------------------------------------------------------- earthquake
class Earthquake(Hazard):
    name = "earthquake"
    title = "Earthquake"
    slow_factor = 0.5
    slow_label = "DEBRIS"
    blocked_label = "RUBBLE"
    controls = [{"action": "aftershock", "label": "Aftershock!"}]
    COLLAPSE_FRACTION = 0.35
    INITIAL_PILES = 6

    def __init__(self, town: Town) -> None:
        rng = random.Random(21)
        n = len(town.buildings)
        self.collapsed = set(rng.sample(range(n), int(n * self.COLLAPSE_FRACTION)))
        pts = road_points()
        rng.shuffle(pts)
        self.initial_piles: list[tuple[float, float, float]] = []
        for x, z in pts:
            if len(self.initial_piles) >= self.INITIAL_PILES:
                break
            if all(math.hypot(x - px, z - pz) > 45 for px, pz, _ in self.initial_piles):
                self.initial_piles.append((x, z, rng.uniform(3.5, 4.5)))
        super().__init__(town)

    def reset(self) -> None:
        self.piles = list(self.initial_piles)
        self.quake_t: float | None = None
        self.aftershocks = 0

    def classes(self) -> list[int]:
        out = list(self.base)
        t = self.town

        def mark(cells, cls):
            for k in cells:
                if cls == BLOCKED or out[k] == CLEAR:
                    out[k] = cls

        for bi in self.collapsed:
            b = t.buildings[bi]
            mark(t.cells_in_rect(b.x, b.z, b.w / 2 + 3.5, b.d / 2 + 3.5), SLOW)
            mark(t.cells_in_rect(b.x, b.z, b.w / 2 + 1.0, b.d / 2 + 1.0), BLOCKED)
        for x, z, r in self.piles:
            mark(t.cells_in_disc(x, z, r + 3.0), SLOW)
            mark(t.cells_in_disc(x, z, r), BLOCKED)
        return out

    def control(self, action: str, world) -> str | None:
        if action != "aftershock":
            return None
        rng = random.Random(100 + self.aftershocks)
        targets = []
        pts = [(world.x, world.z)] + [tuple(p) for p in world.path[world.wp:]] if world.path else []
        if len(pts) > 1:                       # drop debris ahead on the active route
            want, walked = [25.0, 50.0, 75.0], 0.0
            for a, b in zip(pts, pts[1:]):
                seg = math.dist(a, b)
                while want and walked + seg >= want[0]:
                    f = (want.pop(0) - walked) / seg
                    targets.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
                walked += seg
        cands = [p for p in road_points(6.0) if 15 < math.dist(p, (world.x, world.z)) < 70]
        rng.shuffle(cands)
        targets += cands
        added = []
        for x, z in targets:
            if len(added) >= 3:
                break
            if math.dist((x, z), (world.x, world.z)) < 15:
                continue
            if world.goal is not None and math.dist((x, z), world.goal) < 10:
                continue
            if math.hypot(x - CAMP[0], z - CAMP[1]) < CAMP[2] + 15:
                continue
            if any(math.dist((x, z), (px, pz)) < 10 for px, pz, _ in self.piles):
                continue
            added.append((x, z, rng.uniform(3.5, 4.5)))
        self.piles += added
        self.quake_t = world.t
        self.aftershocks += 1
        return f"aftershock {self.aftershocks}: {len(added)} new debris piles"

    def victim_ok(self, b: Building) -> bool:
        return self.town.buildings.index(b) in self.collapsed

    def static(self) -> dict:
        return {"collapsed": sorted(self.collapsed)}

    def dynamic(self) -> dict:
        return {"piles": [[round(x, 1), round(z, 1), round(r, 2)] for x, z, r in self.piles],
                "quake_t": self.quake_t}

    def stage(self) -> str:
        return f"Aftershocks: {self.aftershocks} · debris piles: {len(self.piles)}"


# ---------------------------------------------------------------- landslide
class Landslide(Hazard):
    name = "landslide"
    title = "Landslide"
    slow_factor = 0.3
    slow_label = "MUD"
    blocked_label = "THICK MUD"
    TERRAIN = {"nw_hill": 13.0, "nw_sigma": 32.0}
    controls = [{"action": "trigger_slide", "label": "Trigger new slide"}]
    # (start, end, width): mud flows downhill from the north-west hill
    SLIDES = [
        ((-72.0, -76.0), (-48.0, -54.0), 16.0),   # across the z=-62 / x=-62 crossroads
        ((-96.0, -70.0), (-100.0, -40.0), 14.0),  # down the x=-95 road
        ((-70.0, -100.0), (-40.0, -106.0), 14.0), # across x=-62 and z=-95 in the north
        ((-62.0, -82.0), (-26.0, -76.0), 14.0),   # east, towards the x=-30 road
    ]

    def reset(self) -> None:
        self.active = 1

    def classes(self) -> list[int]:
        out = list(self.base)
        for a, b, w in self.SLIDES[:self.active]:
            hx, hz = abs(a[0] - b[0]) / 2 + w, abs(a[1] - b[1]) / 2 + w
            for k in self.town.cells_in_rect((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, hx, hz):
                if out[k] == BUILDING:
                    continue                    # half-buried house: still a building
                d = seg_dist(*cell_center(k % N, k // N), a, b)
                if d <= w / 2 * 0.55:
                    out[k] = BLOCKED
                elif d <= w / 2 and out[k] == CLEAR:
                    out[k] = SLOW
        return out

    def control(self, action: str, world) -> str | None:
        if action == "trigger_slide" and self.active < len(self.SLIDES):
            self.active += 1
            return f"slide {self.active}/{len(self.SLIDES)}"
        return None

    def victim_ok(self, b: Building) -> bool:
        a, e, w = self.SLIDES[0]
        near_slide = seg_dist(b.x, b.z, a, e) < w / 2 + 12
        return near_slide or self.town.elevation(b.x, b.z) > 7.0

    def dynamic(self) -> dict:
        return {"slides": [[a[0], a[1], b[0], b[1], w] for a, b, w in self.SLIDES[:self.active]]}

    def stage(self) -> str:
        return f"Slides: {self.active}/{len(self.SLIDES)}"


# ---------------------------------------------------------------- wildfire
UNBURNT, BURNING, BURNT = 0, 1, 2


class Wildfire(Hazard):
    """A predictable fire front. Each cell's ignition time comes from a
    shortest-time spread (Dijkstra) over the fuel grid: fast down-wind, slow
    up-wind, slow across roads, stopped by water and the camp firebreak.
    A small fixed per-cell variation keeps the edge natural-looking."""
    name = "wildfire"
    title = "Wildfire"
    slow_factor = 0.7
    slow_label = "SMOKE"
    blocked_label = "FIRE"
    controls = [{"action": "wind_shift", "label": "Wind shift ↻"},
                {"action": "fire_pause", "label": "Pause fire"},
                {"action": "fire_slow", "label": "Fire: slow"},
                {"action": "fire_normal", "label": "Fire: normal"},
                {"action": "fire_fast", "label": "Fire: fast"}]
    SPEEDS = {"paused": 0.0, "slow": 0.3, "normal": 0.5, "fast": 0.9}   # m/s down-wind
    UPWIND = 0.2           # fraction of the down-wind speed when spreading against the wind
    FUEL_SLOW = {1: 1.0, 2: 1.6, 3: 4.0}   # grass, building, road (crossed slowly)
    BURN_S = {1: 18.0, 2: 40.0, 3: 6.0}
    TICK = 0.5
    IGNITION = [(-95.0, -100.0), (-80.0, -108.0), (-104.0, -84.0)]
    SMOKE_CELLS = 3        # smoke (slow, avoided by routes) extends 6 m around the fire
    HEAT_RADIUS = 7.0
    HEAT_RATE = 30.0       # %/s right next to the fire
    COOL_RATE = 12.0       # %/s away from fire
    HOT = 80.0             # % above which the rover runs at half speed
    REARM_TRIP = 60.0      # % the rover must cool below before another overheat stop

    def __init__(self, town: Town) -> None:
        self.fuel = [0] * (N * N)
        base = town.water_classes(RIVER_LEVEL)
        rng = random.Random(9)
        self.noise = [rng.uniform(0.8, 1.25) for _ in range(N * N)]
        for k in range(N * N):
            x, z = cell_center(k % N, k // N)
            if math.hypot(x - CAMP[0], z - CAMP[1]) < CAMP[2] + 16:
                continue                         # firebreak around the camp
            if town.building_cell[k]:
                self.fuel[k] = 2
            elif base[k] != CLEAR:
                continue                         # river
            elif town.road_cell[k]:
                self.fuel[k] = 3
            else:
                self.fuel[k] = 1
        super().__init__(town)

    def reset(self) -> None:
        self.state = [UNBURNT] * (N * N)
        self.burn_left: dict[int, float] = {}
        self.fire_t = 0.0
        self.acc = 0.0
        self.wind = (math.sqrt(0.5), math.sqrt(0.5))     # blowing towards the south-east
        self.speed = "normal"
        self.version = 0
        self.heat = 0.0
        self.overheated = False      # hot: half speed, HUD warning
        self.trip_pending = False    # one-shot overheat stop for the safety layer
        self.tripped = False
        seeds = []
        for x, z in self.IGNITION:
            i, j = cell_of(x, z)
            seeds.append(j * N + i)
        self._plan_spread(seeds)
        self._advance()
        self.version += 1

    # --- spread model ---------------------------------------------------
    def _plan_spread(self, sources: list[int]) -> None:
        """Arrival time of the fire at every unburnt cell, from `sources` burning now."""
        INF = math.inf
        v = self.SPEEDS[self.speed] or self.SPEEDS["normal"]
        wx, wz = self.wind
        arr = [INF] * (N * N)
        h = []
        for k in sources:
            if self.fuel[k]:
                arr[k] = self.fire_t
                heapq.heappush(h, (self.fire_t, k))
        while h:
            t, k = heapq.heappop(h)
            if t > arr[k]:
                continue
            i, j = k % N, k // N
            for di, dj in _NEIGH8:
                ii, jj = i + di, j + dj
                if not (0 <= ii < N and 0 <= jj < N):
                    continue
                nk = jj * N + ii
                f = self.fuel[nk]
                if not f or self.state[nk] != UNBURNT:
                    continue
                d = math.hypot(di, dj)
                align = (di * wx + dj * wz) / d
                speed = v * (self.UPWIND + (1 - self.UPWIND) * max(0.0, align) ** 0.7)
                nt = t + d * 2.0 / speed * self.FUEL_SLOW[f] * self.noise[nk]
                if nt < arr[nk]:
                    arr[nk] = nt
                    heapq.heappush(h, (nt, nk))
        self.arrival = arr
        self.order = sorted((t, k) for k, t in enumerate(arr) if t < INF and self.state[k] == UNBURNT)
        self.next_i = 0

    def _advance(self) -> bool:
        changed = False
        while self.next_i < len(self.order) and self.order[self.next_i][0] <= self.fire_t:
            _, k = self.order[self.next_i]
            self.next_i += 1
            if self.state[k] == UNBURNT:
                self.state[k] = BURNING
                self.burn_left[k] = self.BURN_S[self.fuel[k]] * self.noise[k]
                changed = True
        return changed

    def step(self, dt: float, world) -> bool:
        if self.SPEEDS[self.speed] == 0.0:
            return False                          # paused: frozen
        self.acc += dt
        changed = False
        while self.acc >= self.TICK:
            self.acc -= self.TICK
            self.fire_t += self.TICK
            for k in list(self.burn_left):
                self.burn_left[k] -= self.TICK
                if self.burn_left[k] <= 0:
                    del self.burn_left[k]
                    self.state[k] = BURNT
                    changed = True
            changed |= self._advance()
        if changed:
            self.version += 1
        return changed

    def classes(self) -> list[int]:
        out = list(self.base)
        r = self.SMOKE_CELLS
        for k in self.burn_left:
            if out[k] != BUILDING:
                out[k] = BLOCKED
            i, j = k % N, k // N
            for jj in range(max(0, j - r), min(N, j + r + 1)):
                row = jj * N
                for ii in range(max(0, i - r), min(N, i + r + 1)):
                    if out[row + ii] == CLEAR:
                        out[row + ii] = SLOW         # smoke
        return out

    def control(self, action: str, world) -> str | None:
        if action == "wind_shift":
            wx, wz = self.wind
            self.wind = (-wz, wx)                # rotate 90 degrees clockwise (seen from above)
            detail = f"wind towards {self.compass()}"
        else:
            speed = {"fire_pause": "paused", "fire_slow": "slow", "fire_normal": "normal",
                     "fire_fast": "fast"}.get(action)
            if not speed or speed == self.speed:
                return None
            self.speed = speed
            detail = f"fire {speed}"
        if self.SPEEDS[self.speed] > 0:
            self._plan_spread(list(self.burn_left))   # re-plan the front from what is burning now
        return detail

    def compass(self) -> str:
        wx, wz = self.wind
        ang = math.degrees(math.atan2(wx, -wz)) % 360     # 0 = north (-Z), 90 = east (+X)
        return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((ang + 22.5) // 45) % 8]

    def eta(self, b: Building) -> float | None:
        """Seconds until the planned fire front reaches this house (None = never)."""
        best = min((self.arrival[k] for k in self.town.cells_in_rect(b.x, b.z, b.w / 2 + 4, b.d / 2 + 4)),
                   default=math.inf)
        return None if best == math.inf else max(0.0, best - self.fire_t)

    def victim_ok(self, b: Building) -> bool:
        cx = sum(x for x, _ in self.IGNITION) / len(self.IGNITION)
        cz = sum(z for _, z in self.IGNITION) / len(self.IGNITION)
        d = math.hypot(b.x - cx, b.z - cz)
        eta = self.eta(b)
        return 85 < d < 210 and eta is not None and 150 < eta < 600

    def threatens(self, b: Building) -> bool:
        for k in self.town.cells_in_rect(b.x, b.z, b.w / 2 + 4, b.d / 2 + 4):
            if self.state[k] == BURNING:
                return True
        return False

    def rover_effect(self, x: float, z: float, dt: float) -> None:
        nearest = None
        for k in self.town.cells_in_disc(x, z, self.HEAT_RADIUS):
            if self.state[k] == BURNING:
                cx, cz = cell_center(k % N, k // N)
                d = math.hypot(cx - x, cz - z)
                nearest = d if nearest is None else min(nearest, d)
        if nearest is not None:
            self.heat = min(100.0, self.heat + dt * self.HEAT_RATE * max(0.2, 1 - nearest / self.HEAT_RADIUS))
        else:
            self.heat = max(0.0, self.heat - dt * self.COOL_RATE)
        self.overheated = self.heat >= self.HOT
        if self.heat >= 100.0 and not self.tripped:
            self.tripped = True
            self.trip_pending = True             # stop the rover once; the operator decides what next
        elif self.tripped and self.heat < self.REARM_TRIP:
            self.tripped = False

    def layer(self):
        if getattr(self, "_enc_v", None) != self.version:      # encode once per change
            self._enc_v, self._enc = self.version, encode_layer(self.state)
        return self.version, self._enc

    def dynamic(self) -> dict:
        return {"wind": [round(self.wind[0], 3), round(self.wind[1], 3)], "compass": self.compass(),
                "speed": self.speed, "heat": round(self.heat, 1), "overheated": self.overheated,
                "burning": len(self.burn_left)}

    def stage(self) -> str:
        return f"Fire: {len(self.burn_left)} cells burning · wind → {self.compass()} · {self.speed}"


def encode_layer(state: list[int]) -> str:
    """2 bits per cell, 4 cells per byte, base64 (about 4.8 KB for 120x120)."""
    out = bytearray((len(state) + 3) // 4)
    for k, s in enumerate(state):
        out[k >> 2] |= (s & 3) << ((k & 3) * 2)
    return base64.b64encode(bytes(out)).decode("ascii")


def decode_layer(data: str, n: int) -> list[int]:
    raw = base64.b64decode(data)
    return [(raw[k >> 2] >> ((k & 3) * 2)) & 3 for k in range(n)]


DISASTERS: dict[str, type[Hazard]] = {
    "flood": Flood, "earthquake": Earthquake, "wildfire": Wildfire, "landslide": Landslide,
}
