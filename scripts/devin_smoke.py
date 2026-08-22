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
import sys

DEFAULT_PROMPT = (
    "This is a connectivity smoke test for an automated system. "
    "Reply with a fenced json block containing "
    '{"ok": true, "note": "<one short sentence>"} and nothing else.'
)


def parse_args() -> argparse.Namespace:
    """CLI: --prompt, --wait, --timeout, --json."""
    raise NotImplementedError
    # TODO(build): argparse with the flags above; --wait polls to completion,
    # default is create-and-report-url so the check stays fast.


def check_env() -> tuple[str, str]:
    """Return ``(api_key, api_base)`` or exit with an actionable message."""
    raise NotImplementedError
    # TODO(build): read settings; if the key is blank, print
    # "DEVIN_API_KEY unset — copy .env.example to .env" and exit 1.


def main(argv: list[str] | None = None) -> int:
    """Run the five checks, printing a pass/fail line for each."""
    raise NotImplementedError
    # TODO(build): construct DevinClient, create_session, print session url,
    # send_message, optionally wait_until_done + structured_output, and print
    # a summary. Always print the session url even on later failure — it is
    # the fastest way to see what actually happened.


if __name__ == "__main__":
    sys.exit(main())
