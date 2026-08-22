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

import asyncio
import inspect
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

#: Attempts per request before a retryable failure becomes a DevinError.
MAX_ATTEMPTS = 4

#: Statuses reported by the API that mean the session will not progress further.
TERMINAL_STATUSES = frozenset(
    {
        "blocked",
        "finished",
        "expired",
        "stopped",
        "suspended",
        "completed",
        "failed",
        "cancelled",
        "exit",
        "error",
    }
)

#: Statuses that indicate the session may still make progress.
LIVE_STATUSES = frozenset(
    {"new", "claimed", "running", "resuming", "working", "starting"}
)

#: Status details that mean the session is done for orchestration purposes.
TERMINAL_STATUS_DETAILS = frozenset({"finished"})

#: An idle session will not progress by itself; stop waiting after this grace
#: period so a question that nobody answers cannot hold a slot forever.
SESSION_WAITING_ON_USER_GRACE_PERIOD_S = 120.0

#: Status details that mean the session is waiting for external input.
IDLE_STATUS_DETAILS = frozenset({"waiting_for_user", "waiting_for_approval"})

#: Sent once when a session finishes without a parseable structured output.
STRUCTURED_OUTPUT_REMINDER = (
    "Your result could not be parsed. Reply with nothing but a single fenced "
    "json block matching the schema in your instructions."
)

#: How long to keep polling after the reminder above before giving up.
REMINDER_TIMEOUT_S = 120.0

#: Maximum number of transcript pages fetched for one poll or output scrape.
MAX_TRANSCRIPT_PAGES = 100

#: Response marker returned while a newly created session is booting.
SESSION_INITIALIZING_MARKER = "session still initializing"

#: Warm-up retry bounds for sending the first message to a new session.
SESSION_WARMUP_INTERVAL_S = 5.0
SESSION_WARMUP_TIMEOUT_S = 180.0

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class DevinError(RuntimeError):
    """A Devin API call failed. Distinct from the robot code failing a test."""


class _Retryable(RuntimeError):
    """Internal: a transport error or 429/5xx that is worth trying again."""


class _SessionInitializing(DevinError):
    """Internal: the session has not finished booting yet."""


@dataclass
class SessionHandle:
    """Identifiers for one live Devin session."""

    session_id: str
    url: str
    status: str


def _scrape_json_block(text: str) -> dict[str, Any] | None:
    """Last fenced ``json`` object in ``text``, or ``None``."""
    if not text:
        return None
    for match in reversed(_JSON_BLOCK.findall(text)):
        try:
            parsed = json.loads(match)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_structured(value: Any) -> dict[str, Any] | None:
    """Normalise a structured-output field into a dict, or ``None``."""
    if isinstance(value, dict):
        return value or None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return _scrape_json_block(stripped)
        return parsed if isinstance(parsed, dict) else None
    return None


def transcript_lines(payload: dict[str, Any]) -> list[str]:
    """Flatten a session payload's messages into transcript lines."""
    lines: list[str] = []
    for entry in payload.get("messages") or []:
        if isinstance(entry, str):
            text = entry
        elif isinstance(entry, dict):
            text = str(entry.get("message") or entry.get("text") or "")
        else:
            text = str(entry)
        text = text.strip()
        if text:
            lines.append(text)
    return lines


def extract_structured_output(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Structured output from a session payload, scraping messages if needed."""
    for key in ("structured_output", "structured_outputs", "output"):
        parsed = _coerce_structured(payload.get(key))
        if parsed is not None:
            return parsed
    for line in reversed(transcript_lines(payload)):
        parsed = _scrape_json_block(line)
        if parsed is not None:
            return parsed
    return None


class DevinClient:
    """Async client for the Devin session API."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.devin.ai/v1",
        max_parallel: int = 6,
        timeout_s: float = 60.0,
        org_id: str | None = None,
    ) -> None:
        if not api_key:
            raise DevinError("DEVIN_API_KEY unset; copy .env.example to .env")
        self.api_base = api_base.rstrip("/")
        self.org_id = org_id if org_id is not None else os.environ.get("DEVIN_ORG_ID")
        self.max_parallel = max_parallel
        self.timeout_s = timeout_s
        self._http = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )
        self._sem = asyncio.Semaphore(max_parallel)
        #: Sessions currently holding a concurrency slot.
        self._slots: set[str] = set()

    # -- transport --------------------------------------------------------- #

    async def _attempt(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        """One HTTP attempt. Raises :class:`_Retryable` on 429/5xx."""
        url = f"{self.api_base}{path}"
        try:
            response = await self._http.request(method, url, json=body)
        except httpx.HTTPError as exc:  # transport, timeout, DNS
            raise _Retryable(f"{method} {path} failed: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise _Retryable(f"{method} {path} -> HTTP {response.status_code}")
        if response.status_code >= 400:
            if (
                response.status_code == 400
                and SESSION_INITIALIZING_MARKER in response.text.casefold()
            ):
                raise _SessionInitializing(
                    f"{method} {path} -> HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
            raise DevinError(
                f"{method} {path} -> HTTP {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise DevinError(f"{method} {path} returned non-JSON body") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a request, retrying 429/5xx and transport errors."""
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(MAX_ATTEMPTS),
                wait=wait_exponential(multiplier=1, max=20),
                retry=retry_if_exception_type(_Retryable),
                reraise=True,
            ):
                with attempt:
                    return await self._attempt(method, path, body)
        except _Retryable as exc:
            raise DevinError(str(exc)) from exc
        raise DevinError(f"{method} {path} produced no response")

    def _release(self, session_id: str) -> None:
        """Give back the concurrency slot held by ``session_id``, once."""
        if session_id in self._slots:
            self._slots.discard(session_id)
            self._sem.release()

    # -- paths ------------------------------------------------------------- #

    def _sessions_path(self) -> str:
        """Path for the sessions collection in the configured API flavour."""
        if self.org_id:
            return f"/organizations/{self.org_id}/sessions"
        return "/sessions"

    def _devin_id(self, session_id: str) -> str:
        """Return the v3 path identifier, including its required prefix."""
        if session_id.startswith("devin-"):
            return session_id
        return f"devin-{session_id}"

    def _session_path(self, session_id: str) -> str:
        """Path for one session in the configured API flavour."""
        if self.org_id:
            return f"{self._sessions_path()}/{self._devin_id(session_id)}"
        return f"/session/{session_id}"

    def _message_path(self, session_id: str) -> str:
        """Path for sending one message in the configured API flavour."""
        if self.org_id:
            return f"{self._session_path(session_id)}/messages"
        return f"/session/{session_id}/message"

    def _transcript_path(self, session_id: str) -> str:
        """Path for the v3 transcript endpoint."""
        return f"{self._session_path(session_id)}/messages"

    async def _fetch_transcript(self, session_id: str) -> list[dict[str, Any]]:
        """Fetch a bounded v3 transcript, or no messages for v1."""
        if not self.org_id:
            return []
        messages: list[dict[str, Any]] = []
        path = self._transcript_path(session_id)
        for _ in range(MAX_TRANSCRIPT_PAGES):
            page = await self._request("GET", path)
            items = page.get("items")
            if isinstance(items, list):
                messages.extend(item for item in items if isinstance(item, dict))
            if not page.get("has_next_page"):
                break
            cursor = str(page.get("end_cursor") or "")
            if not cursor:
                break
            path = f"{self._transcript_path(session_id)}?after={quote(cursor, safe='')}"
        return messages

    async def _merge_transcript(
        self, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Best-effort transcript merge that preserves the session response."""
        if not self.org_id:
            return payload
        try:
            messages = await self._fetch_transcript(session_id)
        except DevinError:
            return payload
        merged = dict(payload)
        merged["messages"] = messages
        return merged

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
        body: dict[str, Any] = {"prompt": prompt, "idempotent": idempotent}
        if title:
            body["title"] = title
        if snapshot_id:
            body["snapshot_id"] = snapshot_id
        if playbook_id:
            body["playbook_id"] = playbook_id
        if tags:
            body["tags"] = list(tags)

        # The slot is held for the life of the session, not just this request:
        # MAX_PARALLEL_AGENTS bounds how many agents are alive at once.
        await self._sem.acquire()
        try:
            payload = await self._request("POST", self._sessions_path(), body)
        except BaseException:
            self._sem.release()
            raise
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            self._sem.release()
            raise DevinError(f"POST /sessions returned no session_id: {payload}")
        self._slots.add(session_id)
        return SessionHandle(
            session_id=session_id,
            url=str(
                payload.get("url") or f"https://app.devin.ai/sessions/{session_id}"
            ),
            status=str(
                payload.get("status_enum") or payload.get("status") or "starting"
            ),
        )

    async def ping(self) -> dict[str, Any]:
        """Cheap authenticated call, so a bad key fails before a session does."""
        path = self._sessions_path()
        if self.org_id:
            path += "?limit=1"
        return await self._request("GET", path, None)

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """GET one session's state; v3 transcripts are fetched separately."""
        return await self._request("GET", self._session_path(session_id))

    async def send_message(self, session_id: str, message: str) -> None:
        """POST a message into a session — the relay channel.

        Every agent-to-agent exchange in this system is ultimately this call:
        the orchestrator reads one session's finding and speaks it into
        another. See ``docs/AGENT_ROLES.md``.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SESSION_WARMUP_TIMEOUT_S
        while True:
            try:
                await self._request(
                    "POST", self._message_path(session_id), {"message": message}
                )
                return
            except _SessionInitializing as exc:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise DevinError(str(exc)) from exc
                await asyncio.sleep(min(SESSION_WARMUP_INTERVAL_S, remaining))

    async def wait_until_done(
        self,
        session_id: str,
        *,
        poll_interval_s: float = 5.0,
        timeout_s: float = 1800.0,
        on_activity: Any = None,
        _release_slot: bool = True,
    ) -> dict[str, Any]:
        """Poll until the session finishes, blocks, or times out.

        ``on_activity`` is called with each new transcript line so the
        dashboard's per-agent ticker updates live rather than jumping from
        "working" to "done".
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        seen = 0
        idle_since: float | None = None
        while True:
            payload = await self.get_session(session_id)
            if on_activity is not None:
                payload = await self._merge_transcript(session_id, payload)
            lines = transcript_lines(payload)
            for line in lines[seen:]:
                if on_activity is not None:
                    result = on_activity(line)
                    if inspect.isawaitable(result):
                        await result
            seen = max(seen, len(lines))

            status = str(payload.get("status_enum") or payload.get("status") or "")
            status_detail = str(payload.get("status_detail") or "")
            status_lower = status.lower()
            status_detail_lower = status_detail.lower()
            if status_detail_lower in TERMINAL_STATUS_DETAILS:
                if _release_slot:
                    self._release(session_id)
                return payload
            if status_lower in LIVE_STATUSES:
                if status_detail_lower in IDLE_STATUS_DETAILS:
                    now = loop.time()
                    if idle_since is None:
                        idle_since = now
                    elif now - idle_since >= SESSION_WAITING_ON_USER_GRACE_PERIOD_S:
                        if _release_slot:
                            self._release(session_id)
                        return payload
                else:
                    idle_since = None
            elif status_lower in TERMINAL_STATUSES:
                if _release_slot:
                    self._release(session_id)
                return payload

            if loop.time() >= deadline:
                if _release_slot:
                    self._release(session_id)
                raise DevinError(
                    f"session {session_id} did not finish within {timeout_s:.0f}s "
                    f"(last status {status or 'unknown'})"
                )
            await asyncio.sleep(min(poll_interval_s, max(0.0, deadline - loop.time())))

    # -- structured output ------------------------------------------------- #

    async def structured_output(self, session_id: str) -> dict[str, Any]:
        """Read the session's structured output block.

        Every role prompt ends by instructing the agent to emit a JSON block
        matching a contract schema. This parses it. A session whose output does
        not parse is retried once with an explicit reminder, then failed —
        free-text results are not accepted, because the pipeline cannot verify
        prose.
        """
        payload = await self.get_session(session_id)
        parsed = extract_structured_output(payload)
        if parsed is not None:
            return parsed
        payload = await self._merge_transcript(session_id, payload)
        parsed = extract_structured_output(payload)
        if parsed is not None:
            return parsed

        await self.send_message(session_id, STRUCTURED_OUTPUT_REMINDER)
        try:
            payload = await self.wait_until_done(
                session_id,
                timeout_s=REMINDER_TIMEOUT_S,
                _release_slot=False,
            )
        except DevinError:
            payload = await self.get_session(session_id)
        payload = await self._merge_transcript(session_id, payload)
        parsed = extract_structured_output(payload)
        if parsed is None:
            raise DevinError(
                f"session {session_id} produced no parseable structured output"
            )
        return parsed

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        for session_id in list(self._slots):
            self._release(session_id)
        await self._http.aclose()

    #: Alias matching httpx's naming, for callers that expect ``aclose``.
    aclose = close
