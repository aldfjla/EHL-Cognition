"""The engineering team: one module per seat.

Each role wraps a prompt template, a structured-output contract and the rules
for what it may read off the blackboard. Roles never call HTTP directly — they
go through :class:`~orchestrator.devin.session.AgentSession`.

See ``docs/AGENT_ROLES.md`` for the org chart and the relay policy.
"""

from orchestrator.schemas import Role

__all__ = ["Role"]
