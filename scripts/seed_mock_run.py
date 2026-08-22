#!/usr/bin/env python3
"""Emit a fake run so the dashboard can be built before the pipeline exists.

    python scripts/seed_mock_run.py                # one run, played in realtime
    python scripts/seed_mock_run.py --instant      # all events at once
    python scripts/seed_mock_run.py --loop         # replay forever

Why this exists
---------------
The UI is the deliverable most likely to be judged, and it must not be blocked
on Devin credentials, MuJoCo rendering, or a working pipeline. This script
writes a plausible run to the store and replays a scripted event sequence onto
the bus with realistic timing, so every component can be built and demoed
against live-looking data from hour one.

It is also the stage-fallback: if the live pipeline fails during the demo, the
same dashboard driven by this script still shows the system's shape honestly —
provided it is labelled as a replay, which ``--instant`` and ``--loop`` both do
in the run title. Never present a seeded run as a live one.

The scripted narrative
----------------------
A 7-DOF arm, 24 scenarios, 5 failures in two clusters:

* cluster A (3 scenarios) — gripper closes on a fixed timer, fails whenever the
  approach is slow (low friction / heavy payload);
* cluster B (2 scenarios) — joint 4 exceeds its velocity limit on the retreat.

Two Investigators, two Fixers, one Tech Lead re-run, one clean suite, one PR.
That arc exercises every component including fan-out, relays and before/after
video.
"""

from __future__ import annotations

import sys


def build_run() -> dict:
    """Construct the mock Run object."""
    raise NotImplementedError
    # TODO(build): a Run with repo/sha/robot_model populated, stage TRIGGERED.


def build_event_script() -> list[tuple[float, str, dict]]:
    """The scripted timeline: ``(delay_s, event_type, data)`` in order.

    Delays are what make the replay convincing — a suite that completes
    instantly does not read as a suite. Keep the whole script under ~90s so it
    matches docs/DEMO_SCRIPT.md.
    """
    raise NotImplementedError
    # TODO(build): stage changes, 24 scenario.finished events staggered over
    # ~15s, cluster creation, 2 agent.created bursts, relay messages between
    # investigators and fixers, a verify pass, report.created, PR_OPENED.


def main(argv: list[str] | None = None) -> int:
    """Persist the mock run and replay its events onto the bus."""
    raise NotImplementedError
    # TODO(build): argparse (--instant/--loop/--speed), create the run via
    # repo.create_run, then publish each event with its delay. Prefix the run
    # title with "[REPLAY]" so a seeded run is never mistaken for a live one.


if __name__ == "__main__":
    sys.exit(main())
