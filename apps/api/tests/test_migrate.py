import sqlite3
from pathlib import Path

from app import config
from app.store import db as store_db
from app.store.migrate import repair_schema


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_init_db_repairs_missing_nullable_column_and_preserves_data(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "drift.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                repo TEXT NOT NULL,
                branch TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                commit_message TEXT NOT NULL,
                pushed_by TEXT NOT NULL,
                robot_model_json TEXT,
                suite_json TEXT,
                pull_request_url TEXT,
                report_id TEXT,
                error TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO runs (
                id, stage, repo, branch, commit_sha, commit_message, pushed_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run_old",
                "RUN_SUITE",
                "acme/robot",
                "main",
                "a" * 40,
                "old run",
                "ada",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    store_db.reset_engine()
    store_db.init_db()

    assert "finished_at" in _columns(path, "runs")
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT id, commit_message, finished_at FROM runs"
        ).fetchone()
    assert row == ("run_old", "old run", None)


def test_repair_reports_not_null_column_without_default(tmp_path: Path) -> None:
    path = tmp_path / "drift.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY)")
        drift = repair_schema(conn)

    assert "runs.stage (NOT NULL, no default)" in drift.needs_rebuild
    assert "stage" not in _columns(path, "runs")
