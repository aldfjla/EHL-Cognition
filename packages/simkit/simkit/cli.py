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
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from simkit import scenarios as scenarios_mod
from simkit import suite as suite_mod
from simkit.models import menagerie

#: Exit code for "the robot failed", distinct from a usage or oracle error.
EXIT_SCENARIO_FAILED = 1
#: Exit code for "the oracle broke" — never blame the customer for our bugs.
EXIT_ERROR = 2

#: Randomization axes used when the config declares none.
DEFAULT_AXES: dict[str, tuple[float, float]] = {
    "object_position.x": (-0.15, 0.15),
    "object_position.y": (-0.10, 0.10),
    "object_mass_kg": (0.1, 0.8),
    "friction": (0.4, 1.2),
}


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for all subcommands."""
    parser = argparse.ArgumentParser(
        prog="simkit",
        description="Deterministic MuJoCo oracle: same (model, harness, seed), "
        "same verdict.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of a human table",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = _subcommand(subparsers, "run", "run one scenario by seed")
    _add_scenario_flags(run)
    run.add_argument("--index", type=int, help="scenario index (implies --seed)")
    run.add_argument("--record", metavar="MP4", help="also write a video here")
    run.add_argument(
        "--max-wall-s",
        type=float,
        default=120.0,
        help="wall-clock watchdog, seconds (default: 120)",
    )
    run.set_defaults(func=cmd_run)

    suite = _subcommand(subparsers, "suite", "run the full scenario matrix")
    suite.add_argument("--config", default="robotci.yaml", help="robotci.yaml path")
    suite.add_argument("--model", help="override the robot MJCF path")
    suite.add_argument("--harness", required=True, help="harness module path")
    suite.add_argument("--count", type=int, help="override scenarios.count")
    suite.add_argument("--seed", type=int, help="override scenarios.seed")
    suite.add_argument("--parallel", type=int, help="worker processes")
    suite.add_argument("--workers", type=int, help="worker processes")
    live_flags = suite.add_mutually_exclusive_group()
    live_flags.add_argument("--live", dest="live", action="store_true")
    live_flags.add_argument("--no-live", dest="live", action="store_false")
    suite.set_defaults(live=False)
    suite.add_argument(
        "--record",
        choices=suite_mod.RECORD_POLICIES,
        help="video policy (default: policy.record_video)",
    )
    suite.add_argument("--run-id", default="local", help="run id for scenario ids")
    suite.set_defaults(func=cmd_suite)

    models = _subcommand(subparsers, "models", "inspect resolvable robot models")
    models.add_argument("action", choices=("list", "show"), nargs="?", default="list")
    models.add_argument("name", nargs="?", help="model name for `show`")
    models.add_argument("--menagerie-dir", help="library location")
    models.add_argument(
        "--refresh", action="store_true", help="rebuild index.json in place"
    )
    models.add_argument("--search", help="filter by fuzzy name/vendor match")
    models.set_defaults(func=cmd_models)

    record = _subcommand(subparsers, "record", "re-run one seed and write mp4")
    _add_scenario_flags(record)
    record.add_argument("--index", type=int, help="scenario index (implies --seed)")
    record.add_argument("-o", "--out", required=True, help="output mp4 path")
    record.set_defaults(func=cmd_record)

    return parser


def _subcommand(subparsers: Any, name: str, help_text: str) -> argparse.ArgumentParser:
    """A subparser that also accepts ``--json`` after the command name."""
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument(
        "--json",
        action="store_true",
        # SUPPRESS so the subcommand flag never overwrites `simkit --json run`.
        default=argparse.SUPPRESS,
        help="emit machine-readable JSON",
    )
    return parser


def _add_scenario_flags(parser: argparse.ArgumentParser) -> None:
    """Flags shared by the two single-scenario commands."""
    parser.add_argument("--model", required=True, help="robot MJCF path")
    parser.add_argument("--harness", required=True, help="harness module path")
    parser.add_argument("--seed", type=int, required=True, help="scenario seed")
    parser.add_argument("--config", help="robotci.yaml, for task and axes")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override one scenario parameter (repeatable)",
    )


def cmd_run(args: argparse.Namespace) -> int:
    """``simkit run --model M --harness H --seed N`` — one scenario."""
    from simkit.runner import run_scenario

    config = load_config(getattr(args, "config", None))
    scenario = resolve_scenario(config, seed=args.seed, index=args.index)
    scenario["params"].update(parse_params(args.param))

    result = run_scenario(
        scenario_id=scenario["id"],
        model_path=args.model,
        harness_path=args.harness,
        params=scenario["params"],
        seed=scenario["seed"],
        task=task_of(config),
        record=args.record or False,
        max_wall_s=args.max_wall_s,
    )
    if args.json:
        _print_json(_result_payload(result, scenario))
    else:
        _print_result(result, scenario)
    return _exit_code(result.status)


def cmd_suite(args: argparse.Namespace) -> int:
    """``simkit suite --config robotci.yaml`` — the full matrix."""
    config = load_config(args.config)
    axes = axes_of(config)
    matrix = config.get("scenarios") or {}
    base_seed = int(args.seed if args.seed is not None else matrix.get("seed", 0))
    count = int(args.count if args.count is not None else matrix.get("count", 12))
    policy = args.record or str(
        (config.get("policy") or {}).get("record_video", "failures")
    )

    model_path = args.model or model_path_of(config)
    if not model_path:
        print(
            "no robot model: pass --model or set robot.model_path/robot.menagerie",
            file=sys.stderr,
        )
        return EXIT_ERROR

    scenarios = scenarios_mod.generate(args.run_id, base_seed, count, axes)
    on_progress = None if args.json else _progress_line
    results = suite_mod.run_suite(
        scenarios=scenarios,
        model_path=model_path,
        harness_path=args.harness,
        task=task_of(config),
        parallel=args.parallel if args.parallel is not None else args.workers,
        record=policy,
        on_progress=on_progress,
        live=args.live,
    )
    stats = suite_mod.summarize(results)

    if args.json:
        _print_json(
            {
                "model_path": model_path,
                "harness_path": args.harness,
                "base_seed": base_seed,
                "summary": stats,
                "results": [
                    _result_payload(result, scenario)
                    for result, scenario in zip(results, scenarios, strict=False)
                ],
            }
        )
    else:
        _print_table(results, scenarios, stats)

    threshold = float((config.get("policy") or {}).get("pass_threshold", 1.0))
    if stats["errored"]:
        return EXIT_ERROR
    return 0 if stats["pass_rate"] >= threshold else EXIT_SCENARIO_FAILED


def cmd_models(args: argparse.Namespace) -> int:
    """``simkit models list|show`` — inspect the Menagerie index."""
    directory = (
        Path(args.menagerie_dir) if args.menagerie_dir else menagerie.default_dir()
    )
    if not directory.is_dir():
        print(f"no model library at {directory}; run `make menagerie`", file=sys.stderr)
        return EXIT_ERROR

    if args.action == "show":
        if not args.name:
            print("models show needs a model name", file=sys.stderr)
            return EXIT_ERROR
        model = menagerie.get(args.name, directory)
        if model is None:
            near = menagerie.search(args.name, directory, limit=5)
            print(f"no model named {args.name!r}", file=sys.stderr)
            if near:
                print(
                    "did you mean: " + ", ".join(m.name for m in near), file=sys.stderr
                )
            return EXIT_ERROR
        payload = asdict(model)
        payload["resolved_path"] = str(menagerie.resolve_model_path(model, directory))
        if args.json:
            _print_json(payload)
        else:
            for key, value in payload.items():
                print(f"{key:>14}: {value}")
        return 0

    models = (
        menagerie.search(args.search, directory, limit=100)
        if args.search
        else menagerie.index(directory, refresh=args.refresh)
    )
    if args.json:
        _print_json(
            {"menagerie_dir": str(directory), "models": [asdict(m) for m in models]}
        )
        return 0
    print(f"{len(models)} models in {directory}")
    for model in models:
        dof = "?" if model.dof is None else str(model.dof)
        print(f"  {model.name:<34} {model.kind:<10} dof={dof:<4} {model.model_path}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """``simkit record --seed N -o out.mp4`` — evidence for one scenario."""
    from simkit.recorder import record_scenario

    config = load_config(getattr(args, "config", None))
    scenario = resolve_scenario(config, seed=args.seed, index=args.index)
    scenario["params"].update(parse_params(args.param))
    path = record_scenario(
        model_path=args.model,
        harness_path=args.harness,
        params=scenario["params"],
        seed=scenario["seed"],
        task=task_of(config),
        out_path=args.out,
    )
    if args.json:
        _print_json({"seed": scenario["seed"], "video_path": path})
    else:
        print(f"wrote {path}")
    return 0


# -- config plumbing -------------------------------------------------------- #


def load_config(path: str | None) -> dict[str, Any]:
    """Read ``robotci.yaml``; an absent path means "all defaults"."""
    if not path:
        return {}
    text = Path(path).read_text()
    loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} must contain a YAML mapping")
    return loaded


def axes_of(config: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Flatten ``scenarios.randomize`` into dotted axis -> (low, high)."""
    randomize = ((config.get("scenarios") or {}).get("randomize")) or {}
    axes: dict[str, tuple[float, float]] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}" if prefix else str(key), value)
        elif isinstance(node, (list, tuple)) and len(node) == 2:
            axes[prefix] = (float(node[0]), float(node[1]))

    walk("", randomize)
    return axes or dict(DEFAULT_AXES)


def task_of(config: dict[str, Any]) -> dict[str, Any]:
    """The task block the runner and scorer need, with control rate folded in."""
    task = dict(config.get("task") or {})
    task.setdefault("name", "pick_and_place")
    task["rate_hz"] = int(
        task.get("rate_hz") or (config.get("control") or {}).get("rate_hz") or 100
    )
    if not task.get("success"):
        task["success"] = [
            {"id": "object_in_bin"},
            {"id": "no_collision"},
            {"id": "within_time"},
            {"id": "joint_limits_respected"},
        ]
    return task


def model_path_of(config: dict[str, Any]) -> str:
    """Resolve ``robot.model_path`` or ``robot.menagerie`` from the config."""
    robot = config.get("robot") or {}
    explicit = robot.get("model_path")
    if explicit:
        return str(explicit)
    name = robot.get("menagerie")
    if not name:
        return ""
    directory = menagerie.default_dir()
    model = menagerie.get(str(name), directory)
    if model is None:
        return ""
    return str(menagerie.resolve_model_path(model, directory))


def resolve_scenario(
    config: dict[str, Any], seed: int, index: int | None = None
) -> dict[str, Any]:
    """Rebuild one scenario's params from its seed — the reproduction path."""
    axes = axes_of(config)
    matrix = config.get("scenarios") or {}
    base_seed = int(matrix.get("seed", 0))
    if index is None:
        index = scenarios_mod.find_index(base_seed, int(seed))
    if index is None:
        # A bare seed with no matching index still runs: nominal params, so the
        # command never silently substitutes a different scenario.
        return {
            "id": f"seed{int(seed)}",
            "index": None,
            "seed": int(seed),
            "label": "unindexed",
            "params": scenarios_mod.replay(int(seed), 0, axes)["params"],
        }
    replayed = scenarios_mod.replay(base_seed, int(index), axes)
    return {
        "id": f"s{int(index):03d}",
        "index": int(index),
        "seed": replayed["seed"],
        "label": replayed["label"],
        "params": replayed["params"],
    }


def parse_params(overrides: list[str]) -> dict[str, Any]:
    """Parse repeated ``--param key=value`` flags into scenario params."""
    parsed: dict[str, Any] = {}
    for item in overrides or []:
        key, _, raw = str(item).partition("=")
        if not key or not _:
            raise ValueError(f"--param needs KEY=VALUE, got {item!r}")
        try:
            parsed[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            parsed[key.strip()] = raw
    return parsed


# -- output ----------------------------------------------------------------- #


def _result_payload(result: Any, scenario: dict[str, Any]) -> dict[str, Any]:
    """The JSON shape agents parse — mirrors ``schemas/scenario.json``."""
    return {
        "id": result.scenario_id,
        "index": scenario.get("index"),
        "seed": result.seed,
        "label": scenario.get("label"),
        "params": scenario.get("params"),
        "status": result.status,
        "sim_time_s": round(result.sim_time_s, 4),
        "duration_s": result.duration_s,
        "criteria": result.criteria,
        "diagnosis": result.diagnosis,
        "video_path": result.video_path,
        "error": result.error,
    }


def _exit_code(status: str) -> int:
    """Failure exits 1 so this composes as a CI step; our own breakage exits 2."""
    if status == "passed":
        return 0
    return EXIT_ERROR if status == "error" else EXIT_SCENARIO_FAILED


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _print_result(result: Any, scenario: dict[str, Any]) -> None:
    print(f"{result.scenario_id}  seed={result.seed}  {scenario.get('label', '')}")
    print(f"  status      {result.status.upper()}")
    print(f"  sim time    {result.sim_time_s:.2f}s  (wall {result.duration_s:.2f}s)")
    for criterion in result.criteria:
        mark = "pass" if criterion.get("passed") else "FAIL"
        print(
            f"  [{mark}] {criterion.get('id')}: "
            f"measured={criterion.get('measured')} "
            f"threshold={criterion.get('threshold')}"
        )
    if result.diagnosis:
        print(f"  diagnosis   {result.diagnosis}")
    if result.video_path:
        print(f"  video       {result.video_path}")
    if result.error:
        print(f"  error       {result.error}")


def _progress_line(event: dict[str, Any]) -> None:
    index = event.get("index", 0)
    total = event.get("total", 0)
    status = str(event.get("status", "")).upper()
    print(
        f"[{index + 1:>3}/{total}] {event.get('id')} seed={event.get('seed')} {status}",
        flush=True,
    )


def _print_table(
    results: list[Any], scenarios: list[dict[str, Any]], stats: dict[str, Any]
) -> None:
    print()
    print(f"{'seed':>12}  {'status':<8} {'sim':>6}  scenario")
    for result, scenario in zip(results, scenarios, strict=False):
        print(
            f"{result.seed:>12}  {result.status:<8} {result.sim_time_s:>5.1f}s  "
            f"{scenario.get('label', '')}"
        )
        if result.diagnosis:
            print(f"{'':>12}  -> {result.diagnosis}")
        if result.error:
            print(f"{'':>12}  !! {result.error}")
    print()
    print(
        f"{stats['passed']}/{stats['total']} passed "
        f"({stats['pass_rate'] * 100:.0f}% of {stats['total'] - stats['errored']} "
        f"scored), {stats['failed']} failed, {stats['errored']} errored"
    )


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for the ``simkit`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"simkit: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
