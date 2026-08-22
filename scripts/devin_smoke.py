#!/usr/bin/env python3
"""Prove the Devin API works: auth, one session, one message, one response.

Run this in the first hour of the build, before writing anything that depends
on the API. Every other integration assumption rests on this script passing.

    python scripts/devin_smoke.py
    python scripts/devin_smoke.py --prompt "Print the python version" --wait

Checks, in order — each failure mode gets its own message, because "it didn't
work" costs an hour and "your key is unset" costs ten seconds:

1. ``DEVIN_API_KEY`` is present.
2. The API base is reachable and the key authenticates.
3. A session can be created, and its url is printable.
4. The session accepts a relayed message (the mechanism every agent handoff
   in this system depends on).
5. Structured output can be read back and parsed as JSON.

Exit code 0 on success, 1 on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "orchestrator"))

# Imported after the sys.path bootstrap above so the script runs uninstalled.
from orchestrator.devin.client import (
    DevinClient,
    DevinError,
)

DEFAULT_PROMPT = (
    "This is a connectivity smoke test for an automated system. "
    "Reply with a fenced json block containing "
    '{"ok": true, "note": "<one short sentence>"} and nothing else.'
)

DEFAULT_API_BASE = "https://api.devin.ai/v1"
DEFAULT_V3_API_BASE = "https://api.devin.ai/v3"


def parse_args() -> argparse.Namespace:
    """CLI: --prompt, --wait, --timeout, --json."""
    parser = argparse.ArgumentParser(
        description="Smoke-test the Devin API used by the orchestrator.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt for the throwaway session.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll the session to completion and parse its structured output. "
        "Off by default so the connectivity check stays fast.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Seconds to poll for with --wait (default: 600).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print one machine-readable JSON object instead of a check list.",
    )
    return parser.parse_args()


def _load_dotenv(path: Path) -> None:
    """Populate missing env vars from ``.env``. The process env still wins."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def check_env() -> tuple[str, str, str | None]:
    """Return ``(api_key, api_base, org_id)`` or exit with an actionable message."""
    _load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("DEVIN_API_KEY", "").strip()
    org_id = os.environ.get("DEVIN_ORG_ID", "").strip() or None
    default_base = DEFAULT_V3_API_BASE if org_id else DEFAULT_API_BASE
    api_base = os.environ.get("DEVIN_API_BASE", "").strip() or default_base
    if not api_key:
        print("DEVIN_API_KEY unset — copy .env.example to .env")
        raise SystemExit(1)
    return api_key, api_base, org_id


async def _run(args: argparse.Namespace, api_key: str, result: dict) -> None:
    """Run the API checks, recording each one in ``result`` as it passes.

    ``result`` is the caller's dict rather than a return value: a failure
    halfway through must still leave the earlier findings — above all the
    session url — printable.
    """
    client = DevinClient(
        api_key,
        api_base=result["api_base"],
        max_parallel=1,
        org_id=result["org_id"],
    )
    try:
        # 2. Auth: a bad key fails here, before a session is spent on it.
        await client.ping()
        result["auth"] = True

        # 3. Create.
        handle = await client.create_session(
            args.prompt,
            title="robotci devin_smoke",
            tags=["robotci", "smoke"],
        )
        result["session_id"] = handle.session_id
        result["session_url"] = handle.url

        # 4. Relay: this call is what every agent handoff reduces to.
        await client.send_message(handle.session_id, "Smoke test: relay check.")
        result["message_sent"] = True

        # 5. Structured output.
        if args.wait:
            await client.wait_until_done(
                handle.session_id,
                timeout_s=args.timeout,
                on_activity=None if args.as_json else lambda line: print(f"  | {line}"),
            )
            result["structured_output"] = await client.structured_output(
                handle.session_id
            )
        result["ok"] = True
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Run the five checks, printing a pass/fail line for each."""
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    args = parse_args()
    api_key, api_base, org_id = check_env()

    result: dict = {
        "ok": False,
        "api_base": api_base,
        "org_id": org_id,
        "flavor": "v3" if org_id else "v1",
    }
    error: str | None = None
    try:
        asyncio.run(_run(args, api_key, result))
    except DevinError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 — the script reports, never raises
        error = f"{type(exc).__name__}: {exc}"
    if error:
        result["error"] = error

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"1. DEVIN_API_KEY present            {_mark(True)}")
        print(f"   API flavor: {result['flavor']}")
        print(f"2. {api_base} authenticates  {_mark(result.get('auth'))}")
        print(f"3. session created                  {_mark(result.get('session_id'))}")
        # Printed even when a later check failed: the session page is the
        # fastest way to see what actually happened.
        if result.get("session_url"):
            print(f"   {result['session_url']}")
        print(
            f"4. message accepted                 {_mark(result.get('message_sent'))}"
        )
        if args.wait:
            output = result.get("structured_output")
            print(f"5. structured output parsed          {_mark(output)}")
            if output:
                print(json.dumps(output, indent=2)[:2000])
        else:
            print("5. structured output                 skipped (pass --wait)")
        if error:
            print(f"\nFAILED: {error}")

    return 0 if result.get("ok") else 1


def _mark(value: object) -> str:
    """``ok``/``FAIL`` marker for the check list."""
    return "ok" if value else "FAIL"


if __name__ == "__main__":
    sys.exit(main())
