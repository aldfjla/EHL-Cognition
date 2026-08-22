"""Persistent memory across runs — what the team learned last time.

Responsibility
--------------
Devin's knowledge store lets facts survive a session. This module decides what
is worth keeping and injects it into future prompts.

Inputs:  confirmed findings from a finished run.
Outputs: knowledge entries scoped to a repo, and a prompt preamble for new runs.

What is worth remembering
-------------------------
Only things that are true *across* runs and expensive to rediscover:

* Which Menagerie model matched this repo's robot, and the confidence.
* The harness adapter shape that worked — rediscovering how to bind a
  controller to MuJoCo costs an agent several minutes every single run.
* Confirmed ``constraint`` findings ("this arm's joint 4 has a hard stop the
  URDF does not encode").
* Known-flaky scenario seeds.

Explicitly NOT remembered: root causes of fixed bugs. Once patched they are
noise, and stale causes actively mislead the next Investigator.
"""

from __future__ import annotations

from orchestrator.schemas import Finding, Run


async def recall(repo: str) -> list[str]:
    """Knowledge entries relevant to ``repo``, newest first."""
    raise NotImplementedError
    # TODO(build): GET the Devin knowledge API filtered by repo tag.


async def remember(repo: str, finding: Finding) -> str:
    """Persist one finding as durable knowledge. Returns the entry id."""
    raise NotImplementedError
    # TODO(build): POST to the knowledge API with repo + kind tags.


async def harvest(run: Run, findings: list[Finding]) -> list[str]:
    """Pick the keepers from a finished run and persist them.

    Applies the policy in this module's docstring — filters aggressively.
    """
    raise NotImplementedError
    # TODO(build): filter to model/harness/constraint findings with
    # status == CONFIRMED, dedupe against recall(), remember() each.


def render_preamble(entries: list[str]) -> str:
    """Format recalled knowledge as a markdown block for a role prompt."""
    raise NotImplementedError
    # TODO(build): bulleted list under a "What we know about this repo" heading.
