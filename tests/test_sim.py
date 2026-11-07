import itertools
import random

import pytest

from server.sim import BIN, HALF, WORKSPACE, Sim, fk, ik


@pytest.mark.parametrize("p", list(itertools.product(
    [WORKSPACE["x"][0], 0.0, WORKSPACE["x"][1]],
    [WORKSPACE["y"][0], 0.15, WORKSPACE["y"][1]],
    [WORKSPACE["z"][0], 0.25, WORKSPACE["z"][1]])))
def test_ik_reaches_whole_workspace(p):
    assert fk(ik(*p)) == pytest.approx(p, abs=1e-6)


def run(sim, seconds, vel=None, grip=None, dt=0.01):
    for _ in range(int(round(seconds / dt))):
        sim.step(dt, vel, grip)


def move_to(sim, target, grip):
    """Drive the tip to target with a simple P-controller through step()."""
    for _ in range(2000):
        err = [t - c for t, c in zip(target, sim.tip)]
        if max(abs(e) for e in err) < 0.002:
            return
        sim.step(0.01, tuple(max(-1, min(1, e * 20)) for e in err), grip)
    raise AssertionError(f"did not reach {target}, at {sim.tip}")


def test_workspace_clamp():
    s = Sim()
    run(s, 5.0, vel=(1, 1, 1), grip=False)
    assert s.tip == [WORKSPACE["x"][1], WORKSPACE["y"][1], WORKSPACE["z"][1]]
    run(s, 5.0, vel=(-1, -1, -1), grip=False)
    assert s.tip == [WORKSPACE["x"][0], WORKSPACE["y"][0], WORKSPACE["z"][0]]


def test_no_motion_when_not_allowed():
    s = Sim()
    before = list(s.tip)
    run(s, 1.0)
    assert s.tip == before and s.task_start is None


def pick_and_place(s, cube_id):
    c = s.cubes[cube_id]
    x, _, z = c.pos
    move_to(s, (x, 0.12, z), False)
    move_to(s, (x, HALF + 0.015, z), False)
    run(s, 0.05, vel=(0, 0, 0), grip=True)
    assert s.held == cube_id
    move_to(s, (x, 0.15, z), True)
    move_to(s, (BIN["x"], 0.15, BIN["z"]), True)
    run(s, 0.05, vel=(0, 0, 0), grip=False)
    run(s, 1.0, vel=(0, 0, 0), grip=False)


def test_full_task_scores_and_times():
    s = Sim()
    pick_and_place(s, 0)
    assert s.cubes[0].in_bin and s.score == 1 and not s.cubes[0].falling
    pick_and_place(s, 1)
    assert s.score == 2 and s.task_time is not None and s.task_time > 0
    names = [e[1] for e in s.events]
    assert names.count("grasp") == 2 and names.count("scored") == 2 and "task_complete" in names


def test_grasp_fails_when_far_away():
    s = Sim()
    run(s, 0.05, vel=(0, 0, 0), grip=True)   # home pose is far above every cube
    assert s.held is None


def test_drop_outside_bin_does_not_score():
    s = Sim()
    c = s.cubes[0]
    move_to(s, (c.pos[0], HALF + 0.015, c.pos[2]), False)
    run(s, 0.05, vel=(0, 0, 0), grip=True)
    move_to(s, (-0.25, 0.15, 0.35), True)
    run(s, 0.05, vel=(0, 0, 0), grip=False)
    run(s, 1.0, vel=(0, 0, 0), grip=False)
    assert s.score == 0 and c.pos[1] == pytest.approx(HALF)


def test_detections_include_person_only_when_intruder():
    s = Sim()
    rng = random.Random(0)
    assert all(d["label"] != "person" for d in s.detections(rng))
    s.intruder = True
    assert any(d["label"] == "person" for d in s.detections(rng))
