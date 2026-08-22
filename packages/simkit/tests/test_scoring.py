"""Scoring must be measured: every number in a diagnosis comes from the trace."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from simkit import scoring
from simkit.scene import BIN_HALF

BIN_POS = np.array([0.55, -0.22, 0.5])
CRITERIA = [
    {"id": "object_in_bin"},
    {"id": "no_collision", "max_force_n": 40.0},
    {"id": "within_time", "limit_s": 3.0},
    {"id": "joint_limits_respected", "margin": 0.95},
]


def fake_scene() -> SimpleNamespace:
    """A scene whose bin site sits at BIN_POS, which is all scoring reads."""
    site_xpos = np.array([[0.0, 0.0, 0.0], BIN_POS])
    return SimpleNamespace(
        handles={"bin_site": 1},
        data=SimpleNamespace(site_xpos=site_xpos, xpos=site_xpos),
    )


def episode(
    *,
    steps: int = 100,
    object_end: np.ndarray | None = None,
    lift: float = 0.0,
    peak_force: float = 1.0,
    peak_speed: float = 1.0,
    dist: float = 0.2,
    sim_time_s: float = 2.0,
) -> SimpleNamespace:
    """A synthetic trace with exactly the shape the runner produces."""
    times = np.linspace(0.0, sim_time_s, steps)
    start = np.array([0.55, 0.12, 0.45])
    end = start if object_end is None else np.asarray(object_end, dtype=float)
    object_pos = np.linspace(start, end, steps)
    object_pos[:, 2] += lift * np.sin(np.linspace(0, np.pi, steps))
    forces = np.full(steps, 0.5)
    forces[steps // 2] = peak_force
    qvel = np.zeros((steps, 2))
    qvel[steps // 4, 1] = peak_speed
    return SimpleNamespace(
        sim_time_s=sim_time_s,
        trace={
            "n": steps,
            "t": times,
            "qpos": np.zeros((steps, 2)),
            "qvel": qvel,
            "object_pos": object_pos,
            "contact_force": forces,
            "contact_pair": ["gripper_left/cube"] * steps,
            "gripper_object_dist": np.full(steps, dist),
            "joint_names": ["shoulder", "wrist_3"],
            "joint_range": np.array([[-2.5, 2.5], [-2.5, 2.5]]),
            "joint_limited": [True, True],
        },
    )


def by_id(outcomes: list[dict]) -> dict[str, dict]:
    return {o["id"]: o for o in outcomes}


def test_successful_episode_passes_everything_with_no_diagnosis() -> None:
    result = episode(object_end=BIN_POS + np.array([0.01, 0.0, 0.02]), lift=0.2)
    outcomes, diagnosis = scoring.evaluate(result, CRITERIA, fake_scene())
    assert diagnosis is None
    assert all(o["passed"] for o in outcomes), outcomes
    assert [o["id"] for o in outcomes] == [c["id"] for c in CRITERIA]


def test_every_criterion_reports_measured_and_threshold() -> None:
    outcomes, _ = scoring.evaluate(episode(), CRITERIA, fake_scene())
    for outcome in outcomes:
        assert outcome["measured"] is not None, outcome
        assert outcome["threshold"] is not None, outcome


def test_object_left_on_table_fails_and_is_described() -> None:
    result = episode(dist=0.19)
    outcomes, diagnosis = scoring.evaluate(result, CRITERIA, fake_scene())
    assert by_id(outcomes)["object_in_bin"]["passed"] is False
    assert "never left the table" in diagnosis
    assert "190mm" in diagnosis  # the measured closest approach


def test_object_lifted_but_dropped_short_reports_the_distance() -> None:
    result = episode(object_end=BIN_POS + np.array([0.4, 0.0, 0.0]), lift=0.25)
    outcomes, diagnosis = scoring.evaluate(result, CRITERIA, fake_scene())
    outcome = by_id(outcomes)["object_in_bin"]
    assert outcome["passed"] is False
    assert outcome["measured"] == pytest.approx(0.4, abs=0.02)
    assert "lifted" in diagnosis
    assert str(outcome["measured"]) in diagnosis


def test_collision_failure_names_force_time_and_pair() -> None:
    result = episode(peak_force=120.0, object_end=BIN_POS, lift=0.2)
    outcomes, diagnosis = scoring.evaluate(result, CRITERIA, fake_scene())
    outcome = by_id(outcomes)["no_collision"]
    assert outcome["passed"] is False
    assert outcome["measured"] == pytest.approx(120.0)
    assert outcome["threshold"] == 40.0
    assert "120.0N" in diagnosis
    assert "gripper_left/cube" in diagnosis
    assert "t=1.0" in diagnosis


def test_joint_failure_uses_the_joint_name_not_an_index() -> None:
    result = episode(peak_speed=12.0, object_end=BIN_POS, lift=0.2)
    outcomes, diagnosis = scoring.evaluate(result, CRITERIA, fake_scene())
    outcome = by_id(outcomes)["joint_limits_respected"]
    assert outcome["passed"] is False
    assert outcome["measured"] == pytest.approx(12.0 / 8.0, abs=1e-3)
    assert "wrist_3" in diagnosis
    assert "12.00 rad/s" in diagnosis
    assert "joint1" not in diagnosis


def test_position_limit_violation_is_reported_in_radians() -> None:
    result = episode()
    result.trace["qpos"] = np.tile([0.0, 2.45], (result.trace["n"], 1))
    passed, measured, threshold = scoring.check_joint_limits(result, fake_scene())
    assert passed is False
    assert measured == pytest.approx(0.98, abs=1e-2)
    assert threshold == 0.95
    sentence = scoring.diagnose(
        [{"id": "joint_limits_respected", "threshold": 0.95}], result
    )
    assert "wrist_3" in sentence
    assert "2.450 rad" in sentence
    assert "hard stop" in sentence


def test_gripper_travel_is_not_a_limit_violation() -> None:
    """A closed gripper sits on its stop by design; that is not a violation."""
    result = episode()
    result.trace["joint_range"] = np.array([[-2.5, 2.5], [0.0, 0.04]])
    result.trace["qpos"] = np.tile([0.0, 0.04], (result.trace["n"], 1))
    passed, measured, _ = scoring.check_joint_limits(result, fake_scene())
    assert passed is True
    assert measured < 0.95


def test_timeout_alone_reports_the_budget() -> None:
    result = episode(sim_time_s=4.0, object_end=BIN_POS, lift=0.2)
    outcomes, diagnosis = scoring.evaluate(
        result, [{"id": "within_time", "limit_s": 3.0}], fake_scene()
    )
    assert outcomes[0]["passed"] is False
    assert "3.0s simulated" in diagnosis


def test_diagnosis_leads_with_the_earliest_offence() -> None:
    """A timeout caused by a missed grasp must read as a missed grasp."""
    result = episode(sim_time_s=4.0, dist=0.15)
    _, diagnosis = scoring.evaluate(result, CRITERIA, fake_scene())
    assert diagnosis.startswith("The object never left the table")
    assert len(diagnosis.split(". ")) <= 2  # one or two sentences, not a log


def test_unknown_criterion_fails_loudly_instead_of_passing() -> None:
    outcomes, diagnosis = scoring.evaluate(
        episode(), [{"id": "robot_is_happy"}], fake_scene()
    )
    assert outcomes[0]["passed"] is False
    assert "robot_is_happy" in outcomes[0]["detail"]
    assert "robot_is_happy" in diagnosis


def test_missing_scene_cannot_pass_a_goal_criterion() -> None:
    passed, measured, _ = scoring.check_object_in_bin(episode(), None)
    assert passed is False
    assert measured == float("inf")


def test_bin_tolerance_widens_the_goal_volume() -> None:
    just_outside = BIN_POS + np.array([float(np.max(BIN_HALF[:2])) + 0.03, 0.0, 0.02])
    result = episode(object_end=just_outside, lift=0.2)
    assert scoring.check_object_in_bin(result, fake_scene())[0] is False
    assert (
        scoring.check_object_in_bin(result, fake_scene(), tolerance_m=0.05)[0] is True
    )


def test_register_criterion_extends_the_table() -> None:
    scoring.register_criterion("always_ok", lambda result, scene, **kw: (True, 1, 1))
    try:
        outcomes, diagnosis = scoring.evaluate(episode(), [{"id": "always_ok"}])
        assert outcomes[0]["passed"] is True
        assert diagnosis is None
    finally:
        scoring.CRITERIA.pop("always_ok")
