"""``simkit`` command line — run the oracle without the orchestrator.

Responsibility
--------------
Give humans and agents the same interface to the simulator. Devin sessions
reproduce failures by invoking these commands, which means the CLI is not a
convenience — it is the agent-facing API of the oracle, and every role prompt
references it.

Commands
--------
``simkit run``    Run one scenario by seed. The reproduction path.
``simkit suite``  Run the full matrix and print the score table.
``simkit models`` List/inspect resolvable robot models.
``simkit record`` Re-run one seed and write an mp4.

Design constraints
------------------
* Every command must be reproducible from flags alone — no hidden state.
* Human-readable by default, ``--json`` for machine consumption. Agents parse
  the JSON; the table is what a judge sees on the projector.
* Non-zero exit on scenario failure so it composes as a real CI step.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for all subcommands."""
    raise NotImplementedError
    # TODO(build): subparsers for run/suite/models/record with the flags named
    # in this module's docstring and referenced from devin/prompts/*.md.


def cmd_run(args: argparse.Namespace) -> int:
    """``simkit run --model M --harness H --seed N`` — one scenario."""
    raise NotImplementedError
    # TODO(build): rebuild params via scenarios.replay, run, print diagnosis.


def cmd_suite(args: argparse.Namespace) -> int:
    """``simkit suite --config robotci.yaml`` — the full matrix."""
    raise NotImplementedError
    # TODO(build): generate scenarios, run_suite, print the score table.


def cmd_models(args: argparse.Namespace) -> int:
    """``simkit models list|show`` — inspect the Menagerie index."""
    raise NotImplementedError
    # TODO(build): delegate to models.menagerie.


def cmd_record(args: argparse.Namespace) -> int:
    """``simkit record --seed N -o out.mp4`` — evidence for one scenario."""
    raise NotImplementedError
    # TODO(build): delegate to recorder.record_scenario.


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for the ``simkit`` console script."""
    raise NotImplementedError
    # TODO(build): parse args, dispatch to cmd_*, return exit code.


if __name__ == "__main__":
    raise SystemExit(main())
