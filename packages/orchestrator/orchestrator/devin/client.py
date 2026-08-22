"""HTTP client for the Devin API: create, poll and message sessions.

Responsibility
--------------
Own every outbound call to ``DEVIN_API_BASE``. Retries, rate limiting, timeouts
and auth live here so no role module ever touches HTTP.

Inputs:  a prompt string, optional snapshot/playbook ids, ``DEVIN_API_KEY``.
Outputs: session ids and urls, status polls, transcript lines, structured
         output parsed back out of the session.

Concurrency
-----------
This is the throttle point for the whole system. ``MAX_PARALLEL_AGENTS`` is
enforced with a semaphore held here, not in the pipeline, so every path that
creates a session is bounded — including retries and the Reviewer's ad-hoc
follow-ups.

Failure policy
--------------
A session that errors is a *system* failure, never a robot failure. Callers
must be able to tell the difference, so transport errors raise
:class:`DevinError` rather than returning a failed-looking result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class DevinError(RuntimeError):
    """A Devin API call failed. Distinct from the robot code failing a test."""


@dataclass
class SessionHandle:
    """Identifiers for one live Devin session."""

    session_id: str
    url: str
    status: str


class DevinClient:
    """Async client for the Devin session API."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.devin.ai/v1",
        max_parallel: int = 6,
        timeout_s: float = 60.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.max_parallel = max_parallel
        # TODO(build): httpx.AsyncClient with Bearer auth, asyncio.Semaphore.

    # -- lifecycle --------------------------------------------------------- #

    async def create_session(
        self,
        prompt: str,
        *,
        title: str | None = None,
        snapshot_id: str | None = None,
        playbook_id: str | None = None,
        idempotent: bool = True,
        tags: list[str] | None = None,
    ) -> SessionHandle:
        """POST /sessions — start an agent on a task.

        ``tags`` should carry ``run_id`` and ``role`` so sessions are findable
        in the Devin UI when a judge asks "show me the real thing".
        """
        raise NotImplementedError
        # TODO(build): POST {api_base}/sessions under the semaphore; retry on
        # 429/5xx with tenacity; return SessionHandle.

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """GET /session/{id} — full state including status and messages."""
        raise NotImplementedError
        # TODO(build): GET, raise DevinError on non-2xx.

    async def send_message(self, session_id: str, message: str) -> None:
        """POST /session/{id}/message — the relay channel.

        Every agent-to-agent exchange in this system is ultimately this call:
        the orchestrator reads one session's finding and speaks it into
        another. See ``docs/AGENT_ROLES.md``.
        """
        raise NotImplementedError
        # TODO(build): POST message body.

    async def wait_until_done(
        self,
        session_id: str,
        *,
        poll_interval_s: float = 5.0,
        timeout_s: float = 1800.0,
        on_activity: Any = None,
    ) -> dict[str, Any]:
        """Poll until the session finishes, blocks, or times out.

        ``on_activity`` is called with each new transcript line so the
        dashboard's per-agent ticker updates live rather than jumping from
        "working" to "done".
        """
        raise NotImplementedError
        # TODO(build): poll loop, diff transcript, invoke on_activity, honour
        # timeout by raising DevinError.

    # -- structured output ------------------------------------------------- #

    async def structured_output(self, session_id: str) -> dict[str, Any]:
        """Read the session's structured output block.

        Every role prompt ends by instructing the agent to emit a JSON block
        matching a contract schema. This parses it. A session whose output does
        not parse is retried once with an explicit reminder, then failed —
        free-text results are not accepted, because the pipeline cannot verify
        prose.
        """
        raise NotImplementedError
        # TODO(build): pull the structured output field; fall back to scraping
        # a fenced ```json block from the last message; validate, retry once.

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        raise NotImplementedError
        # TODO(build): await self._http.aclose().
