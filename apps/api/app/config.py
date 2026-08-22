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

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

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
    devin_api_base: str = "https://api.devin.ai/v1"

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
    ui_origin: str = "http://localhost:3000"

    # -- Pipeline tuning --------------------------------------------------- #
    suite_size: int = 24
    max_parallel_agents: int = 6
    max_agent_iterations: int = 3

    def require_devin(self) -> str:
        """Return the Devin key or raise a startup-time error naming the fix."""
        raise NotImplementedError
        # TODO(build): raise RuntimeError("DEVIN_API_KEY unset; copy
        # .env.example to .env") when blank.


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Use this, never ``Settings()`` directly."""
    return Settings()


# TODO(build): validate at startup that artifacts_dir is writable and
# menagerie_dir exists, and log a warning pointing at `make menagerie` if not.
