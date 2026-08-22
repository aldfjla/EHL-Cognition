"""Robot CI orchestrator — the autonomous layer.

Framework-free core logic, importable from the FastAPI app, the CLI, or tests.
Nothing in this package may import from ``apps.api``: the dependency runs one
way only, so the pipeline can be driven headlessly.

Start at :mod:`orchestrator.pipeline`.
"""

__version__ = "0.1.0"
