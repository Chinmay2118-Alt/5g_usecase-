"""Route planning on the town grid: 8-connected A* + line-of-sight smoothing.

Works on the generic cell classes from server/town.py, so every disaster
(flood, earthquake, wildfire, landslide) plans the same way."""
from __future__ import annotations

import heapq
import math

from .town import BLOCKED, BUILDING, CELL, N, SLOW, cell_center, cell_of, in_bounds

INF = math.inf
COST_ROAD = 1.0
COST_DRY = 1.4
COST_SLOW = 3.5
COST_NEAR_BLOCKED = 2.5     # extra cost next to buildings / deep water (keeps clearance)

_NEIGH = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
          (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))]


def blocked(c: int) -> bool:
    return c == BUILDING or c == BLOCKED


_FULL = (1 << N) - 1


def _dilate(rows: list[int]) -> list[int]:
    """8-neighbour dilation of a grid stored as one int bitmask per row."""
    h = [(r | (r << 1) | (r >> 1)) & _FULL for r in rows]
    return [h[j] | (h[j - 1] if j > 0 else 0) | (h[j + 1] if j < N - 1 else 0) for j in range(N)]


def cost_grid(cls: list[int], road: list[bool]) -> list[float]:
    """Planning cost per cell. Cells next to a building or BLOCKED cell are
    treated as blocked too (2 m clearance for a 1.8 m wide rover), and cells
    two away cost extra, so routes keep to the middle of streets.

    Bitmask rows keep this ~10x faster than per-cell neighbour loops; it runs
    every time a hazard changes (twice a second during a wildfire)."""
    rows = []
    for j in range(N):
        r, base_k = 0, j * N
        for i in range(N):
            c = cls[base_k + i]
            if c == BUILDING or c == BLOCKED:
                r |= 1 << i
        rows.append(r)
    near = _dilate(rows)              # blocked or touching a blocked cell -> not plannable
    near2 = _dilate(near)             # within two cells -> extra cost
    cost = [INF] * (N * N)
    for j in range(1, N - 1):
        nr, n2, base_k = near[j], near2[j], j * N
        for i in range(1, N - 1):
            if (nr >> i) & 1:
                continue
            k = base_k + i
            base = COST_SLOW if cls[k] == SLOW else COST_ROAD if road[k] else COST_DRY
            if (n2 >> i) & 1:
                base += COST_NEAR_BLOCKED
            cost[k] = base
    return cost


def _cost_grid_reference(cls: list[int], road: list[bool]) -> list[float]:
    """The original straightforward version, kept for the equivalence test."""
    near = [False] * (N * N)
    for j in range(N):
        for i in range(N):
            if blocked(cls[j * N + i]):
                for di, dj, _ in _NEIGH:
                    ii, jj = i + di, j + dj
                    if in_bounds(ii, jj):
                        near[jj * N + ii] = True
    cost = [INF] * (N * N)
    for j in range(N):
        for i in range(N):
            k = j * N + i
            c = cls[k]
            if blocked(c) or near[k] or i == 0 or j == 0 or i == N - 1 or j == N - 1:
                continue
            base = COST_SLOW if c == SLOW else COST_ROAD if road[k] else COST_DRY
            if any(near[(j + dj) * N + i + di] for di, dj, _ in _NEIGH if in_bounds(i + di, j + dj)):
                base += COST_NEAR_BLOCKED
            cost[k] = base
    return cost


def nearest_free(cost: list[float], i: int, j: int, radius: int = 8) -> tuple[int, int] | None:
    if in_bounds(i, j) and cost[j * N + i] < INF:
        return (i, j)
    best, best_d = None, INF
    for jj in range(j - radius, j + radius + 1):
        for ii in range(i - radius, i + radius + 1):
            if in_bounds(ii, jj) and cost[jj * N + ii] < INF:
                d = (ii - i) ** 2 + (jj - j) ** 2
                if d < best_d:
                    best, best_d = (ii, jj), d
    return best


def astar(cost: list[float], start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]] | None:
    si, sj = start
    gi, gj = goal
    sk, gk = sj * N + si, gj * N + gi
    if cost[gk] == INF:
        return None
    g = {sk: 0.0}
    came: dict[int, int] = {}
    openh = [(0.0, sk)]
    closed = set()
    while openh:
        _, k = heapq.heappop(openh)
        if k == gk:
            path = [k]
            while k in came:
                k = came[k]
                path.append(k)
            path.reverse()
            return [(p % N, p // N) for p in path]
        if k in closed:
            continue
        closed.add(k)
        i, j = k % N, k // N
        gk_ = g[k]
        for di, dj, step in _NEIGH:
            ii, jj = i + di, j + dj
            if not (0 <= ii < N and 0 <= jj < N):
                continue
            nk = jj * N + ii
            c = cost[nk]
            if c == INF or nk in closed:
                continue
            if di and dj and (cost[j * N + ii] == INF or cost[jj * N + i] == INF):
                continue                    # no corner cutting
            ng = gk_ + step * c
            if ng < g.get(nk, INF):
                g[nk] = ng
                came[nk] = k
                heapq.heappush(openh, (ng + math.hypot(ii - gi, jj - gj) * COST_ROAD, nk))
    return None


def segment_clear(cost: list[float], a: tuple[float, float], b: tuple[float, float], base: list[float]) -> bool:
    """True if the straight segment a->b stays on cells that are passable and
    not costlier than the cells the A* route used (so smoothing never cuts
    through shallow water or hugs walls that A* avoided)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    dist = math.hypot(dx, dz)
    steps = max(1, int(dist / (CELL * 0.25)))
    limit = max(base) + 1e-9
    for s in range(steps + 1):
        t = s / steps
        x, z = a[0] + dx * t, a[1] + dz * t
        i, j = cell_of(x, z)
        if not in_bounds(i, j):
            return False
        c = cost[j * N + i]
        if c == INF or c > limit:
            return False
    return True


def smooth(cost: list[float], cells: list[tuple[int, int]]) -> list[tuple[float, float]]:
    pts = [cell_center(i, j) for i, j in cells]
    costs = [cost[j * N + i] for i, j in cells]
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    k = 0
    while k < len(pts) - 1:
        far = k + 1
        for m in range(len(pts) - 1, k + 1, -1):
            if segment_clear(cost, pts[k], pts[m], costs[k:m + 1]):
                far = m
                break
        out.append(pts[far])
        k = far
    return out


def plan(cost: list[float], start_xz: tuple[float, float], goal_xz: tuple[float, float]):
    """Returns (waypoints [(x, z), ...] ending at the (possibly adjusted) goal, adjusted_goal) or (None, None)."""
    s = nearest_free(cost, *cell_of(*start_xz), radius=3)
    g = nearest_free(cost, *cell_of(*goal_xz))
    if s is None or g is None:
        return None, None
    cells = astar(cost, s, g)
    if cells is None:
        return None, None
    pts = smooth(cost, cells)
    goal = cell_center(*g) if g != cell_of(*goal_xz) else goal_xz
    pts[-1] = goal
    pts[0] = start_xz
    return pts, goal
