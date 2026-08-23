"""Clustering on synthetic diagnoses."""

from __future__ import annotations

from orchestrator import clustering
from orchestrator.schemas import CriterionResult, Scenario, ScenarioStatus

RUN = "run_cl"


def scenario(
    index: int,
    status: ScenarioStatus = ScenarioStatus.FAILED,
    diagnosis: str = "",
    failed: tuple[str, ...] = (),
    passed: tuple[str, ...] = (),
    params: dict | None = None,
) -> Scenario:
    criteria = [CriterionResult(id=c, passed=False) for c in failed]
    criteria += [CriterionResult(id=c, passed=True) for c in passed]
    return Scenario(
        run_id=RUN,
        index=index,
        label=f"scenario {index}",
        seed=index,
        status=status,
        diagnosis=diagnosis or None,
        criteria=criteria,
        params=params or {},
    )


def test_normalise_diagnosis_collapses_run_specific_detail() -> None:
    a = clustering.normalise_diagnosis("Gripper closed 40mm early at (0.31, -0.05)")
    b = clustering.normalise_diagnosis("Gripper closed 12mm early at (0.22, 0.08)")
    assert a == b == "gripper closed early at"


def test_passing_scenarios_are_never_clustered() -> None:
    scenarios = [
        scenario(0, ScenarioStatus.PASSED, passed=("no_collision",)),
        scenario(1, ScenarioStatus.PENDING),
    ]
    assert clustering.cluster_failures(RUN, scenarios) == []


def test_same_bug_different_numbers_lands_in_one_cluster() -> None:
    scenarios = [
        scenario(
            i,
            diagnosis=f"Gripper closed {i * 7}mm early at (0.3{i}, -0.0{i})",
            failed=("grasp_success",),
        )
        for i in range(4)
    ]
    scenarios.append(
        scenario(4, diagnosis="Arm exceeded joint limit", failed=("joint_limits",))
    )

    clusters = clustering.cluster_failures(RUN, scenarios, max_clusters=6)

    assert len(clusters) == 2
    assert clusters[0].size == 4
    assert set(clusters[0].scenario_ids) == {s.id for s in scenarios[:4]}
    assert clusters[1].scenario_ids == [scenarios[4].id]


def test_different_failed_criteria_do_not_merge() -> None:
    scenarios = [
        scenario(0, diagnosis="robot dropped the block", failed=("grasp_success",)),
        scenario(1, diagnosis="robot dropped the block", failed=("within_time",)),
    ]
    clusters = clustering.cluster_failures(RUN, scenarios)
    assert len(clusters) == 2


def test_sim_errors_cluster_even_without_criteria() -> None:
    scenarios = [
        scenario(i, ScenarioStatus.ERROR, diagnosis="mujoco step diverged")
        for i in range(3)
    ]
    clusters = clustering.cluster_failures(RUN, scenarios)
    assert len(clusters) == 1
    assert clusters[0].size == 3
    assert "sim_error" in clusters[0].signature


def test_clustering_is_deterministic() -> None:
    scenarios = [
        scenario(i, diagnosis=f"failure kind {i % 3}", failed=(f"c{i % 3}",))
        for i in range(9)
    ]
    first = clustering.cluster_failures(RUN, scenarios, max_clusters=2)
    second = clustering.cluster_failures(RUN, scenarios, max_clusters=2)
    assert [c.signature for c in first] == [c.signature for c in second]
    assert [c.scenario_ids for c in first] == [c.scenario_ids for c in second]


def test_cap_is_respected_and_nothing_is_dropped() -> None:
    scenarios = [
        scenario(i, diagnosis=f"distinct failure {chr(97 + i)}", failed=(f"c{i}",))
        for i in range(10)
    ]
    clusters = clustering.cluster_failures(RUN, scenarios, max_clusters=3)

    assert len(clusters) == 3
    clustered = {sid for c in clusters for sid in c.scenario_ids}
    assert clustered == {s.id for s in scenarios}
    assert sum(c.size for c in clusters) == 10


def test_correlate_params_finds_the_narrow_axis() -> None:
    failures = [
        scenario(
            i,
            diagnosis="slipped",
            failed=("grasp_success",),
            params={"friction": 0.3 + i * 0.02, "payload": 0.1 + i * 0.5},
        )
        for i in range(4)
    ]
    passes = [
        scenario(
            10 + i,
            ScenarioStatus.PASSED,
            passed=("grasp_success",),
            params={"friction": 0.8 + i * 0.1, "payload": 0.2 + i * 0.4},
        )
        for i in range(4)
    ]

    axes = clustering.correlate_params(failures + passes)

    assert "friction" in axes
    low, high = axes["friction"]
    assert low < high <= 0.4
    assert "payload" not in axes


def test_label_mentions_the_dominant_criterion_and_axis() -> None:
    failures = [
        scenario(
            i,
            diagnosis="gripper slipped",
            failed=("grasp_success",),
            params={"friction": 0.30 + i * 0.01},
        )
        for i in range(3)
    ]
    label = clustering.label_cluster(failures)
    assert "grasp success" in label
    assert "friction" in label


def test_cluster_labels_are_human_readable() -> None:
    scenarios = [
        scenario(i, diagnosis="tipped over on the ramp", failed=("stayed_upright",))
        for i in range(2)
    ]
    [cluster] = clustering.cluster_failures(RUN, scenarios)
    assert cluster.label
    assert cluster.label == cluster.label.strip()
    assert "\n" not in cluster.label


def test_criterion_result_accepts_simkit_scoring_dicts() -> None:
    """``simkit.scoring.evaluate`` emits ``measured`` (and sometimes ``detail``)."""
    parsed = CriterionResult.model_validate(
        {
            "id": "object_in_bin",
            "passed": False,
            "measured": 0.1594,
            "threshold": 0.05,
        }
    )
    assert parsed.value == 0.1594
    assert parsed.threshold == 0.05
    unknown = CriterionResult.model_validate(
        {
            "id": "bogus",
            "passed": False,
            "measured": None,
            "threshold": None,
            "detail": "unknown criterion id 'bogus'; nothing was checked",
        }
    )
    assert unknown.detail is not None and "nothing was checked" in unknown.detail
