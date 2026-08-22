"""Shared machinery for every role: prompt rendering, dispatch, validation.

Responsibility
--------------
Give the seven role modules one implementation of the parts they all share, so
a role module contains only what makes that role different.

Inputs:  a :class:`~orchestrator.pipeline.PipelineContext`, role-specific
         template variables.
Outputs: a completed :class:`~orchestrator.schemas.Agent`, one or more
         :class:`~orchestrator.schemas.Finding` objects written to the
         blackboard, and the relay Messages that carry them.

The contract every role honours
-------------------------------
1. Render ``_shared.md`` + the role's own template with the blackboard context
   the relay policy allows this role to see.
2. Start a session, stream activity to the bus.
3. Parse structured output; **reject free text**.
4. Convert output into findings and write them to the board.
5. Never accept the agent's own claim of success — the caller verifies with
   simkit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.schemas import Agent, Finding, Role

if TYPE_CHECKING:
    from orchestrator.pipeline import PipelineContext

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "devin" / "prompts"


class RoleAgent(ABC):
    """Base class for one seat on the team."""

    #: Which seat this is. Set by each subclass.
    role: Role
    #: Filename in ``devin/prompts/``. Defaults to ``<role>.md``.
    prompt_file: str = ""
    #: Human label prefix shown on the dashboard card.
    display_name: str = ""

    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx

    # -- prompt ------------------------------------------------------------ #

    def load_template(self) -> str:
        """Read ``_shared.md`` and this role's template, concatenated."""
        raise NotImplementedError
        # TODO(build): read both files, join with a separator, cache per process.

    @abstractmethod
    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Role-specific substitutions for the template placeholders."""

    def render_prompt(self, **kwargs: Any) -> str:
        """Fill the template, including the blackboard context for this role."""
        raise NotImplementedError
        # TODO(build): str.format-style or minimal {{var}} substitution over
        # template_vars() plus blackboard.render_context(self.role).

    # -- dispatch ---------------------------------------------------------- #

    async def dispatch(self, **kwargs: Any) -> Agent:
        """Render, start the session, await it, parse output, write findings.

        The single entrypoint the pipeline calls. Subclasses override the hooks,
        not this method.
        """
        raise NotImplementedError
        # TODO(build): render_prompt -> AgentSession.start -> wait ->
        # output() -> validate_output() -> to_findings() -> blackboard.write.

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """Check the structured output has the fields this role promised.

        Raises on a malformed block so ``dispatch`` can retry once with an
        explicit reminder before failing the agent.
        """
        raise NotImplementedError
        # TODO(build): per-role required-key check; coerce confidence to float.

    @abstractmethod
    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert this role's output into blackboard findings."""

    # -- relay ------------------------------------------------------------- #

    async def relay(self, finding: Finding, to_role: Role, kind: str) -> None:
        """Speak a finding into another live session and emit the Message.

        This is the *only* mechanism by which two agents exchange information.
        See ``docs/AGENT_ROLES.md``.
        """
        raise NotImplementedError
        # TODO(build): find the live AgentSession for to_role, send_message
        # with the rendered finding, emit a Message on the bus.


# TODO(build): add a `MockRoleAgent` that returns canned output without calling
# Devin, so the pipeline and UI can be exercised offline and on stage if the
# API is down.
