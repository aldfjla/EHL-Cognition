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

Implementation note
-------------------
The stratification is a scrambled Halton sequence: one prime base per axis, the
digits scrambled with a per-axis seed derived from ``base_seed``. That choice is
load-bearing rather than cosmetic — a Latin hypercube needs ``count`` up front to
place its strata, which would make a scenario's parameters depend on how many
siblings it had. Halton is *index-local*: sample ``i`` is a pure function of
``(base_seed, axis, i)``, so :func:`replay` can rebuild scenario 17 of a 24-wide
suite knowing nothing but the base seed, and every prefix of the sequence is
still stratified.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: Halton bases, assigned to axes in sorted-name order so the assignment does
#: not depend on dict insertion order.
_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53)

_MASK32 = 0xFFFFFFFF

# Parameters understood by ``simkit.scene``. Keep this catalogue conservative:
# an axis that the scene ignores would make a scenario look diverse while
# exercising the same world.
DEFAULT_AXES: dict[str, tuple[float, float]] = {
    "friction": (0.2, 1.2),
    "latency_steps": (0, 3),
    "object_mass_kg": (0.1, 2.0),
    "object_position.x": (-0.15, 0.15),
    "object_position.y": (-0.15, 0.15),
    "sensor_noise_std": (0.0, 0.05),
}

#: Words used by :func:`label` for the low/high end of an axis, matched against
#: the axis name. First match wins, so order matters.
_VOCAB: tuple[tuple[str, str, str], ...] = (
    ("mass", "light payload", "heavy payload"),
    ("payload", "light payload", "heavy payload"),
    ("friction", "low friction", "high friction"),
    ("noise", "clean sensors", "noisy sensors"),
    ("latency", "no latency", "high latency"),
    ("delay", "no delay", "long delay"),
    ("damping", "low damping", "high damping"),
    ("gravity", "low gravity", "high gravity"),
    ("position", "near object", "far object"),
    ("pos", "near object", "far object"),
)


def derive_seed(base_seed: int, index: int) -> int:
    """Stable per-scenario seed from the run's base seed and the index.

    Uses BLAKE2b rather than :func:`hash`, whose string/bytes results are salted
    per interpreter process — a seed that changed between runs would make every
    failure irreproducible.
    """
    digest = hashlib.blake2b(
        f"{int(base_seed)}:{int(index)}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest[:4], "big") & _MASK32


def _axis_seed(base_seed: int, axis: str) -> int:
    """Stable per-axis scramble seed. Independent of the scenario index."""
    digest = hashlib.blake2b(
        f"{int(base_seed)}|axis|{axis}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest[:4], "big") & _MASK32


def _scrambled_halton(index: int, base: int, scramble: int) -> float:
    """One scrambled radical-inverse draw in ``[0, 1)``.

    ``index`` is the sample number, ``base`` the Halton base and ``scramble`` a
    per-axis integer that permutes the digits (a random-digit scramble), so two
    axes sharing a base still explore independently.
    """
    result = 0.0
    denominator = 1.0
    n = int(index)
    digit_shift = scramble
    while n > 0:
        denominator *= base
        digit = n % base
        digit_shift = (digit_shift * 1103515245 + 12345) & _MASK32
        digit = (digit + digit_shift) % base
        result += digit / denominator
        n //= base
    return result


def _is_integral(low: float, high: float) -> bool:
    """True when an axis is discrete — ``latency_steps: [0, 3]`` means steps."""
    return float(low).is_integer() and float(high).is_integer()


def _quantize(value: float, low: float, high: float) -> float | int:
    if _is_integral(low, high):
        return round(value)
    return round(value, 6)


def _params_for(
    base_seed: int,
    index: int,
    axes: dict[str, tuple[float, float]],
    include_nominal: bool = True,
) -> dict[str, Any]:
    """The concrete parameters of one scenario. The single source of truth.

    Both :func:`generate` and :func:`replay` route through here, which is what
    guarantees they agree.
    """
    params: dict[str, Any] = {}
    for axis_number, axis in enumerate(sorted(axes)):
        low, high = (float(v) for v in axes[axis])
        if include_nominal and index == 0:
            value = (low + high) / 2.0
        else:
            # Sample 0 of the Halton sequence is the origin of every axis, which
            # would put every parameter at its lower bound simultaneously. Shift
            # by one so no scenario is that degenerate corner.
            base = _PRIMES[axis_number % len(_PRIMES)]
            unit = _scrambled_halton(index + 1, base, _axis_seed(base_seed, axis))
            value = low + unit * (high - low)
        params[axis] = _quantize(value, low, high)
    return params


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

    The returned dicts match ``contracts/schemas/scenario.json``.
    """
    if count < 0:
        raise ValueError(f"count must be non-negative, got {count}")
    for axis, bounds in axes.items():
        low, high = (float(v) for v in bounds)
        if high < low:
            raise ValueError(f"axis {axis!r} has an inverted range: {bounds!r}")

    scenarios: list[dict[str, Any]] = []
    for index in range(count):
        params = _params_for(base_seed, index, axes, include_nominal)
        scenarios.append(
            {
                "id": f"{run_id}-s{index:03d}",
                "run_id": run_id,
                "index": index,
                "seed": derive_seed(base_seed, index),
                "label": (
                    "nominal" if include_nominal and index == 0 else label(params, axes)
                ),
                "params": params,
                "status": "pending",
                "attempt": 1,
            }
        )
    return scenarios


def label(params: dict[str, Any], axes: dict[str, tuple[float, float]]) -> str:
    """Short human name from where the params sit in their ranges.

    e.g. ``"heavy payload, low friction"`` — this string is what makes the
    ScenarioMatrix tooltips readable instead of a wall of floats.
    """
    words: list[str] = []
    for axis in sorted(params):
        if axis not in axes:
            continue
        low, high = (float(v) for v in axes[axis])
        span = high - low
        if span <= 0:
            continue
        fraction = (float(params[axis]) - low) / span
        if 1 / 3 <= fraction <= 2 / 3:
            continue  # mid band: only the extremes are worth naming
        extreme = "low" if fraction < 1 / 3 else "high"
        words.append(_word_for(axis, extreme))
    if not words:
        return "nominal"
    return ", ".join(words)


def _word_for(axis: str, extreme: str) -> str:
    lowered = axis.lower()
    for token, low_word, high_word in _VOCAB:
        if token in lowered:
            return low_word if extreme == "low" else high_word
    return f"{extreme} {axis}"


def replay(base_seed: int, index: int, axes: dict[str, tuple[float, float]]) -> dict:
    """Rebuild one scenario's params from scratch. The reproducibility path.

    Returns ``{"index", "seed", "params", "label"}`` — exactly what
    :func:`generate` produced at that index for the same ``(base_seed, axes)``,
    with ``include_nominal`` at its default.
    """
    params = _params_for(base_seed, index, axes, include_nominal=True)
    return {
        "index": index,
        "seed": derive_seed(base_seed, index),
        "params": params,
        "label": "nominal" if index == 0 else label(params, axes),
    }


def find_index(
    base_seed: int,
    seed: int,
    max_index: int = 4096,
) -> int | None:
    """Invert :func:`derive_seed` by search — ``simkit run --seed N`` needs it.

    An Investigator is handed a seed, not an index; the parameters live at the
    index. Returns ``None`` when no index below ``max_index`` derives ``seed``.
    """
    for index in range(max_index):
        if derive_seed(base_seed, index) == seed:
            return index
    return None
