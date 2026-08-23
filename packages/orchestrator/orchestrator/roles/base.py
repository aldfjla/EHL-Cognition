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

import asyncio
import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.devin.session import AgentSession, live_sessions
from orchestrator.schemas import (
    Agent,
    AgentStatus,
    EventType,
    Finding,
    FindingKind,
    Message,
    MessageKind,
    Ref,
    Role,
    Speaker,
)

if TYPE_CHECKING:
    from orchestrator.pipeline import PipelineContext

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "devin" / "prompts"

#: Prepended to every role prompt.
SHARED_PROMPT_FILE = "_shared.md"

#: Placed between ``_shared.md`` and the role's own template.
PROMPT_SEPARATOR = "\n\n---\n\n"

#: ``{{var}}`` — the only substitution syntax the templates use.
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

#: Rendered in place of a variable no role supplied.
MISSING = "(not provided)"

#: No role is dispatched with less than this, even when the run deadline is
#: nearly spent: a session that cannot possibly answer is worse than none.
MIN_AGENT_TIMEOUT_S = 60.0

#: Sent when the first structured output does not satisfy ``validate_output``.
RETRY_REMINDER = (
    "Your structured output was rejected: {reason}. Post a single fenced json "
    "block with exactly the keys your instructions specify, then finish."
)

#: Fields of :class:`~orchestrator.schemas.Agent` a caller may set at dispatch.
AGENT_FIELDS = (
    "cluster_id",
    "scenario_ids",
    "parent_agent_id",
    "iteration",
    "max_iterations",
)

_TEMPLATE_CACHE: dict[str, str] = {}


def _fmt(value: Any) -> str:
    """Render a template variable as prompt-friendly markdown."""
    if value is None:
        return "(none)"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        if not value:
            return "(none)"
        return json.dumps(value, indent=2, default=str, sort_keys=True)
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if not items:
            return "(none)"
        if all(isinstance(item, (str, int, float)) for item in items):
            return ", ".join(str(item) for item in items)
        return "\n".join(f"- {_fmt(item)}" for item in items)
    if isinstance(value, Finding):
        return f"[{value.author_role.value}, {value.confidence:.2f}] {value.summary}"
    return str(value)


def summarise_config(config: dict[str, Any]) -> str:
    """One-line digest of ``robotci.yaml`` for the shared prompt header."""
    if not config:
        return "none found — every field is inferred"
    control = config.get("control") or {}
    robot = config.get("robot") or {}
    task = config.get("task") or {}
    parts = [
        f"entrypoint={control.get('entrypoint', 'unknown')}",
        f"interface={control.get('interface', 'inferred')}",
        f"rate_hz={control.get('rate_hz', 'inferred')}",
        f"robot={robot.get('menagerie') or robot.get('model_path') or 'inferred'}",
        f"task={task.get('name', 'inferred')}",
    ]
    return ", ".join(parts)


def render_finding(finding: Finding) -> str:
    """Markdown a relay speaks into another session."""
    header = (
        f"**{finding.kind.value}** from the {finding.author_role.value} "
        f"(confidence {finding.confidence:.2f}): {finding.summary}"
    )
    lines = [header]
    if finding.detail:
        lines += ["", finding.detail.strip()]
    if finding.files:
        lines += ["", "Files: " + ", ".join(finding.files)]
    return "\n".join(lines)


class RoleAgent(ABC):
    """Base class for one seat on the team."""

    #: Which seat this is. Set by each subclass.
    role: Role
    #: Filename in ``devin/prompts/``. Defaults to ``<role>.md``.
    prompt_file: str = ""
    #: Human label prefix shown on the dashboard card.
    display_name: str = ""
    #: Force a brand-new Devin session on every dispatch. Devin's ``idempotent``
    #: flag can hand back an existing session for an identical prompt, which
    #: carries that session's context. Roles that must reason from a clean slate
    #: set this so two dispatches never share a context window.
    fresh_session: bool = False
    #: Keys ``validate_output`` insists on. Set by each subclass.
    required_keys: tuple[str, ...] = ()

    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        #: The live session, once :meth:`dispatch` has started one.
        self.session: AgentSession | None = None
        #: The validated structured output of the last dispatch.
        self.output: dict[str, Any] = {}
        #: Findings this role produced on its last dispatch, in order.
        self.findings: list[Finding] = []

    # -- prompt ------------------------------------------------------------ #

    def load_template(self) -> str:
        """Read ``_shared.md`` and this role's template, concatenated."""
        name = self.prompt_file or f"{self.role.value}.md"
        cached = _TEMPLATE_CACHE.get(name)
        if cached is None:
            shared = (PROMPTS_DIR / SHARED_PROMPT_FILE).read_text(encoding="utf-8")
            own = (PROMPTS_DIR / name).read_text(encoding="utf-8")
            cached = shared.strip() + PROMPT_SEPARATOR + own.strip() + "\n"
            _TEMPLATE_CACHE[name] = cached
        return cached

    @abstractmethod
    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Role-specific substitutions for the template placeholders."""

    def shared_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions ``_shared.md`` needs, identical for every role."""
        ctx = self.ctx
        return {
            "repo": ctx.run.repo,
            "commit_sha": ctx.run.commit_sha,
            "workdir": str(kwargs.get("workdir") or ctx.workspace.base),
            "config_summary": summarise_config(ctx.config),
            "blackboard_context": ctx.blackboard.render_context(
                self.role, cluster_id=kwargs.get("cluster_id")
            )
            or "Nothing yet — you are early.",
        }

    def render_prompt(self, **kwargs: Any) -> str:
        """Fill the template, including the blackboard context for this role."""
        variables = self.shared_vars(**kwargs)
        variables.update(self.template_vars(**kwargs))
        rendered = {key: _fmt(value) for key, value in variables.items()}
        return PLACEHOLDER.sub(
            lambda match: rendered.get(match.group(1), MISSING), self.load_template()
        )

    # -- dispatch ---------------------------------------------------------- #

    def title(self, **kwargs: Any) -> str:
        """Card title for the dashboard."""
        label = kwargs.get("cluster_label") or kwargs.get("title")
        name = self.display_name or self.role.value
        return f"{name} — {label}" if label else name

    def agent_fields(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Agent columns the caller supplied, e.g. the cluster being worked."""
        return {key: kwargs[key] for key in AGENT_FIELDS if key in kwargs}

    async def dispatch(self, **kwargs: Any) -> Agent:
        """Render, start the session, await it, parse output, write findings.

        The single entrypoint the pipeline calls. Subclasses override the hooks,
        not this method.
        """
        prompt = self.render_prompt(**kwargs)
        timeout_s = float(
            kwargs.get("timeout_s") or os.getenv("AGENT_TIMEOUT_S", "1800")
        )
        # The run has a wall-clock ceiling; a role may not outlive it. Waiting
        # 30 minutes on a session the run will never read is how a pipeline
        # advertised as ten minutes takes an hour.
        remaining = self.ctx.remaining_s()
        if remaining is not None:
            timeout_s = max(MIN_AGENT_TIMEOUT_S, min(timeout_s, remaining))
        session = await AgentSession.start(
            run_id=self.ctx.run.id,
            role=self.role,
            prompt=prompt,
            title=self.title(**kwargs),
            task=kwargs.get("task") or self.display_name or self.role.value,
            client=self.ctx.devin,
            bus=self.ctx.bus,
            idempotent=not self.fresh_session,
            **self.agent_fields(kwargs),
        )
        self.session = session

        try:
            await session.wait(timeout_s=timeout_s)
            output = await session.output()
            try:
                output = self.validate_output(output)
            except ValueError as first:
                # One reminder, then fail: prose the pipeline cannot verify is
                # not an acceptable result.
                await session.ask(RETRY_REMINDER.format(reason=first))
                await session.wait(timeout_s=timeout_s)
                output = self.validate_output(await session.output())
        except Exception as exc:
            if not session.agent.status.is_terminal:
                await session.set_status(AgentStatus.FAILED, str(exc))
            raise

        self.output = output
        self.findings = self.to_findings(session.agent, output)
        for finding in self.findings:
            await self.ctx.blackboard.write(finding)
            session.agent.finding_ids.append(finding.id)
        await session.set_status(AgentStatus.SUCCEEDED)
        return session.agent

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """Check the structured output has the fields this role promised.

        Raises on a malformed block so ``dispatch`` can retry once with an
        explicit reminder before failing the agent.
        """
        if not isinstance(output, dict) or not output:
            raise ValueError("structured output is empty")
        missing = [key for key in self.required_keys if output.get(key) in (None, "")]
        if missing:
            raise ValueError(f"missing required keys: {', '.join(missing)}")
        cleaned = dict(output)
        if "confidence" in cleaned:
            try:
                confidence = float(cleaned["confidence"])
            except (TypeError, ValueError) as exc:
                raise ValueError("confidence must be a number") from exc
            cleaned["confidence"] = min(1.0, max(0.0, confidence))
        return cleaned

    @abstractmethod
    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert this role's output into blackboard findings."""

    # -- finding helpers --------------------------------------------------- #

    def finding(
        self,
        agent: Agent,
        kind: FindingKind,
        summary: str,
        detail: str = "",
        *,
        confidence: float | None = None,
        files: list[str] | None = None,
    ) -> Finding:
        """A :class:`Finding` authored by this role, wired to ``agent``."""
        return Finding(
            run_id=agent.run_id,
            author_agent_id=agent.id,
            author_role=Speaker(self.role.value),
            kind=kind,
            summary=str(summary).strip(),
            detail=str(detail or "").strip(),
            cluster_id=agent.cluster_id,
            scenario_ids=list(agent.scenario_ids),
            files=[str(f) for f in (files or [])],
            confidence=0.5 if confidence is None else min(1.0, max(0.0, confidence)),
        )

    # -- relay ------------------------------------------------------------- #

    async def relay(self, finding: Finding, to_role: Role, kind: str) -> None:
        """Speak a finding into another live session and emit the Message.

        This is the *only* mechanism by which two agents exchange information.
        See ``docs/AGENT_ROLES.md``.
        """
        body = render_finding(finding)
        targets = live_sessions(self.ctx.run.id, to_role)
        from_agent_id = self.session.agent.id if self.session else None
        speech_act = MessageKind(kind)
        ref = Ref(type="finding", id=finding.id, label=finding.summary[:60])

        # A relay with no live recipient still belongs on the board: the next
        # agent for that seat picks it up from the blackboard context.
        for target in targets or [None]:
            if target is not None:
                await target.client.send_message(target.agent.session_id, body)
            message = Message(
                run_id=self.ctx.run.id,
                from_agent_id=from_agent_id,
                to_agent_id=target.agent.id if target is not None else None,
                from_role=Speaker(self.role.value),
                to_role=Speaker(to_role.value),
                kind=speech_act,
                body=body,
                refs=[ref],
            )
            await self.ctx.bus.emit(
                self.ctx.run.id,
                EventType.MESSAGE_SENT,
                message.model_dump(mode="json"),
            )


#: Canned structured output per role, matching each prompt's schema. Used by
#: :class:`MockRoleAgent` so the pipeline and the dashboard can be exercised
#: with no Devin API at all.
CANNED_OUTPUT: dict[Role, dict[str, Any]] = {
    Role.MODELER: {
        "source": "menagerie",
        "name": "franka_emika_panda",
        "model_path": "vendor/menagerie/franka_emika_panda/panda.xml",
        "dof": 7,
        "confidence": 0.8,
        "reasoning": "Driver imports and the 7-entry joint limit table match a Panda.",
        "assumptions": ["Payload rating taken from the datasheet, not the repo"],
    },
    Role.HARNESS_BUILDER: {
        "harness_path": "harness.py",
        "harness_code": "def run_episode(model, data, params):\n    ...\n",
        "smoke_passed": True,
        "interface_notes": "joint_position commands map onto position actuators.",
        "shims": ["Faked `arm_driver.ArmClient` with a MuJoCo-backed stub"],
        "confidence": 0.7,
        "constraints": ["Their code assumes joint 0 starts at 0 rad"],
    },
    Role.SCENARIO_DESIGNER: {
        "axes": {
            "object_mass_kg": {"low": 0.1, "high": 0.8, "why": "Grip force is fixed"},
            "friction": {"low": 0.4, "high": 1.2, "why": "Straddles the slip point"},
        },
        "include_nominal": True,
        "notes": "GRIP_TIMEOUT=2.0s at controller.py:88",
        "confidence": 0.6,
    },
    Role.INVESTIGATOR: {
        "reproduced": True,
        "root_cause": (
            "The controller starts closing the gripper at a fixed 2.0 s "
            "(controller.py:88) regardless of approach distance."
        ),
        "evidence": "Re-ran seed 4471; the grasp closes 0.4 s before contact.",
        "files": ["src/controller.py"],
        "confidence": 0.75,
        "suggested_direction": "Gate the close on contact, not on elapsed time.",
        "observations": ["Low-friction worlds also approach slower"],
    },
    Role.FIXER: {
        "patched": True,
        "diff_summary": "Close the gripper on measured contact instead of a timer.",
        "files_changed": ["src/controller.py"],
        "cluster_seeds_passing": True,
        "regression_check": "Sampled 6 previously-passing seeds; all still pass.",
        "confidence": 0.7,
        "residual_risk": "Untested against payloads above the arm's rating.",
    },
    Role.REVIEWER: {
        "verdict": "ship",
        "accepted_findings": [],
        "rejected_findings": [],
        "superseded": [],
        "conflict_resolution": "No overlapping patches.",
        "remaining_failures": "None.",
        "confidence": 0.8,
    },
    Role.REPORTER: {
        "verdict": "fixed",
        "title": "Close the gripper on contact instead of a fixed timer",
        "summary": "The gripper closed before reaching the cube on slow approaches.",
        "incidents": [],
    },
}


class MockRoleAgent(RoleAgent):
    """Offline stand-in for any role: canned output, no Devin API call.

    Exists so the pipeline and the dashboard can be driven with the API down —
    including on stage. It still emits every event a real role emits and still
    writes findings to the blackboard, so what the UI receives is identical.
    """

    role = Role.INVESTIGATOR

    def __init__(
        self,
        ctx: PipelineContext,
        role: Role = Role.INVESTIGATOR,
        output: dict[str, Any] | None = None,
        *,
        findings: list[Finding] | None = None,
        delay_s: float = 0.0,
        activity: list[str] | None = None,
    ) -> None:
        super().__init__(ctx)
        self.role = role
        self.prompt_file = f"{role.value}.md"
        self.display_name = role.value.replace("_", " ").title()
        self.canned = dict(
            output if output is not None else CANNED_OUTPUT.get(role, {})
        )
        self.canned_findings = findings
        self.delay_s = delay_s
        self.activity = activity or [
            f"[mock] {role.value} reading the repo",
            f"[mock] {role.value} finished",
        ]

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Pass the caller's kwargs straight through — nothing is required."""
        return dict(kwargs)

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """Canned output is trusted; only the confidence coercion applies."""
        return super().validate_output(output) if output else {}

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """The findings handed to the constructor, or one generic observation."""
        if self.canned_findings is not None:
            return list(self.canned_findings)
        summary = str(
            output.get("root_cause")
            or output.get("diff_summary")
            or output.get("summary")
            or output.get("interface_notes")
            or output.get("notes")
            or f"{self.role.value} completed (mock)"
        )
        return [
            self.finding(
                agent,
                FindingKind.OBSERVATION,
                summary.splitlines()[0][:200],
                detail=json.dumps(output, indent=2, default=str),
                confidence=float(output.get("confidence", 0.5) or 0.5),
            )
        ]

    async def dispatch(self, **kwargs: Any) -> Agent:
        """Same event sequence as a real dispatch, with no HTTP anywhere."""
        agent = Agent(
            run_id=self.ctx.run.id,
            role=self.role,
            title=self.title(**kwargs),
            task=kwargs.get("task") or f"{self.display_name} (mock)",
            status=AgentStatus.STARTING,
            session_id="mock",
            session_url="https://app.devin.ai/sessions/mock",
            **self.agent_fields(kwargs),
        )
        session = AgentSession(agent, client=None, bus=self.ctx.bus)
        self.session = session
        await self.ctx.bus.emit(
            agent.run_id, EventType.AGENT_CREATED, agent.model_dump(mode="json")
        )
        await session.set_status(AgentStatus.WORKING)

        for line in self.activity:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            session.transcript.append(line)
            agent.last_activity = line
            await self.ctx.bus.emit(
                agent.run_id,
                EventType.AGENT_ACTIVITY,
                {"agent_id": agent.id, "text": line},
            )

        self.output = self.validate_output(dict(self.canned))
        for finding in self.to_findings(agent, self.output):
            await self.ctx.blackboard.write(finding)
            agent.finding_ids.append(finding.id)
        await session.set_status(AgentStatus.SUCCEEDED)
        return agent
