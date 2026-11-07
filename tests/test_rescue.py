import math

import pytest

from server import nav
from server.disasters import Flood
from server.town import BLOCKED, BUILDING, CAMP, N, N_VICTIMS, cell_of

DEEP_W = BLOCKED
LEVELS = Flood.LEVELS
from server.protocol import Command, ProtocolError, parse_world_msg
from server.rescue_world import EBRAKE, VMAX, RescueWorld

IDLE = Command(0, 0.0, False, 0.0, 0.0, 0.0, False)


def drive(vz=-1.0, vx=0.0):
    return Command(0, 0.0, True, vx, 0.0, vz, False)


@pytest.fixture(scope="module")
def base():
    return RescueWorld("flood")


@pytest.fixture
def w(base):
    base.reset()
    base.events.clear()
    return base


def run(w, seconds, armed=True, cmd=IDLE, dt=0.01):
    for _ in range(int(round(seconds / dt))):
        w.step(dt, armed, cmd)


def go(w, goal, limit=150.0):
    msg = w.set_goal(*goal)
    t0 = w.t
    while w.goal is not None and w.t - t0 < limit:
        w.step(0.02, True, IDLE)
    return msg, w.goal is None


def test_five_victims_all_reachable_from_camp(w):
    assert len(w.map.victims) == N_VICTIMS
    for vid in range(N_VICTIMS):
        spot = w.approach_spot(vid)
        assert spot is not None
        pts, _ = nav.plan(w.cost, CAMP[:2], spot)
        assert pts is not None, f"victim {vid} unreachable"


def test_routes_never_cross_buildings_or_deep_water(w):
    for vid in range(N_VICTIMS):
        pts, _ = nav.plan(w.cost, CAMP[:2], w.approach_spot(vid))
        for a, b in zip(pts, pts[1:]):
            steps = int(math.dist(a, b) / 0.25) + 1
            for s in range(steps + 1):
                x = a[0] + (b[0] - a[0]) * s / steps
                z = a[1] + (b[1] - a[1]) * s / steps
                assert w.cls_at(x, z) not in (BUILDING, DEEP_W), (vid, x, z)


def test_full_autonomous_mission(w):
    for vid in range(N_VICTIMS):
        _, ok = go(w, w.approach_spot(vid))
        assert ok
        run(w, 2.5)
        assert w.victims[vid]["status"] == "onboard"
        if len(w.onboard) == 2 or vid == N_VICTIMS - 1:
            _, ok = go(w, CAMP[:2])
            assert ok
            run(w, 0.5)
            assert not w.onboard
    assert w.rescued == N_VICTIMS and w.task_time is not None and w.task_time > 0


def test_capacity_is_two(w):
    for vid in range(3):
        go(w, w.approach_spot(vid))
        run(w, 2.5)
    assert len(w.onboard) == 2
    assert [v["status"] for v in w.victims[:3]].count("onboard") == 2


def test_loading_needs_two_seconds_stationary(w):
    go(w, w.approach_spot(0))
    run(w, 1.0)
    assert w.victims[0]["status"] == "waiting" and 0 < w.victims[0]["load"] < 1
    run(w, 1.2)
    assert w.victims[0]["status"] == "onboard"


def test_manual_drive_cannot_enter_deep_water_or_buildings(w):
    # point the rover at the river and floor it for a long time
    w.x, w.z, w.yaw = -60.0, 20.0, math.pi       # heading north towards the river
    for heading in (math.pi, math.pi / 2, -math.pi / 2, 0.0):
        w.yaw = heading
        for _ in range(3000):
            w.step(0.01, True, drive(-1.0, 0.0))
            assert w.cls_at(w.x, w.z) not in (BUILDING, DEEP_W)
        w.step(0.01, True, IDLE)


def test_clutch_release_stops_and_hold_brakes_hard(w):
    run(w, 3.0, cmd=drive(-1.0))
    assert w.v > 4.0 and w.mode == "MANUAL"
    run(w, 3.0, cmd=IDLE)
    assert abs(w.v) < 1e-6 and w.mode == "IDLE"
    run(w, 3.0, cmd=drive(-1.0))
    v0 = w.v
    run(w, v0 / EBRAKE + 0.05, armed=False, cmd=drive(-1.0))   # watchdog -> not armed
    assert abs(w.v) < 1e-6 and w.mode == "HOLD"


def test_hold_pauses_route_and_rearm_resumes(w):
    w.set_goal(*w.approach_spot(0))
    run(w, 3.0)
    assert w.mode == "AUTO" and w.v > 1
    goal = w.goal
    run(w, 1.0, armed=False)
    assert w.v == 0 and w.goal == goal
    run(w, 1.0)
    assert w.mode == "AUTO" and w.v > 0


def test_manual_takeover_cancels_route(w):
    w.set_goal(*w.approach_spot(0))
    run(w, 1.0)
    run(w, 0.1, cmd=drive(0.0))
    assert w.goal is None and w.mode == "MANUAL"


def test_speed_limit(w):
    run(w, 10.0, cmd=drive(-1.0))
    assert w.v <= VMAX + 1e-9


def test_person_on_road_is_a_hazard_then_clears(w):
    assert w.hazard_reason() is None
    w.dashboard_msg({"action": "person_on_road"})
    assert w.hazard_reason() == "ai"
    assert any(d["label"] == "person" for d in w.detections(__import__("random").Random(0)))
    run(w, 4.2)
    assert w.hazard_reason() is None


def test_raise_water_floods_more_and_replans(w):
    w.set_goal(*w.approach_spot(0))
    pid = w.path_id
    deep0 = w.cls.count(DEEP_W)
    w.dashboard_msg({"action": "raise_water"})
    assert w.level == LEVELS[1] and w.cls.count(DEEP_W) > deep0
    assert w.path_id != pid
    w.dashboard_msg({"action": "lower_water"})
    assert w.level == LEVELS[0]


def test_goal_on_a_building_moves_to_nearest_reachable(w):
    b = w.map.buildings[10]
    msg = w.set_goal(b.x, b.z)
    assert msg.startswith("Route planned")
    i, j = cell_of(*w.goal)
    assert w.cost[j * N + i] < nav.INF


def test_goto_bounds_checked():
    assert parse_world_msg({"type": "goto", "x": 10, "z": -5}, 117)["x"] == 10.0
    with pytest.raises(ProtocolError):
        parse_world_msg({"type": "goto", "x": 500, "z": 0}, 117)
    with pytest.raises(ProtocolError):
        parse_world_msg({"type": "goto", "x": "a", "z": 0}, 117)
