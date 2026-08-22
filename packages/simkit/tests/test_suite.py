"""Suite orchestration, aggregation and before/after comparison."""

from __future__ import annotations

import pytest
from simkit import suite
from simkit.runner import EpisodeResult


def result(seed: int, status: str = "passed", **kw) -> EpisodeResult:
    return EpisodeResult(scenario_id=f"s{seed}", seed=seed, status=status, **kw)


def scenarios(count: int) -> list[dict]:
    from simkit import scenarios as gen

    return gen.generate("run", 1337, count, {"object_mass_kg": (0.2, 0.6)})


# -- aggregation ------------------------------------------------------------ #


def test_summarize_counts_each_status_separately() -> None:
    summary = suite.summarize(
        [
            result(1),
            result(2),
            result(3, "failed"),
            result(4, "error", error="renderer died"),
        ]
    )
    assert summary == {
        "total": 4,
        "passed": 2,
        "failed": 1,
        "errored": 1,
        "pass_rate": 0.6667,
    }


def test_pass_rate_excludes_our_own_errors() -> None:
    """An oracle error is not the customer failing; it must not skew the rate."""
    assert suite.summarize([result(1), result(2, "error")])["pass_rate"] == 1.0
    assert suite.summarize([])["pass_rate"] == 0.0


def test_compare_partitions_by_seed() -> None:
    before = [result(1, "failed"), result(2, "failed"), result(3)]
    after = [result(1), result(2, "failed"), result(3, "failed")]
    diff = suite.compare(before, after)
    assert diff["fixed"] == [1]
    assert diff["still_failing"] == [2]
    assert diff["newly_broken"] == [3]
    assert diff["improved"] is False  # a regression is not an improvement
    assert diff["before"]["passed"] == 1
    assert diff["after"]["passed"] == 1


def test_compare_flags_a_clean_improvement() -> None:
    diff = suite.compare([result(1, "failed"), result(2)], [result(1), result(2)])
    assert diff["fixed"] == [1]
    assert diff["newly_broken"] == []
    assert diff["improved"] is True


def test_compare_reports_seeds_present_on_only_one_side() -> None:
    diff = suite.compare([result(1, "failed")], [result(2, "failed")])
    assert diff["only_in_before"] == [1]
    assert diff["only_in_after"] == [2]


# -- execution -------------------------------------------------------------- #


def test_run_suite_returns_results_in_scenario_order(
    toy_arm, sweep_harness, task
) -> None:
    generated = scenarios(4)
    seen: list[dict] = []
    results = suite.run_suite(
        scenarios=generated,
        model_path=str(toy_arm),
        harness_path=str(sweep_harness),
        task=task,
        parallel=4,
        record="none",
        on_progress=seen.append,
    )
    assert [r.seed for r in results] == [s["seed"] for s in generated]
    assert len(seen) == len(generated), "the dashboard needs one event per scenario"
    assert all(r.status in {"passed", "failed"} for r in results)


def test_progress_callback_exceptions_do_not_fail_the_suite(
    toy_arm, sweep_harness, task
) -> None:
    def explode(event: dict) -> None:
        raise RuntimeError("dashboard is down")

    results = suite.run_suite(
        scenarios=scenarios(2),
        model_path=str(toy_arm),
        harness_path=str(sweep_harness),
        task=task,
        parallel=2,
        record="none",
        on_progress=explode,
    )
    assert len(results) == 2


def test_broken_harness_is_an_error_for_every_scenario(toy_arm, task, tmp_path) -> None:
    results = suite.run_suite(
        scenarios=scenarios(2),
        model_path=str(toy_arm),
        harness_path=str(tmp_path / "absent.py"),
        task=task,
        parallel=2,
        record="none",
    )
    assert [r.status for r in results] == ["error", "error"]
    assert suite.summarize(results)["errored"] == 2


def test_empty_scenario_list_is_a_no_op(toy_arm, sweep_harness, task) -> None:
    assert (
        suite.run_suite(
            scenarios=[],
            model_path=str(toy_arm),
            harness_path=str(sweep_harness),
            task=task,
        )
        == []
    )


def test_unknown_record_policy_is_rejected(toy_arm, sweep_harness, task) -> None:
    with pytest.raises(ValueError, match="record must be one of"):
        suite.run_suite(
            scenarios=scenarios(1),
            model_path=str(toy_arm),
            harness_path=str(sweep_harness),
            task=task,
            record="sometimes",
        )
