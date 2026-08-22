"""The contract test: ``packages/contracts/schemas/*.json`` vs ``schemas.py``.

The JSON is the source of truth — it is what the dashboard parses. The Pydantic
models are hand-mirrored from it, and hand-mirroring only stays honest if
something checks it, which is this file.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, get_args

import pytest
from orchestrator import schemas
from pydantic import BaseModel

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "schemas"

#: ``title`` in the JSON -> the model that mirrors it.
ROOT_MODELS: dict[str, type[BaseModel]] = {
    "Agent": schemas.Agent,
    "Event": schemas.Event,
    "Finding": schemas.Finding,
    "Message": schemas.Message,
    "Report": schemas.Report,
    "Run": schemas.Run,
    "Scenario": schemas.Scenario,
}

#: Nested objects, addressed by the JSON pointer they sit at.
NESTED_MODELS: dict[str, type[BaseModel]] = {
    "run.json:/properties/robot_model": schemas.RobotModel,
    "run.json:/properties/suite": schemas.SuiteStats,
    "report.json:/properties/incidents/items": schemas.Incident,
    "report.json:/$defs/suiteStats": schemas.SuiteStats,
    "message.json:/properties/refs/items": schemas.Ref,
    "scenario.json:/properties/criteria/items": schemas.CriterionResult,
}

#: Enums the JSON and Python sides must agree on *exactly*.
EXACT_ENUMS: dict[str, type[Enum]] = {
    "agent.json:/$defs/role": schemas.Role,
    "agent.json:/$defs/status": schemas.AgentStatus,
    "event.json:/properties/type": schemas.EventType,
    "finding.json:/properties/kind": schemas.FindingKind,
    "finding.json:/properties/status": schemas.FindingStatus,
    "message.json:/properties/kind": schemas.MessageKind,
    "message.json:/properties/to_role": schemas.Speaker,
    "report.json:/properties/verdict": schemas.Verdict,
    "run.json:/$defs/stage": schemas.Stage,
    "run.json:/properties/robot_model/properties/source": schemas.ModelSource,
    "scenario.json:/properties/status": schemas.ScenarioStatus,
}

SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _walk(node: Any, pointer: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every dict in the document, with its JSON pointer."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        found.append((pointer, node))
        for key, value in node.items():
            found += _walk(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _walk(value, f"{pointer}/{index}")
    return found


def test_schema_directory_is_not_empty() -> None:
    assert SCHEMA_FILES, f"no contracts found under {SCHEMA_DIR}"


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_root_object_fields_match(path: Path) -> None:
    schema = _load(path)
    title = schema.get("title", "")
    model = ROOT_MODELS.get(title)
    assert model is not None, f"{path.name} has no mirrored model for {title!r}"

    schema_fields = set(schema.get("properties", {}))
    model_fields = set(model.model_fields)
    assert schema_fields == model_fields, (
        f"{path.name} and {model.__name__} disagree: "
        f"only in json {sorted(schema_fields - model_fields)}, "
        f"only in python {sorted(model_fields - schema_fields)}"
    )


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_required_fields_are_not_optional_in_python(path: Path) -> None:
    schema = _load(path)
    model = ROOT_MODELS[schema["title"]]
    for name in schema.get("required", []):
        field = model.model_fields[name]
        # A required property must be reachable without the caller guessing:
        # either mandatory or given a default we generate (ids, timestamps).
        assert (
            field.is_required()
            or field.default is not None
            or (field.default_factory is not None)
        ), f"{model.__name__}.{name} is required by {path.name} but has no value"


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_nested_object_fields_match(path: Path) -> None:
    schema = _load(path)
    checked = 0
    for pointer, node in _walk(schema):
        model = NESTED_MODELS.get(f"{path.name}:{pointer}")
        if model is None:
            continue
        checked += 1
        schema_fields = set(node.get("properties", {}))
        model_fields = set(model.model_fields)
        # Subset, not equality: one model can back several nested definitions,
        # and some of them narrow it (report.json's suiteStats omits the
        # baseline field, which only makes sense on a Run).
        assert schema_fields <= model_fields, (
            f"{path.name}{pointer} has fields {model.__name__} does not "
            f"mirror: {sorted(schema_fields - model_fields)}"
        )
    expected = sum(1 for key in NESTED_MODELS if key.startswith(f"{path.name}:"))
    assert checked == expected, f"{path.name}: nested pointers moved in the json"


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_every_enum_is_mirrored(path: Path) -> None:
    schema = _load(path)
    for pointer, node in _walk(schema):
        values = node.get("enum")
        if not isinstance(values, list):
            continue
        key = f"{path.name}:{pointer}"
        exact = EXACT_ENUMS.get(key)
        if exact is not None:
            assert [member.value for member in exact] == values, (
                f"{key} does not match {exact.__name__}"
            )
            continue
        # Not pinned to one enum (e.g. Literal unions, or a narrowed subset of
        # Speaker): every member must still exist somewhere in the module.
        assert _mirrored_somewhere(values), f"{key} has no mirror in schemas.py"


def _mirrored_somewhere(values: list[Any]) -> bool:
    wanted = set(values)
    for candidate in vars(schemas).values():
        if (
            isinstance(candidate, type)
            and issubclass(candidate, Enum)
            and wanted <= {member.value for member in candidate}
        ):
            return True
    for model in ROOT_MODELS.values():
        if _in_literal(model, wanted):
            return True
    for model in NESTED_MODELS.values():
        if _in_literal(model, wanted):
            return True
    return False


def _in_literal(model: type[BaseModel], wanted: set[Any]) -> bool:
    for field in model.model_fields.values():
        if wanted <= set(get_args(field.annotation)):
            return True
    return False


def test_event_types_cover_the_protocol_doc() -> None:
    """Every event type in the JSON exists as an EventType member."""
    schema = _load(SCHEMA_DIR / "event.json")
    assert [e.value for e in schemas.EventType] == schema["properties"]["type"]["enum"]
