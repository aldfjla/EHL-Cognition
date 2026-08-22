"""Devin settings and client flavour wiring."""

from __future__ import annotations

import asyncio

import pytest

from app import config, deps


def test_settings_reads_org_id_and_defaults_to_v3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An org id selects the documented v3 API default."""
    monkeypatch.setenv("DEVIN_API_KEY", "dummy-key")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-test")
    monkeypatch.delenv("DEVIN_API_BASE", raising=False)

    settings = config.Settings(_env_file=None)

    assert settings.devin_org_id == "org-test"
    assert settings.devin_api_base == "https://api.devin.ai/v3"


def test_live_settings_defaults_and_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = config.Settings(_env_file=None)
    assert defaults.sim_workers == 4
    assert defaults.max_live_streams == 12
    assert defaults.live_stream_fps == 6.0
    assert defaults.live_stream_idle_timeout_s == 30.0

    monkeypatch.setenv("SIM_WORKERS", "9")
    monkeypatch.setenv("MAX_LIVE_STREAMS", "3")
    monkeypatch.setenv("LIVE_STREAM_FPS", "2.5")
    monkeypatch.setenv("LIVE_STREAM_IDLE_TIMEOUT_S", "4.25")
    overridden = config.Settings(_env_file=None)
    assert overridden.sim_workers == 9
    assert overridden.max_live_streams == 3
    assert overridden.live_stream_fps == 2.5
    assert overridden.live_stream_idle_timeout_s == 4.25


@pytest.mark.parametrize(
    ("org_id", "api_base", "expected_org_id"),
    [
        ("org-test", "https://api.devin.ai/v3", "org-test"),
        (None, "https://api.devin.ai/v1", None),
    ],
)
def test_client_from_settings_selects_api_flavour(
    monkeypatch: pytest.MonkeyPatch,
    org_id: str | None,
    api_base: str,
    expected_org_id: str | None,
) -> None:
    """The API dependency passes org mode through to DevinClient."""
    monkeypatch.setenv("DEVIN_API_KEY", "dummy-key")
    monkeypatch.setenv("DEVIN_API_BASE", api_base)
    if org_id is None:
        monkeypatch.delenv("DEVIN_ORG_ID", raising=False)
    else:
        monkeypatch.setenv("DEVIN_ORG_ID", org_id)

    settings = config.Settings(_env_file=None)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    deps._devin = None
    client = deps.get_devin()
    try:
        assert client.org_id == expected_org_id
        assert client.api_base == api_base
    finally:
        asyncio.run(client.aclose())
        deps._devin = None
