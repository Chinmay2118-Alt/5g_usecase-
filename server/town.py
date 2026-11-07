"""Deterministic town shared by every disaster scenario.

Frame: metres, Y up, map centred on the origin, x and z in [-120, 120].
+Z points towards the operator's side of the table (south), +X to their right.

The terrain is an analytic height function: a river valley across the
middle, a hill with the rescue camp in the south-east, a second hill in the
north-west (bigger for the landslide), and two raised bridges.

Cells are classified by the active disaster (server/disasters.py) into the
generic classes the planner and the rover understand:
  CLEAR     drive normally
  SLOW      passable, speed limited (shallow water, mud, debris, smoke)
  BLOCKED   impassable (deep water, rubble, fire, thick mud)
  BUILDING  an intact building
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

SIZE = 240.0
HALF = SIZE / 2
CELL = 2.0
N = int(SIZE / CELL)          # 120 x 120 cells

CAMP = (88.0, 88.0, 14.0)     # x, z, radius
BRIDGES = [(-30.0, 2.6), (50.0, 3.9)]   # x of a north-south bridge, deck height
ROADS_X = [-95.0, -62.0, -30.0, 10.0, 50.0, 95.0]
ROADS_Z = [-95.0, -62.0, 48.0, 80.0]
ROAD_W = 6.0
N_VICTIMS = 5
APPROACH_R = 8.0             # a rover spot must be this close to the victim's house centre

CLEAR, SLOW, BLOCKED, BUILDING = 0, 1, 2, 3


def river_z(x: float) -> float:
    return -8.0 + 22.0 * math.sin(x / 38.0) + 6.0 * math.sin(x / 17.0 + 1.0)


def cell_center(i: int, j: int) -> tuple[float, float]:
    return (-HALF + (i + 0.5) * CELL, -HALF + (j + 0.5) * CELL)


def cell_of(x: float, z: float) -> tuple[int, int]:
    return (int((x + HALF) // CELL), int((z + HALF) // CELL))


def in_bounds(i: int, j: int) -> bool:
    return 0 <= i < N and 0 <= j < N


def on_road(x: float, z: float) -> bool:
    return any(abs(x - rx) < ROAD_W / 2 for rx in ROADS_X) or any(abs(z - rz) < ROAD_W / 2 for rz in ROADS_Z)


@dataclass
class Building:
    x: float
    z: float
    w: float
    d: float
    h: float

    def contains(self, x: float, z: float, pad: float = 0.0) -> bool:
        return abs(x - self.x) <= self.w / 2 + pad and abs(z - self.z) <= self.d / 2 + pad


@dataclass
class Town:
    seed: int = 7
    nw_hill: float = 4.5          # height of the north-west hill
    nw_sigma: float = 24.0        # its width
    ground: list[float] = field(default_factory=list)      # per cell, index j*N+i
    corners: list[float] = field(default_factory=list)     # (N+1)^2, index jz*(N+1)+ix
    building_cell: list[bool] = field(default_factory=list)
    building_of: list[int] = field(default_factory=list)   # per cell: building index or -1
    road_cell: list[bool] = field(default_factory=list)
    buildings: list[Building] = field(default_factory=list)
    victims: list[Building] = field(default_factory=list)   # the building each victim is in

    def __post_init__(self) -> None:
        rng = random.Random(self.seed)
        self.ground = [0.0] * (N * N)
        self.road_cell = [False] * (N * N)
        for j in range(N):
            for i in range(N):
                x, z = cell_center(i, j)
                self.ground[j * N + i] = self.elevation(x, z)
                self.road_cell[j * N + i] = on_road(x, z)
        self.corners = [self.elevation(-HALF + ix * CELL, -HALF + jz * CELL)
                        for jz in range(N + 1) for ix in range(N + 1)]
        self._place_buildings(rng)
        self.building_cell = [False] * (N * N)
        self.building_of = [-1] * (N * N)
        for bi, b in enumerate(self.buildings):
            for k in self.cells_in_rect(b.x, b.z, b.w / 2 + 0.2, b.d / 2 + 0.2):
                self.building_cell[k] = True
                self.building_of[k] = bi

    # ------------------------------------------------------------ terrain
    def elevation(self, x: float, z: float) -> float:
        d = abs(z - river_z(x))
        h = 0.5 + 4.2 * (1 - math.exp(-(d / 45.0) ** 2)) - 2.5 * math.exp(-(d / 9.0) ** 2)
        h += 7.0 * math.exp(-((x - CAMP[0]) ** 2 + (z - CAMP[1]) ** 2) / (2 * 28.0 ** 2))
        h += self.nw_hill * math.exp(-((x + 85.0) ** 2 + (z + 88.0) ** 2) / (2 * self.nw_sigma ** 2))
        h += 0.25 * math.sin(x / 7.0) * math.cos(z / 9.0) + 0.2 * math.sin((x + z) / 13.0)
        for bx, deck in BRIDGES:
            if abs(x - bx) < ROAD_W / 2 + 1 and d < 45:
                h = max(h, deck)
        return h

    def _place_buildings(self, rng: random.Random) -> None:
        step = 12.5
        x = -HALF + 8
        while x < HALF - 6:
            z = -HALF + 8
            while z < HALF - 6:
                w, d = rng.uniform(6, 9), rng.uniform(6, 9)
                bx, bz = x + rng.uniform(-1.5, 1.5), z + rng.uniform(-1.5, 1.5)
                ok = (
                    rng.random() > 0.15
                    and not any(abs(bx - rx) < ROAD_W / 2 + w / 2 + 1.0 for rx in ROADS_X)
                    and not any(abs(bz - rz) < ROAD_W / 2 + d / 2 + 1.0 for rz in ROADS_Z)
                    and abs(bz - river_z(bx)) > 14
                    and math.hypot(bx - CAMP[0], bz - CAMP[1]) > CAMP[2] + 12
                    and abs(bx) < HALF - 8 and abs(bz) < HALF - 8
                )
                if ok:
                    self.buildings.append(Building(bx, bz, w, d, rng.uniform(4.0, 10.0)))
                z += step
            x += step

    # ------------------------------------------------------------ queries
    def cells_in_rect(self, x: float, z: float, hx: float, hz: float) -> list[int]:
        i0, j0 = cell_of(x - hx, z - hz)
        i1, j1 = cell_of(x + hx, z + hz)
        out = []
        for j in range(max(0, j0), min(N, j1 + 1)):
            for i in range(max(0, i0), min(N, i1 + 1)):
                cx, cz = cell_center(i, j)
                if abs(cx - x) <= hx and abs(cz - z) <= hz:
                    out.append(j * N + i)
        return out

    def cells_in_disc(self, x: float, z: float, r: float) -> list[int]:
        return [k for k in self.cells_in_rect(x, z, r, r)
                if math.hypot(*(a - b for a, b in zip(cell_center(k % N, k // N), (x, z)))) <= r]

    def ground_at(self, x: float, z: float) -> float:
        """Bilinear interpolation of the corner heights (matches the rendered terrain)."""
        fx = min(max((x + HALF) / CELL, 0.0), N - 1e-6)
        fz = min(max((z + HALF) / CELL, 0.0), N - 1e-6)
        ix, iz = int(fx), int(fz)
        tx, tz = fx - ix, fz - iz
        c = self.corners
        r = N + 1
        h00, h10 = c[iz * r + ix], c[iz * r + ix + 1]
        h01, h11 = c[(iz + 1) * r + ix], c[(iz + 1) * r + ix + 1]
        return (h00 * (1 - tx) + h10 * tx) * (1 - tz) + (h01 * (1 - tx) + h11 * tx) * tz

    def water_classes(self, level: float, shallow: float = 0.2, deep: float = 0.8) -> list[int]:
        """Classes from water depth alone (used by every scenario for the river)."""
        out = [CLEAR] * (N * N)
        for k in range(N * N):
            if self.building_cell[k]:
                out[k] = BUILDING
            else:
                depth = level - self.ground[k]
                out[k] = BLOCKED if depth > deep else SLOW if depth > shallow else CLEAR
        return out

    def choose_victims(self, eligible, passable, reachable) -> None:
        """Pick N_VICTIMS eligible houses, spread out, each with a reachable spot
        next to it. eligible(building) -> bool comes from the disaster;
        passable(i, j) and reachable(x, z) come from the planner."""
        cands = []
        for b in self.buildings:
            if not eligible(b):
                continue
            spot = self.approach_spot(b, passable)
            if spot is not None and reachable(*spot):
                cands.append(b)
        chosen: list[Building] = []
        while cands and len(chosen) < N_VICTIMS:
            def score(b):
                others = [math.hypot(b.x - c.x, b.z - c.z) for c in chosen]
                others.append(math.hypot(b.x - CAMP[0], b.z - CAMP[1]) * 0.6)
                return min(others)
            best = max(cands, key=score)
            chosen.append(best)
            cands.remove(best)
        self.victims = chosen

    @staticmethod
    def approach_spot(b: Building, passable, reach: float = APPROACH_R) -> tuple[float, float] | None:
        """The nearest cell centre the planner can use within `reach` of the house centre."""
        best, best_d = None, 1e9
        i0, j0 = cell_of(b.x - reach, b.z - reach)
        i1, j1 = cell_of(b.x + reach, b.z + reach)
        for j in range(max(0, j0), min(N, j1 + 1)):
            for i in range(max(0, i0), min(N, i1 + 1)):
                if passable(i, j):
                    x, z = cell_center(i, j)
                    d = math.hypot(x - b.x, z - b.z)
                    if d < best_d and d <= reach:
                        best, best_d = (x, z), d
        return best

    def to_client(self) -> dict:
        return {
            "size": SIZE, "cell": CELL, "n": N,
            "heights": [round(h, 2) for h in self.corners],
            "roads": {"x": ROADS_X, "z": ROADS_Z, "w": ROAD_W},
            "buildings": [[round(b.x, 2), round(b.z, 2), round(b.w, 2), round(b.d, 2), round(b.h, 2)]
                          for b in self.buildings],
            "camp": list(CAMP),
            "bridges": [[x, deck] for x, deck in BRIDGES],
        }
