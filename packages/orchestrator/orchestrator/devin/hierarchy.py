"""Bound the repair-agent tree so the pipeline spends seats on asked work.

The repair contract is exactly three seats deep: the cluster owner reproduces
and diagnoses, a Fixer proposes a patch, and a Reviewer verifies it. A fourth
seat would be an agent inventing work no simulation asked for. A parent's
children are its repair attempts; ``PipelineContext.max_fix_iterations`` is
three, so four seats provide three attempts plus one spare. More than that is
thrashing, not converging.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

MAX_AGENT_TREE_DEPTH = int(os.getenv("MAX_AGENT_TREE_DEPTH", "3"))
MAX_AGENT_CHILDREN = int(os.getenv("MAX_AGENT_CHILDREN", "4"))


@dataclass
class _AgentNode:
    agent_id: str
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)


class AgentTree:
    """Small in-memory record of the agent hierarchy built by one pipeline."""

    def __init__(
        self,
        *,
        max_depth: int = MAX_AGENT_TREE_DEPTH,
        max_children: int = MAX_AGENT_CHILDREN,
    ) -> None:
        self.max_depth = max_depth
        self.max_children = max_children
        self._nodes: dict[str, _AgentNode] = {}
        self._roots: list[str] = []

    def register_root(self, agent_id: str) -> None:
        """Register a cluster owner at depth one."""
        if agent_id in self._nodes:
            raise ValueError(f"agent already registered: {agent_id}")
        self._nodes[agent_id] = _AgentNode(agent_id=agent_id)
        self._roots.append(agent_id)

    def register_child(self, parent_id: str, agent_id: str) -> None:
        """Register a spawned child after :meth:`child_refusal` allows it."""
        if agent_id in self._nodes:
            raise ValueError(f"agent already registered: {agent_id}")
        if parent_id not in self._nodes:
            raise ValueError(f"unknown parent agent: {parent_id}")
        refusal = self.child_refusal(parent_id)
        if refusal is not None:
            raise ValueError(refusal)
        self._nodes[agent_id] = _AgentNode(agent_id=agent_id, parent_id=parent_id)
        self._nodes[parent_id].children.append(agent_id)

    def depth(self, agent_id: str) -> int:
        """Return one-based tree depth, with roots at depth one."""
        if agent_id not in self._nodes:
            raise KeyError(agent_id)
        depth = 1
        current = self._nodes[agent_id]
        while current.parent_id is not None:
            depth += 1
            current = self._nodes[current.parent_id]
        return depth

    def children(self, agent_id: str) -> tuple[str, ...]:
        """Return a parent's children in registration order."""
        if agent_id not in self._nodes:
            raise KeyError(agent_id)
        return tuple(self._nodes[agent_id].children)

    def has(self, agent_id: str) -> bool:
        """Return whether an agent is already in the tree."""
        return agent_id in self._nodes

    def child_refusal(self, parent_id: str) -> str | None:
        """Explain why another child cannot be spawned, or return ``None``."""
        if parent_id not in self._nodes:
            return f"agent tree parent unavailable: {parent_id}"
        node = self._nodes[parent_id]
        if self.depth(parent_id) >= self.max_depth:
            return (
                f"agent tree depth cap reached at depth {self.depth(parent_id)} "
                f"(MAX_AGENT_TREE_DEPTH={self.max_depth})"
            )
        if len(node.children) >= self.max_children:
            return (
                f"agent tree child cap reached for {parent_id} "
                f"(MAX_AGENT_CHILDREN={self.max_children})"
            )
        return None

    def render(self) -> str:
        """Render the recorded tree as compact indented text."""
        lines: list[str] = []

        def visit(agent_id: str, indent: int) -> None:
            lines.append("  " * indent + agent_id)
            for child_id in self._nodes[agent_id].children:
                visit(child_id, indent + 1)

        for root_id in self._roots:
            visit(root_id, 0)
        return "\n".join(lines)
