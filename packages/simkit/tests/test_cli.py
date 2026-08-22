"""The CLI is the reproduction surface: a seed and flags must be enough."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from simkit import cli

CONFIG = {
    "robot": {"model_path": ""},
    "task": {
        "name": "pick_and_place",
        "rate_hz": 50,
        "success": [
            {"id": "object_in_bin"},
            {"id": "within_time", "limit_s": 2.0},
        ],
    },
    "scenarios": {
        "count": 3,
        "seed": 1337,
        "randomize": {
            "object_mass_kg": [0.2, 0.6],
            "friction": [0.5, 1.0],
        },
    },
    "policy": {"pass_threshold": 1.0, "record_video": "none"},
}


@pytest.fixture
def config_file(tmp_path, toy_arm) -> Path:
    config = json.loads(json.dumps(CONFIG))
    config["robot"]["model_path"] = str(toy_arm)
    path = tmp_path / "robotci.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


class Run(NamedTuple):
    code: int
    out: str
    err: str


def run(argv: list[str], capsys) -> Run:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return Run(code, captured.out, captured.err)


def test_run_reports_failure_with_a_non_zero_exit(
    config_file, toy_arm, sweep_harness, capsys
) -> None:
    code, out, _ = run(
        [
            "run",
            "--config",
            str(config_file),
            "--model",
            str(toy_arm),
            "--harness",
            str(sweep_harness),
            "--seed",
            "4242",
            "--json",
        ],
        capsys,
    )
    payload = json.loads(out)
    assert payload["seed"] == 4242
    assert payload["status"] in {"passed", "failed"}
    assert payload["criteria"], "criteria must be reported, not just a verdict"
    # A failing scenario is a non-zero exit so CI can gate on it.
    assert code == (0 if payload["status"] == "passed" else cli.EXIT_SCENARIO_FAILED)
    assert payload["diagnosis"] or payload["status"] == "passed"


def test_run_is_reproducible_from_flags_alone(toy_arm, sweep_harness, capsys) -> None:
    argv = [
        "run",
        "--model",
        str(toy_arm),
        "--harness",
        str(sweep_harness),
        "--seed",
        "99",
        "--param",
        "object_mass_kg=0.35",
        "--json",
    ]
    first = json.loads(run(argv, capsys).out)
    second = json.loads(run(argv, capsys).out)
    assert first["params"]["object_mass_kg"] == 0.35
    first.pop("duration_s", None)
    second.pop("duration_s", None)
    assert first == second


def test_run_oracle_error_exits_differently_from_a_scenario_failure(
    toy_arm, tmp_path, capsys
) -> None:
    code, out, _ = run(
        [
            "run",
            "--model",
            str(toy_arm),
            "--harness",
            str(tmp_path / "absent.py"),
            "--seed",
            "1",
            "--json",
        ],
        capsys,
    )
    assert json.loads(out)["status"] == "error"
    assert code == cli.EXIT_ERROR
    assert cli.EXIT_ERROR != cli.EXIT_SCENARIO_FAILED


def test_suite_runs_the_matrix_from_the_config(
    config_file, sweep_harness, capsys
) -> None:
    code, out, _ = run(
        [
            "suite",
            "--config",
            str(config_file),
            "--harness",
            str(sweep_harness),
            "--parallel",
            "3",
            "--json",
        ],
        capsys,
    )
    payload = json.loads(out)
    assert payload["summary"]["total"] == 3
    assert len(payload["results"]) == 3
    assert payload["base_seed"] == 1337
    assert [r["index"] for r in payload["results"]] == [0, 1, 2]
    assert code in {0, cli.EXIT_SCENARIO_FAILED}


def test_suite_human_output_shows_seeds_and_diagnoses(
    config_file, sweep_harness, capsys
) -> None:
    out = run(
        ["suite", "--config", str(config_file), "--harness", str(sweep_harness)],
        capsys,
    ).out
    assert "seed" in out
    assert "passed" in out
    assert "nominal" in out, "labels make the table readable"


def test_suite_without_a_model_is_an_error(tmp_path, sweep_harness, capsys) -> None:
    config = tmp_path / "empty.yaml"
    config.write_text("task:\n  name: pick_and_place\n")
    assert (
        run(
            ["suite", "--config", str(config), "--harness", str(sweep_harness)], capsys
        ).code
        == cli.EXIT_ERROR
    )


def test_models_list_and_show(menagerie_dir, capsys) -> None:
    listed = run(["models", "list", "--json"], capsys)
    assert listed.code == 0
    assert len(json.loads(listed.out)["models"]) > 30

    shown = run(["models", "show", "franka_emika_panda", "--json"], capsys)
    payload = json.loads(shown.out)
    assert shown.code == 0
    assert payload["dof"] == 9
    assert Path(payload["resolved_path"]).is_file()


def test_models_show_suggests_near_misses(menagerie_dir, capsys) -> None:
    result = run(["models", "show", "panda"], capsys)
    assert result.code == cli.EXIT_ERROR
    assert "franka_emika_panda" in result.err


def test_record_writes_a_video(toy_arm, sweep_harness, tmp_path, capsys) -> None:
    out_path = tmp_path / "clip.mp4"
    code, out, _ = run(
        [
            "record",
            "--model",
            str(toy_arm),
            "--harness",
            str(sweep_harness),
            "--seed",
            "5",
            "-o",
            str(out_path),
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert Path(json.loads(out)["video_path"]) == out_path
    assert out_path.stat().st_size > 1000


def test_bad_config_is_reported_not_traced(tmp_path, sweep_harness, capsys) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text("- just\n- a list\n")
    result = run(
        ["suite", "--config", str(config), "--harness", str(sweep_harness)], capsys
    )
    assert result.code == cli.EXIT_ERROR
    assert "mapping" in result.err


def test_parse_params_reads_scalars_and_strings() -> None:
    parsed = cli.parse_params(["object_mass_kg=0.4", "latency_steps=2", "label=nom"])
    assert parsed == {"object_mass_kg": 0.4, "latency_steps": 2, "label": "nom"}
    with pytest.raises(ValueError, match="KEY=VALUE"):
        cli.parse_params(["oops"])


def test_axes_of_flattens_nested_randomization() -> None:
    axes = cli.axes_of(CONFIG)
    assert axes["object_mass_kg"] == (0.2, 0.6)
    assert axes["friction"] == (0.5, 1.0)
