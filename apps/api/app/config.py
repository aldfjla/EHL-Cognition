"""Typed settings loaded from ``.env`` via pydantic-settings.

Responsibility
--------------
One authoritative reading of the environment. Every key documented in
``.env.example`` appears here with the same name and a sane default, so a
missing variable produces a clear startup error rather than a mystery at
stage FIX.

Inputs:  process environment and ``.env`` at the repo root.
Outputs: a cached :class:`Settings` singleton via :func:`get_settings`.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("robotci.config")

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All runtime configuration. Mirrors ``.env.example`` key for key."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Devin ------------------------------------------------------------- #
    devin_api_key: str = ""
    devin_org_id: str = ""
    devin_api_base: str = "https://api.devin.ai/v3"
    #: Empty means "send no model field", so the org default applies.
    devin_model: str = ""

    # -- GitHub ------------------------------------------------------------ #
    github_token: str = ""
    target_repo: str = ""
    target_branch: str = "main"
    webhook_secret: str = ""

    # -- Storage ----------------------------------------------------------- #
    database_url: str = "sqlite:///./robotci.db"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    menagerie_dir: Path = REPO_ROOT / "vendor" / "menagerie"

    # -- Server ------------------------------------------------------------ #
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_origin: str = "http://localhost:8000"
    ui_origin: str = "http://localhost:3000"

    # -- Pipeline tuning --------------------------------------------------- #
    suite_size: int = 50
    max_parallel_agents: int = 6
    max_agent_iterations: int = 3
    sim_workers: int = 4
    scenario_timeout_s: float = 60.0
    max_live_streams: int = 12
    live_stream_fps: float = 6.0
    live_stream_idle_timeout_s: float = 30.0

    def require_devin(self) -> str:
        """Return the Devin key or raise a startup-time error naming the fix."""
        if not self.devin_api_key.strip():
            raise RuntimeError("DEVIN_API_KEY unset; copy .env.example to .env")
        return self.devin_api_key


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Use this, never ``Settings()`` directly."""
    return Settings()


def validate_paths(settings: Settings | None = None) -> list[str]:
    """Check the filesystem assumptions and return the warnings raised.

    Called from the app lifespan. Never raises: a missing model library is a
    reason to warn loudly at startup, not a reason to refuse to boot — the
    dashboard and its browser replay work without it.
    """
    settings = settings or get_settings()
    warnings: list[str] = []

    try:
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.artifacts_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        warnings.append(
            f"ARTIFACTS_DIR {settings.artifacts_dir} is not writable: {exc}"
        )

    if not settings.menagerie_dir.is_dir():
        warnings.append(
            f"MENAGERIE_DIR {settings.menagerie_dir} is missing; run `make menagerie`"
        )

    for warning in warnings:
        log.warning(warning)
    return warnings
