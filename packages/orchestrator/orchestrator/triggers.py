"""Which pushes deserve a run — the deterministic half of the entrypoint.

Responsibility
--------------
Decide, from data alone, whether a push should start a pipeline: does the
pushed ref match the repository's watched branches, and does at least one
changed path match its path filters.

Inputs:  a parsed ``ci:`` section (from the customer's ``robotci.yaml`` or from
         the connected-repository registry), a branch name, and the changed
         paths of a push.
Outputs: a :class:`Decision` carrying a stable ``code`` and a human sentence.

Why this is a module and not a few ``if``\\ s in the router
---------------------------------------------------------
The same filters are evaluated in two places with two sources of truth: the
webhook (registry, because the repo is not cloned yet) and stage TRIGGERED
(the repo's own ``robotci.yaml``, which is only readable once there is a
checkout). Both must agree, so the rules live here, are pure, and are tested
directly. Nothing in this module imports the agent layer, the database or
FastAPI.

Glob semantics
--------------
Patterns are matched with :func:`fnmatch.fnmatchcase` after normalising away a
leading ``./`` and then a leading ``/`` — as *prefixes*, not character sets: a
dot-prefixed path such as ``.github/workflows/ci.yml`` must survive intact or
the default ``.github/*`` exclusion could never match. ``*`` crosses ``/`` —
``src/*`` matches
``src/deep/file.py``. This is deliberately more permissive than gitignore
semantics: a filter that accidentally matches too much costs a run, one that
accidentally matches too little silently skips CI on real code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

#: Config section read from ``robotci.yaml``.
CONFIG_SECTION = "ci"

#: Watched branches when nothing is configured anywhere.
DEFAULT_BRANCHES: tuple[str, ...] = ("main",)

#: Paths that are control code or configuration by default. Chosen so that a
#: repo which never writes a ``ci:`` block still gets sensible behaviour, and
#: so that a docs-only push does not burn a run.
DEFAULT_PATH_INCLUDE: tuple[str, ...] = (
    "*.py",
    "*.c",
    "*.cc",
    "*.cpp",
    "*.h",
    "*.hpp",
    "*.rs",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.cfg",
    "*.json",
    "*.xml",
    "*.urdf",
    "*.txt",
    "robotci.yaml",
    "robotci.yml",
)

#: Subtracted from the include set: documentation and CI plumbing of the
#: customer's own repo, which cannot change how the robot behaves.
DEFAULT_PATH_EXCLUDE: tuple[str, ...] = (
    "*.md",
    "*.rst",
    "docs/*",
    "doc/*",
    ".github/*",
    "LICENSE*",
    "*.png",
    "*.jpg",
    "*.gif",
    "*.mp4",
)


def normalise(path: str) -> str:
    """Strip a leading ``./`` and then a leading ``/``, and nothing else.

    ``str.lstrip`` takes a character *set*, so ``lstrip("./")`` would also eat
    the leading dot of ``.github/workflows/ci.yml`` and silently defeat the
    default ``.github/*`` exclusion. Applied identically to configured patterns
    and to incoming paths, so both sides of a comparison agree.
    """
    stripped = path.strip()
    if stripped.startswith("./"):
        stripped = stripped[2:]
    return stripped.removeprefix("/")


class Code:
    """Stable machine-readable reasons. Clients may switch on these."""

    STARTED = "started"
    NOT_WATCHED_BRANCH = "branch_not_watched"
    NO_MATCHING_PATHS = "no_matching_paths"
    PATHS_UNAVAILABLE = "changed_paths_unavailable"


@dataclass(frozen=True, slots=True)
class Filters:
    """Branch and path filters for one connected repository."""

    branches: tuple[str, ...] = DEFAULT_BRANCHES
    path_include: tuple[str, ...] = DEFAULT_PATH_INCLUDE
    path_exclude: tuple[str, ...] = DEFAULT_PATH_EXCLUDE

    def as_dict(self) -> dict[str, list[str]]:
        """JSON-friendly form, used in responses and log lines."""
        return {
            "branches": list(self.branches),
            "path_include": list(self.path_include),
            "path_exclude": list(self.path_exclude),
        }


@dataclass(frozen=True, slots=True)
class Decision:
    """The verdict on one push.

    ``code`` is stable and machine-readable; ``reason`` is the sentence shown
    on GitHub's delivery page and written to the log. ``matched_paths`` is the
    evidence for a start decision and is empty when paths were not filtered.
    """

    start: bool
    code: str
    reason: str
    matched_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"reason_code": self.code, "reason": self.reason}
        if self.matched_paths:
            payload["matched_paths"] = list(self.matched_paths)
        return payload


def _clean(patterns: Iterable[Any] | None, default: tuple[str, ...]) -> tuple[str, ...]:
    """Normalise a configured pattern list, falling back to ``default``.

    An explicitly empty list is *not* the default: ``path_exclude: []`` means
    "exclude nothing", which a team disabling the defaults must be able to say.
    """
    if patterns is None:
        return default
    if isinstance(patterns, str):
        patterns = [patterns]
    return tuple(
        normalise(str(pattern)) for pattern in patterns if str(pattern).strip()
    )


def parse(config: Mapping[str, Any] | None) -> Filters | None:
    """Read filters from a parsed ``robotci.yaml``.

    Returns ``None`` when the file carries no ``ci:`` section at all, which the
    caller distinguishes from an empty one: absent means "no opinion, keep
    whatever the registry says", present means "this is the truth".
    """
    if not config:
        return None
    section = config.get(CONFIG_SECTION)
    if not isinstance(section, Mapping):
        return None

    branches = section.get("branches", section.get("branch"))
    paths = section.get("paths")
    include = exclude = None
    if isinstance(paths, Mapping):
        include = paths.get("include")
        exclude = paths.get("exclude")
    elif paths is not None:
        include = paths

    return Filters(
        branches=_clean(branches, DEFAULT_BRANCHES),
        path_include=_clean(include, DEFAULT_PATH_INCLUDE),
        path_exclude=_clean(exclude, DEFAULT_PATH_EXCLUDE),
    )


def from_registry(
    *,
    branch: str,
    branches: Iterable[str] | None = None,
    path_include: Iterable[str] | None = None,
    path_exclude: Iterable[str] | None = None,
) -> Filters:
    """Filters as stored on a connected repository row.

    The registry keeps ``branch`` as its primary field (it is what the connect
    form asks for) plus optional extra patterns, so the watched branch set is
    the union of the two, and cannot be empty: there is no way to say "watch no
    branch". The path lists are nullable instead, because ``paths.exclude: []``
    in a repo's ``robotci.yaml`` is a real answer meaning "exclude nothing".
    ``None`` there means unset and falls back to the defaults; an empty list is
    honoured as configured.
    """
    watched = tuple(
        dict.fromkeys(
            [pattern for pattern in (branch,) if pattern]
            + [pattern for pattern in (branches or ()) if pattern]
        )
    )
    return Filters(
        branches=watched or DEFAULT_BRANCHES,
        path_include=(
            DEFAULT_PATH_INCLUDE
            if path_include is None
            else _clean(path_include, DEFAULT_PATH_INCLUDE)
        ),
        path_exclude=(
            DEFAULT_PATH_EXCLUDE
            if path_exclude is None
            else _clean(path_exclude, DEFAULT_PATH_EXCLUDE)
        ),
    )


def changed_paths(payload: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Every path touched by a push payload, or ``None`` when unknowable.

    GitHub lists ``added``/``removed``/``modified`` on each commit, but only for
    the first 20 commits of a push and never for more than 3000 files. When the
    payload carries commits with no file lists we return ``None`` rather than an
    empty tuple: "we do not know" and "nothing changed" must not collapse into
    the same value, because one of them would silently skip CI.
    """
    commits = payload.get("commits")
    if not isinstance(commits, Sequence) or isinstance(commits, str | bytes):
        commits = []
    entries = [commit for commit in commits if isinstance(commit, Mapping)]
    if not entries:
        head = payload.get("head_commit")
        entries = [head] if isinstance(head, Mapping) else []
    if not entries:
        return None

    paths: list[str] = []
    listed = False
    for commit in entries:
        for key in ("added", "removed", "modified"):
            values = commit.get(key)
            if values is None:
                continue
            if not isinstance(values, Sequence) or isinstance(values, str | bytes):
                continue
            listed = True
            paths.extend(str(value) for value in values if str(value).strip())

    if not listed:
        return None
    seen: dict[str, None] = {}
    for path in paths:
        seen.setdefault(normalise(path), None)
    return tuple(seen)


def matches(path: str, patterns: Iterable[str]) -> bool:
    """True when ``path`` matches any pattern, basename included.

    The basename check makes ``*.md`` mean what a reader expects it to mean for
    ``docs/deep/notes.md`` without forcing every pattern to be written ``**/``.
    """
    normalised = normalise(path)
    base = normalised.rsplit("/", 1)[-1]
    return any(
        fnmatchcase(normalised, pattern) or fnmatchcase(base, pattern)
        for pattern in patterns
    )


def branch_matches(branch: str, patterns: Iterable[str]) -> bool:
    """True when the pushed branch matches any watched branch pattern."""
    return any(fnmatchcase(branch, pattern) for pattern in patterns)


def select_paths(paths: Iterable[str], filters: Filters) -> tuple[str, ...]:
    """The subset of ``paths`` that is included and not excluded."""
    return tuple(
        path
        for path in paths
        if matches(path, filters.path_include)
        and not matches(path, filters.path_exclude)
    )


def evaluate(
    filters: Filters,
    *,
    branch: str,
    paths: Iterable[str] | None,
) -> Decision:
    """Decide whether this push starts a run.

    ``paths is None`` means the delivery did not tell us what changed. We start
    the run and say so in the reason — the alternative is skipping CI on a push
    we failed to inspect, which is the one failure mode a CI system may not
    have.
    """
    if not branch:
        return Decision(False, Code.NOT_WATCHED_BRANCH, "push carries no branch ref")

    if not branch_matches(branch, filters.branches):
        watched = ", ".join(filters.branches) or "(none)"
        return Decision(
            False,
            Code.NOT_WATCHED_BRANCH,
            f"branch {branch} is not watched (watching: {watched})",
        )

    if paths is None:
        return Decision(
            True,
            Code.PATHS_UNAVAILABLE,
            "changed paths are not in the delivery payload; "
            "path filters were not applied",
        )

    matched = select_paths(paths, filters)
    if not matched:
        return Decision(
            False,
            Code.NO_MATCHING_PATHS,
            "no changed path matches the configured path filters "
            f"(changed: {', '.join(sorted(paths)) or 'nothing'})",
        )

    return Decision(
        True,
        Code.STARTED,
        f"{len(matched)} changed path(s) match the path filters",
        matched_paths=matched,
    )
