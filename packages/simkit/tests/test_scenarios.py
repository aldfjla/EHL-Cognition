"""Scenario generation must be reproducible across processes and versions."""

from __future__ import annotations

import subprocess
import sys

import pytest
from simkit import scenarios

AXES = {
    "object_mass_kg": (0.1, 0.8),
    "friction": (0.4, 1.2),
    "object_position.x": (-0.15, 0.15),
}


def test_derive_seed_is_stable_across_processes() -> None:
    """`hash()` is randomized per process; derive_seed must not be."""
    expected = [scenarios.derive_seed(1337, i) for i in range(4)]
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from simkit import scenarios;"
                "print([scenarios.derive_seed(1337, i) for i in range(4)])"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "random", "PATH": "/usr/bin:/bin"},
    )
    assert out.stdout.strip() == str(expected)


def test_derive_seed_is_deterministic_and_distinct() -> None:
    seeds = [scenarios.derive_seed(7, i) for i in range(64)]
    assert seeds == [scenarios.derive_seed(7, i) for i in range(64)]
    assert len(set(seeds)) == len(seeds)
    assert all(0 <= s <= 0xFFFFFFFF for s in seeds)
    assert scenarios.derive_seed(7, 0) != scenarios.derive_seed(8, 0)


def test_scenario_zero_is_the_nominal_midpoint() -> None:
    first = scenarios.generate("run", 1337, 5, AXES)[0]
    assert first["label"] == "nominal"
    assert first["params"] == {
        "friction": pytest.approx(0.8),
        "object_mass_kg": pytest.approx(0.45),
        "object_position.x": pytest.approx(0.0),
    }


def test_generate_matches_contract_shape() -> None:
    generated = scenarios.generate("run7", 1337, 3, AXES)
    for index, scenario in enumerate(generated):
        assert scenario["run_id"] == "run7"
        assert scenario["index"] == index
        assert scenario["status"] == "pending"
        assert scenario["seed"] == scenarios.derive_seed(1337, index)
        assert set(scenario["params"]) == set(AXES)
        for axis, (low, high) in AXES.items():
            assert low <= scenario["params"][axis] <= high


def test_sampling_is_stratified_not_clustered() -> None:
    """Every axis should spread over its range, not pile up in one band."""
    generated = scenarios.generate("run", 99, 16, AXES, include_nominal=False)
    for axis, (low, high) in AXES.items():
        fractions = [(s["params"][axis] - low) / (high - low) for s in generated]
        thirds = {min(int(f * 3), 2) for f in fractions}
        assert thirds == {0, 1, 2}, f"{axis} never left {thirds}"


def test_default_axes_are_deterministic_and_diverse() -> None:
    first = scenarios.generate("run", 2025, 50, scenarios.DEFAULT_AXES)
    second = scenarios.generate("run", 2025, 50, scenarios.DEFAULT_AXES)
    assert first == second
    assert len({str(item["params"]) for item in first}) == 50
    for axis, (low, high) in scenarios.DEFAULT_AXES.items():
        values = [float(item["params"][axis]) for item in first]
        fractions = [(value - low) / (high - low) for value in values]
        assert {min(int(fraction * 3), 2) for fraction in fractions} == {0, 1, 2}
        if not float(low).is_integer() or not float(high).is_integer():
            assert len(set(values)) >= 20


def test_replay_reproduces_generated_params() -> None:
    generated = scenarios.generate("run", 4242, 8, AXES)
    for scenario in generated:
        replayed = scenarios.replay(4242, scenario["index"], AXES)
        assert replayed["params"] == scenario["params"]
        assert replayed["seed"] == scenario["seed"]
        assert replayed["label"] == scenario["label"]


def test_find_index_inverts_derive_seed() -> None:
    seed = scenarios.derive_seed(1337, 11)
    assert scenarios.find_index(1337, seed) == 11
    assert scenarios.find_index(1337, 1, max_index=32) is None


def test_labels_name_the_extremes() -> None:
    generated = scenarios.generate("run", 5, 12, AXES, include_nominal=False)
    labels = {s["label"] for s in generated}
    assert any("friction" in label for label in labels)
    assert all(label for label in labels)


def test_inverted_axis_is_rejected() -> None:
    with pytest.raises(ValueError, match="inverted range"):
        scenarios.generate("run", 1, 2, {"friction": (1.2, 0.4)})
    with pytest.raises(ValueError, match="non-negative"):
        scenarios.generate("run", 1, -1, AXES)
