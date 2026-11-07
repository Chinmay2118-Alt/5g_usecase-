import pytest

from server import protocol as P

GOOD = {"type": "command", "seq": 3, "t_op": 12.5, "clutch": True,
        "vx": 0.5, "vy": 0, "vz": -1, "gripper": False}


def test_parse_good_command():
    c = P.parse_command(P.parse(P.dumps(GOOD)))
    assert c.seq == 3 and c.clutch and c.vz == -1.0


@pytest.mark.parametrize("bad", [
    {"seq": -1}, {"seq": 1.5}, {"seq": True}, {"vx": 1.5}, {"vy": float("nan")},
    {"clutch": 1}, {"gripper": "yes"}, {"t_op": None},
])
def test_reject_bad_command(bad):
    with pytest.raises(P.ProtocolError):
        P.parse_command({**GOOD, **bad})


def test_reject_non_object():
    for raw in ("[]", "nope", '{"no_type": 1}'):
        with pytest.raises(P.ProtocolError):
            P.parse(raw)


def test_operator_cannot_use_dashboard_controls():
    with pytest.raises(P.ProtocolError):
        P.parse_control({"action": "set_delay_ms", "value": 100}, P.OPERATOR_CONTROLS)
    with pytest.raises(P.ProtocolError):
        P.parse_control({"action": "cut_link", "value": True}, P.OPERATOR_CONTROLS)


def test_ai_source_can_only_stop():
    # The simulated AI path only ever produces 'stop'; arming is not a dashboard action.
    assert "arm" not in P.DASHBOARD_CONTROLS
    c = P.parse_control({"action": "stop", "source": "ai"}, P.DASHBOARD_CONTROLS)
    assert c == {"action": "stop", "source": "ai"}


def test_start_run_sanitises_names():
    c = P.parse_control({"action": "start_run", "participant": "P01/../x", "label": "a<b>"},
                        P.DASHBOARD_CONTROLS)
    assert c["participant"] == "P01x" and c["label"] == "ab"
