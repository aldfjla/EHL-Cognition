"""Devin client behaviour against a fake httpx transport."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from orchestrator.devin.client import (
    STRUCTURED_OUTPUT_REMINDER,
    DevinClient,
    DevinError,
    extract_structured_output,
    transcript_lines,
)


def make_client(handler: Any, **kwargs: Any) -> DevinClient:
    """A client whose HTTP layer is a MockTransport running ``handler``."""
    client = DevinClient("key", api_base="https://api.test/v1", **kwargs)
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer key"},
    )
    return client


def test_missing_key_is_a_devin_error() -> None:
    with pytest.raises(DevinError):
        DevinClient("")


async def test_create_session_sends_prompt_and_tags() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, "body": request.read().decode()})
        return httpx.Response(
            200, json={"session_id": "s-1", "url": "https://app.devin.ai/sessions/s-1"}
        )

    client = make_client(handler)
    handle = await client.create_session(
        "do the thing", title="Investigator", tags=["run-1", "investigator"]
    )
    await client.aclose()

    assert handle.session_id == "s-1"
    assert handle.url.endswith("/s-1")
    assert seen[0]["path"] == "/v1/sessions"
    assert "do the thing" in seen[0]["body"]
    assert "investigator" in seen[0]["body"]


async def test_create_session_without_id_fails_and_frees_the_slot() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"url": "x"}))
    with pytest.raises(DevinError):
        await client.create_session("p")
    assert client._sem._value == client.max_parallel
    await client.aclose()


async def test_retries_5xx_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, text="upstream down")
        return httpx.Response(200, json={"session_id": "s-2", "url": "u"})

    client = make_client(handler)
    handle = await client.create_session("p")
    await client.aclose()

    assert attempts["n"] == 3
    assert handle.session_id == "s-2"


async def test_transport_error_becomes_devin_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = make_client(handler)
    with pytest.raises(DevinError):
        await client.get_session("s-1")
    await client.aclose()


async def test_client_error_is_not_retried() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(403, text='{"detail":"Unauthorized"}')

    client = make_client(handler)
    with pytest.raises(DevinError, match="403"):
        await client.ping()
    await client.aclose()
    assert attempts["n"] == 1


async def test_semaphore_bounds_live_sessions() -> None:
    live = {"now": 0, "max": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
            return httpx.Response(
                200, json={"session_id": f"s{live['now']}", "url": ""}
            )
        live["now"] -= 1
        return httpx.Response(200, json={"status": "finished", "messages": []})

    client = make_client(handler, max_parallel=2)

    async def one() -> None:
        handle = await client.create_session("p")
        await asyncio.sleep(0)
        await client.wait_until_done(handle.session_id, poll_interval_s=0)

    await asyncio.gather(*(one() for _ in range(6)))
    await client.aclose()

    assert live["max"] <= 2


async def test_wait_until_done_streams_only_new_lines() -> None:
    polls = {"n": 0}
    scripts = [
        {"status": "working", "messages": [{"message": "first"}]},
        {
            "status": "working",
            "messages": [{"message": "first"}, {"message": "second"}],
        },
        {
            "status": "finished",
            "messages": [{"message": "first"}, {"message": "second"}],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = scripts[min(polls["n"], len(scripts) - 1)]
        polls["n"] += 1
        return httpx.Response(200, json=payload)

    seen: list[str] = []
    client = make_client(handler)
    payload = await client.wait_until_done(
        "s-1", poll_interval_s=0, on_activity=seen.append
    )
    await client.aclose()

    assert seen == ["first", "second"]
    assert payload["status"] == "finished"


async def test_wait_until_done_times_out() -> None:
    client = make_client(
        lambda request: httpx.Response(200, json={"status": "working", "messages": []})
    )
    with pytest.raises(DevinError, match="did not finish"):
        await client.wait_until_done("s-1", poll_interval_s=0, timeout_s=0.0)
    await client.aclose()


async def test_structured_output_reads_the_api_field() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200, json={"status": "finished", "structured_output": {"verdict": "ship"}}
        )
    )
    assert await client.structured_output("s-1") == {"verdict": "ship"}
    await client.aclose()


async def test_structured_output_scrapes_a_fenced_block() -> None:
    payload = {
        "status": "finished",
        "messages": [{"message": 'done:\n```json\n{"root_cause": "timer"}\n```'}],
    }
    client = make_client(lambda request: httpx.Response(200, json=payload))
    assert await client.structured_output("s-1") == {"root_cause": "timer"}
    await client.aclose()


async def test_structured_output_reminds_once_then_accepts() -> None:
    state = {"reminded": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert STRUCTURED_OUTPUT_REMINDER in request.read().decode()
            state["reminded"] = True
            return httpx.Response(200, json={})
        if not state["reminded"]:
            return httpx.Response(
                200, json={"status": "finished", "messages": [{"message": "all good!"}]}
            )
        return httpx.Response(
            200,
            json={
                "status": "finished",
                "messages": [{"message": '```json\n{"ok": true}\n```'}],
            },
        )

    client = make_client(handler)
    assert await client.structured_output("s-1") == {"ok": True}
    await client.aclose()
    assert state["reminded"]


async def test_structured_output_gives_up_on_prose() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(
            200, json={"status": "finished", "messages": [{"message": "I think so"}]}
        )

    client = make_client(handler)
    with pytest.raises(DevinError, match="no parseable structured output"):
        await client.structured_output("s-1")
    await client.aclose()


def test_transcript_lines_handles_mixed_shapes() -> None:
    payload = {"messages": ["a", {"message": "b"}, {"text": "c"}, {"message": "  "}]}
    assert transcript_lines(payload) == ["a", "b", "c"]


def test_extract_structured_output_prefers_the_last_block() -> None:
    payload = {
        "messages": [
            {"message": '```json\n{"n": 1}\n```'},
            {"message": '```json\n{"n": 2}\n```'},
        ]
    }
    assert extract_structured_output(payload) == {"n": 2}


def test_extract_structured_output_parses_a_json_string_field() -> None:
    assert extract_structured_output({"structured_output": '{"a": 1}'}) == {"a": 1}
    assert extract_structured_output({"structured_output": ""}) is None
