"""Deterministic sampling of randomized world parameters.

Responsibility
--------------
Turn the axis ranges chosen by the QA Lead agent into N concrete, reproducible
scenarios.

Inputs:  a base seed, a count, and ``{axis: (low, high)}`` ranges.
Outputs: a list of :class:`~orchestrator.schemas.Scenario`-shaped dicts, each
         with its own derived seed.

Why the sampling is here and not in the agent
---------------------------------------------
Reproducibility is the property that makes the whole system work: an
Investigator can only debug a failure it can re-run. So the agent chooses
*ranges* and this module derives every concrete world from a single integer.
``(base_seed, index)`` fully determines a scenario — replaying it needs no
stored state, just the seed, which is what makes the CLI's ``--seed`` flag
enough to reproduce anything the suite found.

Sampling uses stratified (Latin-hypercube-style) draws rather than uniform
random: with only ~24 samples over several axes, uniform sampling leaves
visible gaps, and a gap is a bug that ships.
"""

from __future__ import annotations

from typing import Any


def derive_seed(base_seed: int, index: int) -> int:
    """Stable per-scenario seed from the run's base seed and the index."""
    raise NotImplementedError
    # TODO(build): hash (base_seed, index) into a 32-bit int; must be stable
    # across python versions, so no built-in hash().


def generate(
    run_id: str,
    base_seed: int,
    count: int,
    axes: dict[str, tuple[float, float]],
    include_nominal: bool = True,
) -> list[dict[str, Any]]:
    """Sample ``count`` scenarios over ``axes``.

    When ``include_nominal``, scenario 0 is the midpoint of every axis so that a
    total failure is distinguishable from an edge-case failure.
    """
    raise NotImplementedError
    # TODO(build): stratified sample per axis, shuffle per-axis with the derived
    # seed, build scenario dicts with label + params + seed.


def label(params: dict[str, Any], axes: dict[str, tuple[float, float]]) -> str:
    """Short human name from where the params sit in their ranges.

    e.g. ``"heavy payload, low friction"`` — this string is what makes the
    ScenarioMatrix tooltips readable instead of a wall of floats.
    """
    raise NotImplementedError
    # TODO(build): bucket each param into low/mid/high, name the extremes only.


def replay(base_seed: int, index: int, axes: dict[str, tuple[float, float]]) -> dict:
    """Rebuild one scenario's params from scratch. The reproducibility path."""
    raise NotImplementedError
    # TODO(build): must return exactly what generate() produced at that index.
