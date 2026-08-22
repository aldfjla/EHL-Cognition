"""Group failing scenarios by suspected shared root cause.

Responsibility
--------------
Turn N failing scenarios into K clusters, where K is how many Investigator
agents we spend. This is the cost control valve of the whole system: 20 failing
scenarios caused by one bug should cost one agent, not twenty.

Inputs:  failed :class:`~orchestrator.schemas.Scenario` objects, each carrying
         a ``diagnosis`` string from the oracle and the ``criteria`` that failed.
Outputs: :class:`~orchestrator.schemas.Cluster` objects with ``scenario_ids``
         and a human-readable ``label``.

Approach
--------
Deliberately not an LLM call. Clustering runs on structured signal that simkit
already produced:

1. **Failed-criterion signature** — the set of criterion ids that failed is a
   strong, free grouping key. ``{no_collision}`` and ``{within_time}`` failures
   are almost never the same bug.
2. **Normalised diagnosis text** — strip numbers and object names from the
   diagnosis, then group on the remainder.
3. **Parameter correlation** — if every failure in a candidate group sits at
   one end of a randomized range (all low-friction, all heavy), that is a
   cluster and the range boundary belongs in the label.

Step 3 is what makes the demo land: "fails only when friction < 0.6" is a
sentence a human engineer recognises as real debugging.
"""

from __future__ import annotations

import re

from orchestrator.schemas import Cluster, Scenario, ScenarioStatus

#: A parameter discriminates a cluster when its spread inside the cluster is at
#: most this fraction of the spread across the whole population handed in.
NARROW_FRACTION = 0.5

#: Coordinate tuples: ``(0.31, -0.05)``.
_COORDS = re.compile(r"\(\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)+\s*\)")
#: A number, with or without a trailing unit.
_NUMBER = re.compile(
    r"-?\d+(?:\.\d+)?\s*(?:mm|cm|m|km|ms|s|hz|kg|g|nm|n|deg|rad|%)?\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^a-z ]+")
_SPACE = re.compile(r"\s+")

_FAILING = (ScenarioStatus.FAILED, ScenarioStatus.ERROR)


def cluster_failures(
    run_id: str,
    scenarios: list[Scenario],
    max_clusters: int = 6,
) -> list[Cluster]:
    """Partition failing scenarios into at most ``max_clusters`` groups.

    ``max_clusters`` should be ``MAX_PARALLEL_AGENTS`` — the fan-out ceiling.
    When there are more distinct signatures than slots, the largest clusters
    win and the tail is merged into a catch-all so nothing is silently dropped.
    """
    failures = [s for s in scenarios if s.status in _FAILING]
    if not failures or max_clusters < 1:
        return []

    groups: dict[str, list[Scenario]] = {}
    for scenario in failures:
        groups.setdefault(failure_signature(scenario), []).append(scenario)

    # Largest first, signature as the tie-break so a re-run clusters identically.
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    if len(ranked) > max_clusters:
        head, tail = ranked[: max_clusters - 1], ranked[max_clusters - 1 :]
        merged: list[Scenario] = [s for _, group in tail for s in group]
        ranked = head + [("mixed", merged)]

    clusters: list[Cluster] = []
    for signature, group in ranked:
        ordered = sorted(group, key=lambda s: s.index)
        label = (
            f"{len(ordered)} assorted failures"
            if signature == "mixed"
            else label_cluster(ordered)
        )
        clusters.append(
            Cluster(
                run_id=run_id,
                label=label,
                signature=signature,
                scenario_ids=[s.id for s in ordered],
                size=len(ordered),
            )
        )
    return clusters


def failure_signature(scenario: Scenario) -> str:
    """Stable key for grouping: failed criterion ids + normalised diagnosis."""
    failed = sorted(c.id for c in scenario.criteria if not c.passed)
    if scenario.status is ScenarioStatus.ERROR and not failed:
        failed = ["sim_error"]
    return "+".join(failed) + "|" + normalise_diagnosis(scenario.diagnosis or "")


def normalise_diagnosis(diagnosis: str) -> str:
    """Strip run-specific detail so two instances of one bug collide.

    Removes numbers, units, coordinates and object indices; lowercases the rest.
    ``"Gripper closed 40mm early at (0.31, -0.05)"`` and ``"Gripper closed 12mm
    early at (0.22, 0.08)"`` must produce the same string.
    """
    text = _COORDS.sub(" ", diagnosis.lower())
    text = _NUMBER.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def correlate_params(scenarios: list[Scenario]) -> dict[str, tuple[float, float]]:
    """Find randomized parameters whose range is unusually narrow in a cluster.

    Returns ``{param_name: (low, high)}`` for parameters that discriminate this
    cluster from the passing population — the evidence behind a label like
    "only when payload > 0.6 kg".

    Pass the whole suite to compare the cluster against the scenarios that
    passed; pass only the cluster to fall back on absolute narrowness.
    """
    failures = [s for s in scenarios if s.status in _FAILING]
    if not failures:
        return {}
    population = scenarios if len(failures) < len(scenarios) else failures

    discriminating: dict[str, tuple[float, float]] = {}
    for name in sorted(_numeric_params(failures)):
        values = _values(failures, name)
        if not values:
            continue
        low, high = min(values), max(values)
        reference = _values(population, name)
        ref_spread = max(reference) - min(reference) if reference else 0.0
        spread = high - low
        if ref_spread > 0:
            narrow = spread / ref_spread <= NARROW_FRACTION
        else:
            scale = max(abs(low), abs(high)) or 1.0
            narrow = spread / scale <= NARROW_FRACTION
        if narrow:
            discriminating[name] = (low, high)
    return discriminating


def label_cluster(scenarios: list[Scenario]) -> str:
    """Short human name for a cluster, e.g. 'gripper slips on heavy payloads'."""
    if not scenarios:
        return "empty cluster"

    counts: dict[str, int] = {}
    for scenario in scenarios:
        for criterion in scenario.criteria:
            if not criterion.passed:
                counts[criterion.id] = counts.get(criterion.id, 0) + 1
    if counts:
        dominant = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        head = dominant.replace("_", " ")
    else:
        head = normalise_diagnosis(scenarios[0].diagnosis or "") or "unexplained"
        head = " ".join(head.split()[:6])

    params = correlate_params(scenarios)
    if params:
        name, (low, high) = next(iter(params.items()))
        axis = name.replace("_", " ")
        span = f"{_fmt(low)}" if low == high else f"{_fmt(low)}–{_fmt(high)}"
        return f"{head} when {axis} {span}"
    return head


def _numeric_params(scenarios: list[Scenario]) -> set[str]:
    """Params present with a numeric value on every scenario handed in."""
    shared: set[str] | None = None
    for scenario in scenarios:
        numeric = {
            key
            for key, value in scenario.params.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        shared = numeric if shared is None else shared & numeric
    return shared or set()


def _values(scenarios: list[Scenario], name: str) -> list[float]:
    return [
        float(s.params[name])
        for s in scenarios
        if isinstance(s.params.get(name), (int, float))
        and not isinstance(s.params.get(name), bool)
    ]


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
