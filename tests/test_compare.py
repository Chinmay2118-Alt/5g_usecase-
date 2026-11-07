"""Network-profile emulation and the AI-stop distance test (the "Why 5G" comparison)."""
import asyncio
import statistics
import time

import pytest

from server.netctl import PROFILES, RETX_S, DelayLine, NetCtl
from server.protocol import DASHBOARD_CONTROLS, Command, ProtocolError, parse_control
from server.rescue_world import RescueWorld

IDLE = Command(0, 0.0, False, 0.0, 0.0, 0.0, False)
FULL = Command(0, 0.0, True, 0.0, 0.0, -1.0, False)       # clutch held, full throttle


@pytest.fixture(scope="module")
def world():
    return RescueWorld("flood")


def stop_test(w, latency_s):
    """Drive north out of the camp at full speed, put a person 8 m ahead, and let the
    (delayed) AI stop act exactly like the server's safety layer would."""
    w.reset()
    for _ in range(300):                       # 3 s of full throttle -> 8 m/s
        w.step(0.01, True, FULL)
    assert w.v > 7.5
    w.dashboard_msg({"action": "person_on_road", "ai_latency_s": latency_s, "profile": "test"})
    held = False
    for _ in range(600):
        held = held or w.hazard_reason() == "ai"
        w.step(0.01, not held, FULL)           # the operator keeps the throttle down the whole time
        if w.last_stop:
            return w.last_stop
    raise AssertionError("stop test never finished")


def test_low_latency_stops_well_clear(world):
    r = stop_test(world, 0.05)
    assert r["result"] == "stopped" and r["margin_m"] > 3.5
    assert r["reaction_ms"] == 50 and r["speed_kmh"] > 27


def test_margin_shrinks_as_latency_grows(world):
    margins = [stop_test(world, lat)["margin_m"] for lat in (0.02, 0.1, 0.3)]
    assert margins[0] > margins[1] > margins[2]


def test_satellite_latency_would_hit(world):
    r = stop_test(world, 0.65)
    assert r["result"] == "would have hit"


def test_stationary_rover_is_not_a_valid_test(world):
    world.reset()
    world.dashboard_msg({"action": "person_on_road", "ai_latency_s": 0.02})
    for _ in range(500):
        world.step(0.01, world.hazard_reason() != "ai", IDLE)
    assert world.last_stop["result"] == "stationary"


def test_ai_stop_waits_for_the_pipeline_latency(world):
    world.reset()
    world.dashboard_msg({"action": "person_on_road", "ai_latency_s": 0.2})
    assert world.hazard_reason() is None
    for _ in range(25):
        world.step(0.01, True, IDLE)
    assert world.hazard_reason() == "ai"


# ---------------------------------------------------------------- profiles
def test_profile_mean_delay():
    net = NetCtl()
    net.profile_id = "lte"
    samples = [net.sample_one_way_s() * 1000 for _ in range(4000)]
    assert 35 <= statistics.median(samples) <= 50          # 35 ms one-way + jitter


def test_live_profile_adds_nothing():
    net = NetCtl()
    assert net.profile.live and net.sample_one_way_s() == 0.0


def test_loss_causes_retransmission_stalls():
    net = NetCtl()
    net.profile_id = "wifi_busy"
    samples = [net.sample_jitter_s() for _ in range(20000)]
    stalls = sum(s >= RETX_S for s in samples)
    assert 20 < stalls < 120                               # ~0.3 % of messages


def test_jitter_keeps_messages_in_order():
    async def run():
        net = NetCtl()
        net.profile_id = "wifi_busy"
        got = []

        async def deliver(x):
            got.append(x)
        line = DelayLine(net, deliver).start()
        for i in range(60):
            line.put(i)
            await asyncio.sleep(0.002)
        await asyncio.sleep(0.6)
        line.stop()
        return got
    got = asyncio.run(run())
    assert got == sorted(got) and len(got) == 60            # TCP-like: in order, nothing dropped


def test_profiles_are_sorted_by_latency_for_the_comparison():
    rtts = [p.rtt_ms for p in PROFILES.values()]
    assert PROFILES["p5g_mec"].live and rtts[0] == 0
    assert PROFILES["satellite"].rtt_ms == max(rtts)


# ---------------------------------------------------------------- server
def test_set_profile_and_ai_latency(world):
    from server.main import Server
    srv = Server({"flood": world}, "flood")
    srv.log.start = lambda *a, **k: "test"
    assert parse_control({"action": "set_profile", "value": "lte"}, DASHBOARD_CONTROLS)["value"] == "lte"
    with pytest.raises(ProtocolError):
        parse_control({"action": "set_profile", "value": "lte!"}, DASHBOARD_CONTROLS)
    srv.dashboard_control({"action": "set_profile", "value": "satellite"})
    assert srv.net.profile_id == "satellite"
    srv.dashboard_control({"action": "set_profile", "value": "nonsense"})
    assert srv.net.profile_id == "satellite"
    world.reset()
    srv.dashboard_control({"action": "person_on_road"})
    assert world.stop_test["lat"] >= 0.6 and world.stop_test["profile"] == "satellite"
    srv.base_rtt_ms = 12.0
    srv.net.profile_id = "p5g_mec"
    assert 0.04 <= srv.ai_pipeline_latency_s() <= 0.05     # 12 ms real link + 30 ms inference


def test_results_are_recorded(world):
    from server.main import Server
    srv = Server({"flood": world}, "flood")
    srv.log.row = lambda *a, **k: None
    world.reset()
    world.stop_results.append({"result": "stopped", "margin_m": 4.0, "profile": "lte"})
    srv.sim_tick(0.01)
    assert srv.results and srv.results[-1]["kind"] == "stop_test" and srv.results[-1]["network"].startswith("4G")
