"""The smoke script's argument parsing, env check and exit codes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "devin_smoke.py"


def load_script() -> Any:
    """Import ``scripts/devin_smoke.py`` as a module."""
    spec = importlib.util.spec_from_file_location("devin_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["devin_smoke"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke(monkeypatch: pytest.MonkeyPatch) -> Any:
    module = load_script()
    # Never read the developer's real .env during a test run.
    monkeypatch.setattr(module, "_load_dotenv", lambda path: None)
    return module


def test_parse_args_defaults(smoke: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["devin_smoke.py"])
    args = smoke.parse_args()
    assert args.prompt == smoke.DEFAULT_PROMPT
    assert args.wait is False
    assert args.as_json is False
    assert args.timeout > 0


def test_parse_args_flags(smoke: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["devin_smoke.py", "--prompt", "hi", "--wait", "--timeout", "12", "--json"],
    )
    args = smoke.parse_args()
    assert (args.prompt, args.wait, args.timeout, args.as_json) == (
        "hi",
        True,
        12.0,
        True,
    )


def test_missing_key_prints_the_fix(
    smoke: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DEVIN_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        smoke.check_env()
    assert exc.value.code == 1
    assert "DEVIN_API_KEY unset — copy .env.example to .env" in capsys.readouterr().out


def test_check_env_returns_key_and_base(
    smoke: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "k")
    monkeypatch.delenv("DEVIN_ORG_ID", raising=False)
    monkeypatch.delenv("DEVIN_API_BASE", raising=False)
    assert smoke.check_env() == ("k", smoke.DEFAULT_API_BASE, None)
    monkeypatch.setenv("DEVIN_API_BASE", "https://api.test/v1")
    assert smoke.check_env()[1:] == ("https://api.test/v1", None)


def test_check_env_defaults_v3_base_for_org(
    smoke: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "k")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-test")
    monkeypatch.delenv("DEVIN_API_BASE", raising=False)
    assert smoke.check_env() == ("k", smoke.DEFAULT_V3_API_BASE, "org-test")


def _patch_transport(smoke: Any, monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Give the script's client a MockTransport instead of the network."""
    real = smoke.DevinClient

    def factory(*args: Any, **kwargs: Any) -> Any:
        client = real(*args, **kwargs)
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return client

    monkeypatch.setattr(smoke, "DevinClient", factory)


def test_main_reports_success(
    smoke: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "k")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions") and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "session_id": "s-1",
                    "url": "https://app.devin.ai/sessions/s-1",
                },
            )
        return httpx.Response(200, json={"sessions": []})

    _patch_transport(smoke, monkeypatch, handler)
    assert smoke.main([]) == 0
    out = capsys.readouterr().out
    assert "https://app.devin.ai/sessions/s-1" in out
    assert "FAIL" not in out


def test_main_prints_the_session_url_even_when_a_later_step_fails(
    smoke: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "k")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/message"):
            return httpx.Response(400, text="nope")
        if request.method == "POST":
            return httpx.Response(
                200, json={"session_id": "s-9", "url": "https://app.devin.ai/s/s-9"}
            )
        return httpx.Response(200, json={})

    _patch_transport(smoke, monkeypatch, handler)
    assert smoke.main([]) == 1
    out = capsys.readouterr().out
    assert "https://app.devin.ai/s/s-9" in out
    assert "FAILED:" in out


def test_main_fails_on_bad_auth(
    smoke: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "bad")
    _patch_transport(
        smoke,
        monkeypatch,
        lambda request: httpx.Response(403, text='{"detail":"Unauthorized"}'),
    )
    assert smoke.main([]) == 1
    assert "403" in capsys.readouterr().out
