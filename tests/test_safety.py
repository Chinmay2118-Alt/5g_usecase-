from server.protocol import ARMED, DISARMED, HOLDING, Command
from server.safety import Safety


def cmd(seq, clutch=True, vx=0.5):
    return Command(seq=seq, t_op=0.0, clutch=clutch, vx=vx, vy=0.0, vz=0.0, gripper=False)


def armed_safety(t=0.0):
    s = Safety()
    s.on_command(cmd(0, clutch=False), t)
    ok, why = s.arm(t)
    assert ok, why
    return s


def test_starts_disarmed_and_never_moves():
    s = Safety()
    assert s.state == DISARMED
    d = s.on_command(cmd(0), 0.0)
    assert d.accepted and not d.motion and d.reason == "not_armed"


def test_arm_requires_released_clutch():
    s = Safety()
    s.on_command(cmd(0, clutch=True), 0.0)
    ok, why = s.arm(0.01)
    assert not ok and why == "release_clutch_first"
    s.on_command(cmd(1, clutch=False), 0.02)
    assert s.arm(0.03)[0]
    assert s.state == ARMED


def test_arm_requires_live_commands():
    s = Safety()
    s.on_command(cmd(0, clutch=False), 0.0)
    ok, why = s.arm(1.0)
    assert not ok and why == "no_live_commands"


def test_clutch_gates_motion():
    s = armed_safety()
    assert s.on_command(cmd(1, clutch=True), 0.01).motion
    d = s.on_command(cmd(2, clutch=False), 0.02)
    assert d.accepted and not d.motion and d.reason == "clutch_off"
    assert not s.motion_allowed(cmd(3, clutch=False))


def test_out_of_order_and_duplicate_rejected():
    s = armed_safety()
    assert s.on_command(cmd(5), 0.01).accepted
    assert s.on_command(cmd(5), 0.02).reason == "out_of_order"
    assert s.on_command(cmd(3), 0.03).reason == "out_of_order"
    assert s.on_command(cmd(6), 0.04).accepted


def test_watchdog_trips_after_timeout_not_before():
    s = armed_safety(0.0)
    s.on_command(cmd(1), 1.0)
    s.tick(1.0 + s.watchdog_s - 0.001)
    assert s.state == ARMED
    s.tick(1.0 + s.watchdog_s + 0.01)
    assert s.state == HOLDING and s.reason == "watchdog"
    assert s.hold_latency_ms is not None and s.hold_latency_ms <= 200.0


def test_watchdog_worst_case_under_200ms_with_10ms_tick():
    s = armed_safety(0.0)
    s.on_command(cmd(1), 0.0)
    t = 0.0
    while s.state == ARMED:
        t += 0.010
        s.tick(t)
    assert t * 1000 <= 200.0


def test_holding_needs_rearm_and_commands_do_not_move():
    s = armed_safety(0.0)
    s.tick(1.0)
    assert s.state == HOLDING
    d = s.on_command(cmd(1, clutch=True), 1.01)
    assert not d.motion
    assert not s.arm(1.02)[0]           # clutch still held
    s.on_command(cmd(2, clutch=False), 1.03)
    assert s.arm(1.04)[0] and s.state == ARMED


def test_stop_from_any_source_holds():
    for src in ("operator", "dashboard", "ai", "link"):
        s = armed_safety()
        s.stop(0.01, src)
        assert s.state == HOLDING and s.reason == f"stop_{src}"


def test_new_session_resets_seq_and_holds():
    s = armed_safety()
    s.on_command(cmd(100), 0.01)
    s.new_session(0.02)
    assert s.state == HOLDING
    assert s.on_command(cmd(0), 0.03).accepted


def test_extend_watchdog_grace():
    s = armed_safety(0.0)
    s.on_command(cmd(1), 0.0)
    s.extend_watchdog(0.2)
    s.tick(0.3)
    assert s.state == ARMED
    s.tick(0.39)
    assert s.state == HOLDING
