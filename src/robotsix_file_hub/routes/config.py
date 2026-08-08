"""The standard config HTTP surface.

Required of every deployable component by robotsix-standards
``config-ownership.md``. The deploy plane keeps no copy of these values — it
reads them from the component — so this surface is how config is inspected and
changed at runtime, and the ``<config>.versions`` sidecar beside the config
file is where its history lives.

All four handlers delegate to :mod:`robotsix_config.history`. That is
deliberate: ``PUT /config`` has to deep-merge, restore secrets the caller did
not really resubmit, validate, write, and record — in that order — and
reimplementing that sequence per component is how a form save ends up erasing
a live credential.
"""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from robotsix_config import (
    InvalidConfigError,
    apply_update,
    current_version,
    mask_secrets,
    read_versions,
    resolve_config_path,
    rollback,
)

from ..auth import get_current_user
from ..config import Settings, reload_settings

router = APIRouter(tags=["config"], dependencies=[Depends(get_current_user)])


def _read_config_file() -> dict[str, Any]:
    """Return the raw on-disk config.

    Read from the file rather than dumping the loaded model so ``GET /config``
    reflects what is actually persisted, and agrees with what ``PUT /config``
    merges into.
    """
    try:
        loaded = json.loads(resolve_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}


def _masked(raw: dict[str, Any]) -> dict[str, Any]:
    return mask_secrets(raw, Settings)


@router.get("/config")
def read_config() -> dict[str, Any]:
    """Effective config with secrets masked, plus schema and version."""
    return {
        "config": _masked(_read_config_file()),
        "schema": Settings.model_json_schema(),
        "version": current_version(),
    }


@router.put("/config")
def write_config(update: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update and record a new version.

    Keys omitted from *update* keep their current values. A secret submitted
    as the mask sentinel or as an empty string counts as unchanged.
    """
    try:
        merged, _changed, version = apply_update(Settings, update)
    except InvalidConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Republish so the change takes effect without a restart; otherwise the
    # value lands on disk and every handler keeps serving the settings loaded
    # at startup, making a successful save look like a no-op.
    reload_settings()
    return {"config": _masked(merged), "version": version}


@router.get("/config/versions")
def config_versions() -> dict[str, Any]:
    """The version history, newest first, without the snapshots."""
    return {"versions": list(reversed(read_versions(include_data=False)))}


@router.post("/config/rollback")
def config_rollback(body: dict[str, Any]) -> dict[str, Any]:
    """Restore an earlier version as a new version.

    Secrets are not rolled back: the history never stores them, so they are
    carried forward at their current values rather than being blanked.
    """
    target = body.get("version")
    if not isinstance(target, int):
        raise HTTPException(status_code=422, detail="'version' must be an integer")
    try:
        restored, _changed, version = rollback(Settings, target)
    except InvalidConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    reload_settings()
    return {"config": _masked(restored), "version": version}
