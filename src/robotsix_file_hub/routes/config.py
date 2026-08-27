"""The standard config HTTP surface.

Required of every deployable component by robotsix-standards
``config-ownership.md``. The deploy plane keeps no copy of these values — it
reads them from the component — so this surface is how config is inspected and
changed at runtime, and the ``<config>.versions`` sidecar beside the config
file is where its history lives.

All five handlers delegate to :mod:`robotsix_config.history`. That is
deliberate: ``PUT /config`` has to deep-merge, restore secrets the caller did
not really resubmit, validate, write, and record — in that order — and
reimplementing that sequence per component is how a form save ends up erasing
a live credential.
"""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from robotsix_config import (
    InvalidConfigError,
    apply_update,
    current_version,
    mask_secrets,
    read_versions,
    resolve_config_path,
    rollback,
)
from robotsix_config.history import _read_raw, _write_raw, record_version

from ..config import Settings, reload_settings
from ..rate_limiter import DEFAULT_RATE_LIMIT, limiter

router = APIRouter(tags=["config"])


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
@limiter.limit(DEFAULT_RATE_LIMIT)
def read_config(request: Request) -> dict[str, Any]:
    """Effective config with secrets masked, plus schema and version."""
    return {
        "config": _masked(_read_config_file()),
        "schema": Settings.model_json_schema(),
        "version": current_version(),
    }


@router.put("/config")
@limiter.limit(DEFAULT_RATE_LIMIT)
def write_config(request: Request, update: dict[str, Any]) -> dict[str, Any]:
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
@limiter.limit(DEFAULT_RATE_LIMIT)
def config_versions(request: Request) -> dict[str, Any]:
    """The version history, newest first, without the snapshots."""
    return {"versions": list(reversed(read_versions(include_data=False)))}


@router.post("/config/rollback")
@limiter.limit(DEFAULT_RATE_LIMIT)
def config_rollback(request: Request, body: dict[str, Any]) -> dict[str, Any]:
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


def _schema_properties(model_cls: type[Any]) -> dict[str, dict[str, Any]]:
    """Return the top-level ``properties`` from *model_cls*'s JSON Schema.

    Nested ``$ref`` entries in ``$defs`` are resolved so that sub-model
    properties are available for recursive pruning.
    """
    schema = model_cls.model_json_schema()
    defs: dict[str, dict[str, Any]] = schema.get("$defs", {})
    resolved: dict[str, dict[str, Any]] = {}

    def _resolve_ref(ref: str) -> dict[str, Any]:
        name = ref.rsplit("/", 1)[-1]
        return defs.get(name, {})

    for key, prop in (schema.get("properties") or {}).items():
        if "$ref" in prop:
            resolved[key] = _resolve_ref(prop["$ref"])
        else:
            resolved[key] = prop
    return resolved


def _prune_to_schema(
    data: dict[str, Any],
    properties: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Remove keys from *data* that are not in *properties*.

    Recurses into nested objects whose schema defines ``properties``.

    Returns:
        ``(pruned_data, removed_keys)`` where *removed_keys* are the
        dotted paths of every removed key.
    """
    pruned: dict[str, Any] = {}
    removed: list[str] = []
    for key, value in data.items():
        if key not in properties:
            removed.append(key)
            continue
        prop_schema = properties[key]
        if isinstance(value, dict) and "properties" in prop_schema:
            nested, nested_removed = _prune_to_schema(value, prop_schema["properties"])
            pruned[key] = nested
            removed.extend(f"{key}.{r}" for r in nested_removed)
        else:
            pruned[key] = value
    return pruned, removed


@router.post("/config/prune")
@limiter.limit(DEFAULT_RATE_LIMIT)
def prune_config(request: Request) -> dict[str, Any]:
    """Remove config keys not present in the current schema.

    ``PUT /config`` deep-merges, so keys removed from the schema (e.g.
    legacy ``s3_*``, ``storage_backend``, ``enrichment_llm_*``) linger
    on disk forever.  This endpoint strips them in one shot, validates
    the result, writes atomically, and records a version so the removal
    is auditable and reversible via rollback.
    """
    path = resolve_config_path()
    existing = _read_raw(path)
    if not existing:
        return {"config": {}, "removed": [], "version": current_version(path)}

    props = _schema_properties(Settings)
    pruned, removed = _prune_to_schema(existing, props)

    if not removed:
        return {
            "config": _masked(pruned),
            "removed": [],
            "version": current_version(path),
        }

    # Validate the pruned config against the model before touching the file.
    try:
        Settings.model_validate(pruned)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Pruned config is invalid: {exc}") from exc

    # Ensure an initial version exists if this is the first recorded change.
    if not read_versions(path, include_data=False) and existing:
        record_version(existing, ["initial"], Settings, path)

    _write_raw(path, pruned)
    version = record_version(pruned, sorted(removed), Settings, path)
    reload_settings()
    return {
        "config": _masked(pruned),
        "removed": sorted(removed),
        "version": version,
    }
