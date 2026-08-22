"""Which pushes deserve a run.

These tests are the specification of the filter semantics: the webhook and
stage TRIGGERED both delegate here, so a behaviour change shows up once.
"""

from __future__ import annotations

from typing import Any

from orchestrator import triggers
from orchestrator.triggers import Code, Filters


def test_parse_returns_none_without_a_ci_section() -> None:
    """No opinion is not the same as an empty opinion."""
    assert triggers.parse({}) is None
    assert triggers.parse(None) is None
    assert triggers.parse({"control": {"entrypoint": "src/c.py:run"}}) is None
    assert triggers.parse({"ci": "main"}) is None


def test_parse_reads_branches_and_paths() -> None:
    filters = triggers.parse(
        {
            "ci": {
                "branches": ["main", "release/*"],
                "paths": {"include": ["src/*"], "exclude": ["src/vendor/*"]},
            }
        }
    )

    assert filters == Filters(
        branches=("main", "release/*"),
        path_include=("src/*",),
        path_exclude=("src/vendor/*",),
    )


def test_parse_accepts_a_bare_branch_and_a_bare_path_list() -> None:
    filters = triggers.parse({"ci": {"branch": "dev", "paths": ["ctrl/*"]}})

    assert filters is not None
    assert filters.branches == ("dev",)
    assert filters.path_include == ("ctrl/*",)
    assert filters.path_exclude == triggers.DEFAULT_PATH_EXCLUDE


def test_parse_treats_an_empty_exclude_list_as_exclude_nothing() -> None:
    filters = triggers.parse({"ci": {"paths": {"exclude": []}}})

    assert filters is not None
    assert filters.path_exclude == ()
    # ``docs/*`` is excluded by default; with the exclude list emptied the file
    # is judged on the include set alone.
    assert triggers.select_paths(["docs/tuning.yaml"], filters) == ("docs/tuning.yaml",)


def test_defaults_ignore_docs_and_keep_control_code() -> None:
    filters = Filters()

    assert triggers.select_paths(["README.md", "docs/deep/notes.md"], filters) == ()
    assert triggers.select_paths(["src/controller.py"], filters) == (
        "src/controller.py",
    )
    assert triggers.select_paths(["robotci.yaml"], filters) == ("robotci.yaml",)


def test_branch_patterns_are_globs() -> None:
    filters = Filters(branches=("main", "release/*"))

    assert triggers.branch_matches("release/2.1", filters.branches)
    assert not triggers.branch_matches("spike", filters.branches)


def test_from_registry_unions_branch_and_extra_patterns() -> None:
    filters = triggers.from_registry(branch="main", branches=["release/*", "main"])

    assert filters.branches == ("main", "release/*")
    assert filters.path_include == triggers.DEFAULT_PATH_INCLUDE


def test_from_registry_falls_back_to_defaults_when_unset() -> None:
    assert triggers.from_registry(branch="") == Filters()


def test_from_registry_honours_a_stored_empty_exclude_list() -> None:
    # ``None`` is unset and takes the defaults; ``[]`` is a configured answer
    # meaning "exclude nothing" and must not be read back as unset.
    filters = triggers.from_registry(
        branch="main", path_include=["docs/*.yaml"], path_exclude=[]
    )

    assert filters.path_exclude == ()
    assert triggers.select_paths(["docs/tuning.yaml"], filters) == ("docs/tuning.yaml",)


def test_dot_prefixed_paths_keep_their_dot() -> None:
    # ``lstrip("./")`` would turn this into ``github/workflows/ci.yml`` and the
    # default ``.github/*`` exclusion could never match.
    assert triggers.normalise(".github/workflows/ci.yml") == (
        ".github/workflows/ci.yml"
    )
    assert triggers.normalise("./.env") == ".env"
    assert triggers.normalise("/src/a.py") == "src/a.py"
    assert triggers.changed_paths(
        {"commits": [{"added": [".github/workflows/ci.yml"]}]}
    ) == (".github/workflows/ci.yml",)


def test_defaults_ignore_ci_plumbing_of_the_customer_repo() -> None:
    filters = Filters()

    assert triggers.select_paths([".github/workflows/ci.yml"], filters) == ()
    decision = triggers.evaluate(
        filters, branch="main", paths=[".github/workflows/ci.yml"]
    )
    assert not decision.start
    assert decision.code == Code.NO_MATCHING_PATHS


def test_dot_prefixed_patterns_match_dot_prefixed_paths() -> None:
    filters = triggers.parse(
        {"ci": {"paths": {"include": [".env", ".config/*"], "exclude": []}}}
    )
    assert filters is not None

    assert triggers.select_paths([".env"], filters) == (".env",)
    assert triggers.select_paths([".config/robot.toml"], filters) == (
        ".config/robot.toml",
    )
    assert triggers.select_paths(["src/a.py"], filters) == ()


def test_changed_paths_collects_every_commit() -> None:
    payload: dict[str, Any] = {
        "commits": [
            {"added": ["src/a.py"], "removed": [], "modified": ["README.md"]},
            {"added": [], "removed": ["src/b.py"], "modified": ["src/a.py"]},
        ]
    }

    assert triggers.changed_paths(payload) == ("src/a.py", "README.md", "src/b.py")


def test_changed_paths_is_none_when_the_payload_does_not_say() -> None:
    """Unknown must not collapse into "nothing changed" — that skips CI."""
    assert triggers.changed_paths({}) is None
    assert triggers.changed_paths({"commits": []}) is None
    assert triggers.changed_paths({"head_commit": {"id": "abc"}}) is None
    assert triggers.changed_paths({"commits": [{"id": "abc"}]}) is None


def test_changed_paths_reads_the_head_commit_when_commits_are_absent() -> None:
    payload = {"head_commit": {"id": "abc", "modified": ["src/a.py"]}}

    assert triggers.changed_paths(payload) == ("src/a.py",)


def test_evaluate_starts_on_a_matching_branch_and_path() -> None:
    decision = triggers.evaluate(Filters(), branch="main", paths=["src/a.py"])

    assert decision.start
    assert decision.code == Code.STARTED
    assert decision.matched_paths == ("src/a.py",)
    assert decision.as_dict()["matched_paths"] == ["src/a.py"]


def test_evaluate_ignores_an_unwatched_branch() -> None:
    decision = triggers.evaluate(Filters(), branch="spike", paths=["src/a.py"])

    assert not decision.start
    assert decision.code == Code.NOT_WATCHED_BRANCH
    assert "spike" in decision.reason


def test_evaluate_ignores_a_docs_only_push() -> None:
    decision = triggers.evaluate(Filters(), branch="main", paths=["README.md"])

    assert not decision.start
    assert decision.code == Code.NO_MATCHING_PATHS
    assert "README.md" in decision.reason


def test_evaluate_ignores_a_push_with_no_ref() -> None:
    decision = triggers.evaluate(Filters(), branch="", paths=["src/a.py"])

    assert not decision.start
    assert decision.code == Code.NOT_WATCHED_BRANCH


def test_evaluate_starts_when_changed_paths_are_unavailable() -> None:
    """A push we could not inspect still runs, and says why."""
    decision = triggers.evaluate(Filters(), branch="main", paths=None)

    assert decision.start
    assert decision.code == Code.PATHS_UNAVAILABLE
    assert decision.matched_paths == ()


def test_excludes_win_over_includes() -> None:
    filters = Filters(path_include=("src/*",), path_exclude=("src/generated/*",))

    assert triggers.select_paths(["src/generated/x.py", "src/y.py"], filters) == (
        "src/y.py",
    )


def test_leading_slashes_are_normalised() -> None:
    filters = Filters(path_include=("src/*",), path_exclude=())

    assert triggers.select_paths(["./src/a.py"], filters) == ("./src/a.py",)
    assert triggers.changed_paths({"commits": [{"added": ["./src/a.py"]}]}) == (
        "src/a.py",
    )
