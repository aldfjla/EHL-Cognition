"""simkit — the oracle.

Deterministic, agent-free simulation. Everything in this package can be run,
inspected and trusted without an LLM in the loop, which is the entire reason the
autonomous layer above it can be trusted at all: agents propose, simkit disposes.

Two invariants hold everywhere in this package:

1. **Determinism.** A ``(model, harness, seed)`` triple always produces the same
   result. Without this, no failure is reproducible and no agent can debug.
2. **No agent calls.** Nothing here imports :mod:`orchestrator`. The dependency
   runs one way.
"""

__version__ = "0.1.0"
