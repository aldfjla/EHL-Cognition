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

from orchestrator.schemas import Cluster, Scenario


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
    raise NotImplementedError
    # TODO(build): signature -> group -> merge tail -> label; return Clusters.


def failure_signature(scenario: Scenario) -> str:
    """Stable key for grouping: failed criterion ids + normalised diagnosis."""
    raise NotImplementedError
    # TODO(build): sorted failed criterion ids, joined with normalise_diagnosis.


def normalise_diagnosis(diagnosis: str) -> str:
    """Strip run-specific detail so two instances of one bug collide.

    Removes numbers, units, coordinates and object indices; lowercases the rest.
    ``"Gripper closed 40mm early at (0.31, -0.05)"`` and ``"Gripper closed 12mm
    early at (0.22, 0.08)"`` must produce the same string.
    """
    raise NotImplementedError
    # TODO(build): regex out floats/ints/coords/units, collapse whitespace.


def correlate_params(scenarios: list[Scenario]) -> dict[str, tuple[float, float]]:
    """Find randomized parameters whose range is unusually narrow in a cluster.

    Returns ``{param_name: (low, high)}`` for parameters that discriminate this
    cluster from the passing population — the evidence behind a label like
    "only when payload > 0.6 kg".
    """
    raise NotImplementedError
    # TODO(build): compare per-param min/max against the full suite spread.


def label_cluster(scenarios: list[Scenario]) -> str:
    """Short human name for a cluster, e.g. 'gripper slips on heavy payloads'."""
    raise NotImplementedError
    # TODO(build): combine the dominant failed criterion with correlate_params.
