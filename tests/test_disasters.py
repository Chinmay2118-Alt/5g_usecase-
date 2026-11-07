import math
import random

import pytest

from server import nav
from server.disasters import BURNING, BURNT, decode_layer, encode_layer
from server.protocol import ARMED, DISARMED, Command, ProtocolError, parse_control, DASHBOARD_CONTROLS
from server.rescue_world import SCENARIOS, RescueWorld
from server.town import BLOCKED, BUILDING, CAMP, N, N_VICTIMS, cell_of

IDLE = Command(0, 0.0, False, 0.0, 0.0, 0.0, False)


@pytest.fixture(scope="module")
def worlds():
    return {s: RescueWorld(s) for s in SCENARIOS}


def fresh(worlds, name):
    w = worlds[name]
    w.reset()
    w.events.clear()
    return w


def run(w, seconds, armed=True, cmd=IDLE, dt=0.02):
    for _ in range(int(round(seconds / dt))):
        w.step(dt, armed, cmd)


def go(w, goal, limit=150.0):
    msg = w.set_goal(*goal)
    t0 = w.t
    while w.goal is not None and w.t - t0 < limit:
        w.step(0.02, True, IDLE)
    return msg, w.goal is None


def full_mission(w):
    for vid in range(N_VICTIMS):
        if w.victims[vid]["status"] != "waiting":
            continue
        _, ok = go(w, w.approach_spot(vid))
        assert ok, f"{w.name}: could not reach survivor {vid}"
        run(w, 2.5)
        assert w.victims[vid]["status"] == "onboard"
        if len(w.onboard) == 2 or vid == N_VICTIMS - 1:
            _, ok = go(w, CAMP[:2])
            assert ok
            run(w, 0.5)
    if w.onboard:
        go(w, CAMP[:2])
        run(w, 0.5)


@pytest.mark.parametrize("name", SCENARIOS)
def test_five_reachable_survivors(worlds, name):
    w = fresh(worlds, name)
    assert len(w.town.victims) == N_VICTIMS
    for vid in range(N_VICTIMS):
        spot = w.approach_spot(vid)
        assert spot is not None and nav.plan(w.cost, CAMP[:2], spot)[0] is not None


@pytest.mark.parametrize("name", SCENARIOS)
def test_full_autonomous_mission(worlds, name):
    w = fresh(worlds, name)
    if name == "wildfire":
        w.dashboard_msg({"action": "hazard", "value": "fire_pause"})   # deterministic
    full_mission(w)
    assert w.rescued == N_VICTIMS and w.task_time is not None


@pytest.mark.parametrize("name", SCENARIOS)
def test_routes_avoid_blocked_cells(worlds, name):
    w = fresh(worlds, name)
    for vid in range(N_VICTIMS):
        pts, _ = nav.plan(w.cost, CAMP[:2], w.approach_spot(vid))
        for a, b in zip(pts, pts[1:]):
            steps = int(math.dist(a, b) / 0.25) + 1
            for s in range(steps + 1):
                x = a[0] + (b[0] - a[0]) * s / steps
                z = a[1] + (b[1] - a[1]) * s / steps
                assert w.cls_at(x, z) not in (BLOCKED, BUILDING), (name, vid, x, z)


# ------------------------------------------------------------- earthquake
def test_earthquake_collapsed_houses_hold_the_survivors(worlds):
    w = fresh(worlds, "earthquake")
    idx = [w.town.buildings.index(b) for b in w.town.victims]
    assert all(i in w.hazard.collapsed for i in idx)


def test_aftershock_drops_debris_on_the_route_and_replans(worlds):
    w = fresh(worlds, "earthquake")
    w.set_goal(*w.approach_spot(0))
    run(w, 2.0)
    piles, pid = len(w.hazard.piles), w.path_id
    w.dashboard_msg({"action": "hazard", "value": "aftershock"})
    assert len(w.hazard.piles) > piles
    assert w.hazard.dynamic()["quake_t"] is not None
    assert w.path_id != pid or w.goal is None          # re-planned (or reported cut off)
    assert not w._route_blocked()


# ------------------------------------------------------------- landslide
def test_trigger_slide_blocks_more_road(worlds):
    w = fresh(worlds, "landslide")
    before = w.cls.count(BLOCKED)
    w.dashboard_msg({"action": "hazard", "value": "trigger_slide"})
    assert w.hazard.active == 2 and w.cls.count(BLOCKED) > before
    for _ in range(5):
        w.dashboard_msg({"action": "hazard", "value": "trigger_slide"})
    assert w.hazard.active == len(w.hazard.SLIDES)


# ------------------------------------------------------------- wildfire
def test_fire_spreads_and_burns_out(worlds):
    w = fresh(worlds, "wildfire")
    h = w.hazard
    burning0 = len(h.burn_left)
    for _ in range(120):
        h.step(0.5, w)
    assert len(h.burn_left) > burning0
    assert h.state.count(BURNT) > 0
    burnt = h.state.index(BURNT)
    w._update_classes()
    assert w.cls[burnt] != BLOCKED                      # ash is passable again


def test_fire_paused_is_frozen(worlds):
    w = fresh(worlds, "wildfire")
    h = w.hazard
    h.control("fire_pause", w)
    snap = list(h.state)
    for _ in range(100):
        assert not h.step(0.5, w)
    assert h.state == snap


def test_wind_shift_changes_direction(worlds):
    w = fresh(worlds, "wildfire")
    c0 = w.hazard.compass()
    assert w.hazard.control("wind_shift", w)
    assert w.hazard.compass() != c0


def test_overheat_stops_once_then_allows_driving_away(worlds):
    w = fresh(worlds, "wildfire")
    h = w.hazard
    k = next(iter(h.burn_left))
    x, z = (k % N) * 2 - 120 + 1, (k // N) * 2 - 120 + 1
    for _ in range(400):
        h.rover_effect(x + 3, z, 0.02)
    assert h.heat == 100 and h.overheated
    assert w.hazard_reason() == "overheat"
    assert w.hazard_reason() is None                     # one-shot: re-arm and drive away is allowed
    for _ in range(600):
        h.rover_effect(0.0, 100.0, 0.02)                 # far from the fire: cools
    assert h.heat < 60 and not h.overheated and not h.tripped


def test_fire_reaching_a_survivor_means_airlift(worlds):
    w = fresh(worlds, "wildfire")
    h = w.hazard
    h.control("fire_fast", w)
    for _ in range(int(900 / 0.5)):
        if h.step(0.5, w):
            w._check_threats()
        if any(v["status"] == "airlifted" for v in w.victims):
            break
    assert any(v["status"] == "airlifted" for v in w.victims)


def test_layer_roundtrip():
    rng = random.Random(1)
    state = [rng.choice([0, 1, 2]) for _ in range(N * N)]
    assert decode_layer(encode_layer(state), N * N) == state


# ------------------------------------------------------------- server switching
def test_set_scenario_protocol():
    assert parse_control({"action": "set_scenario", "value": "wildfire"}, DASHBOARD_CONTROLS)["value"] == "wildfire"
    with pytest.raises(ProtocolError):
        parse_control({"action": "set_scenario", "value": "../x"}, DASHBOARD_CONTROLS)
    with pytest.raises(ProtocolError):
        parse_control({"action": "hazard", "value": 5}, DASHBOARD_CONTROLS)


def test_server_switches_scenario_and_disarms(worlds):
    from server.main import Server
    srv = Server(dict(worlds), "flood")
    srv.log.start = lambda *a, **k: "test"      # don't write log folders from tests
    srv.safety.on_command(Command(0, 0.0, False, 0, 0, 0, False), srv.now())
    assert srv.safety.arm(srv.now())[0] and srv.safety.state == ARMED
    srv.dashboard_control({"action": "set_scenario", "value": "earthquake"})
    assert srv.world.name == "earthquake" and srv.safety.state == DISARMED
    assert '"scenarios"' in srv.hello() and '"earthquake"' in srv.hello()
    srv.dashboard_control({"action": "set_scenario", "value": "nonsense"})
    assert srv.world.name == "earthquake"


@pytest.mark.parametrize("name", SCENARIOS)
def test_fast_cost_grid_matches_reference(worlds, name):
    w = fresh(worlds, name)
    if name == "wildfire":
        w.hazard.control("fire_fast", w)
        for _ in range(120):
            w.hazard.step(0.5, w)
        w._update_classes()
    assert nav.cost_grid(w.cls, w.town.road_cell) == nav._cost_grid_reference(w.cls, w.town.road_cell)
