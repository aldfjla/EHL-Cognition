"""Robot model catalogue endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from simkit.models import menagerie

from app.config import Settings
from app.deps import get_config

router = APIRouter(tags=["models"])

_BASELINE = {
    "name": "franka_emika_panda",
    "dof": 9,
    "kind": "arm",
}


def _model_info(model: Any) -> dict[str, Any]:
    return {"name": model.name, "dof": model.dof, "kind": model.kind}


@router.get("/models")
async def list_models(settings: Settings = Depends(get_config)) -> list[dict[str, Any]]:
    """List Menagerie models, retaining a usable baseline without a checkout."""
    try:
        indexed = menagerie.index(settings.menagerie_dir)
    except Exception:  # noqa: BLE001 - the catalogue must not take down the API
        indexed = []

    if not indexed:
        return [_BASELINE]

    models = [_model_info(model) for model in indexed]
    baseline = next(
        (model for model in models if model["name"] == _BASELINE["name"]),
        None,
    )
    if baseline is None:
        baseline = _BASELINE
        models = [baseline, *models]
    elif models[0] is not baseline:
        models = [baseline, *[model for model in models if model is not baseline]]
    return models
