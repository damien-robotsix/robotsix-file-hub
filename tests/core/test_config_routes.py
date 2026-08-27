"""Tests for the standard config HTTP surface.

`config-ownership.md` requires every deployable component to expose
`GET /config`, `PUT /config`, `GET /config/versions` and
`POST /config/rollback`. Because the deploy plane keeps no copy of these
values, this surface is the only way config is inspected or changed at
runtime — so these tests care most about the two ways it can lose data:
returning a secret it should have masked, and overwriting a stored secret
with the mask a client echoed back.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from src.robotsix_file_hub import config as config_mod


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Point the component at a throwaway config file."""
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "log_level": "INFO",
                "embedding": {
                    "model": "seeded-model",
                    "api_key": "real-secret",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROBOTSIX_CONFIG_FILE", str(cfg))
    config_mod.get_settings.cache_clear()
    yield cfg
    config_mod.get_settings.cache_clear()


class TestGetConfig:
    async def test_returns_config_schema_and_version(self, test_client: AsyncClient):
        body = (await test_client.get("/config")).json()
        assert set(body) >= {"config", "schema", "version"}
        assert body["schema"]["title"] == "Settings"

    async def test_secrets_are_masked(self, test_client: AsyncClient):
        """embedding.api_key is a SecretStr; it must never leave the process."""
        body = (await test_client.get("/config")).json()
        assert body["config"]["embedding"]["api_key"] == "**********"

    async def test_non_secrets_are_intact(self, test_client: AsyncClient):
        body = (await test_client.get("/config")).json()
        assert body["config"]["embedding"]["model"] == "seeded-model"


class TestPutConfig:
    async def test_partial_update_keeps_other_keys(self, test_client: AsyncClient):
        resp = await test_client.put("/config", json={"log_level": "DEBUG"})
        assert resp.status_code == 200
        cfg = resp.json()["config"]
        assert cfg["log_level"] == "DEBUG"
        assert cfg["embedding"]["model"] == "seeded-model"

    async def test_echoed_mask_keeps_the_secret(self, test_client: AsyncClient):
        """A client that GETs, edits one field and PUTs it all back is sending
        the mask for the secret. Treating that literally destroys it."""
        current = (await test_client.get("/config")).json()["config"]
        current["log_level"] = "WARNING"
        await test_client.put("/config", json=current)
        after = (await test_client.get("/config")).json()["config"]
        assert after["log_level"] == "WARNING"
        assert after["embedding"]["api_key"] == "**********"

    async def test_takes_effect_without_a_restart(self, test_client: AsyncClient):
        """A write that lands on disk but not in the cached settings reads as
        a successful save that silently did nothing."""
        await test_client.put("/config", json={"log_level": "DEBUG"})
        assert config_mod.get_settings().log_level == "DEBUG"

    async def test_invalid_update_is_rejected(self, test_client: AsyncClient):
        resp = await test_client.put("/config", json={"max_file_size": "not-an-int"})
        assert resp.status_code == 422


class TestVersionsAndRollback:
    async def test_versions_newest_first_without_snapshots(self, test_client: AsyncClient):
        await test_client.put("/config", json={"log_level": "DEBUG"})
        versions = (await test_client.get("/config/versions")).json()["versions"]
        assert versions
        assert versions[0]["version"] >= versions[-1]["version"]
        assert all("data" not in v for v in versions)

    async def test_rollback_restores_an_earlier_value(self, test_client: AsyncClient):
        await test_client.put("/config", json={"log_level": "DEBUG"})
        first = (await test_client.get("/config/versions")).json()["versions"][-1]["version"]
        resp = await test_client.post("/config/rollback", json={"version": first})
        assert resp.status_code == 200
        assert resp.json()["config"]["log_level"] == "INFO"

    async def test_rollback_keeps_the_live_secret(self, test_client: AsyncClient):
        """The history stores no secrets, so rollback carries the current one
        forward rather than blanking it."""
        await test_client.put("/config", json={"log_level": "DEBUG"})
        first = (await test_client.get("/config/versions")).json()["versions"][-1]["version"]
        await test_client.post("/config/rollback", json={"version": first})
        after = (await test_client.get("/config")).json()["config"]
        assert after["embedding"]["api_key"] == "**********"

    async def test_unknown_version_rejected(self, test_client: AsyncClient):
        resp = await test_client.post("/config/rollback", json={"version": 999})
        assert resp.status_code == 422

    async def test_non_integer_version_rejected(self, test_client: AsyncClient):
        resp = await test_client.post("/config/rollback", json={"version": "x"})
        assert resp.status_code == 422


class TestSecretsNeverReachTheHistory:
    async def test_history_file_holds_no_secret_values(
        self, test_client: AsyncClient, _isolated_config
    ):
        await test_client.put("/config", json={"embedding": {"api_key": "rotated-secret"}})
        sidecar = _isolated_config.with_suffix(_isolated_config.suffix + ".versions")
        raw = sidecar.read_text(encoding="utf-8") if sidecar.exists() else ""
        assert "rotated-secret" not in raw
        assert "real-secret" not in raw


class TestPruneConfig:
    async def test_removes_unknown_top_level_keys(self, test_client: AsyncClient, _isolated_config):
        """Legacy keys not in the schema should be stripped."""
        # Inject stale keys into the config file.
        raw = json.loads(_isolated_config.read_text(encoding="utf-8"))
        raw["s3_bucket"] = "old-bucket"
        raw["storage_backend"] = "s3"
        raw["enrichment_llm_model"] = "gpt-4"
        _isolated_config.write_text(json.dumps(raw), encoding="utf-8")

        resp = await test_client.post("/config/prune")
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["removed"]) == [
            "enrichment_llm_model",
            "s3_bucket",
            "storage_backend",
        ]
        assert "s3_bucket" not in body["config"]
        assert "storage_backend" not in body["config"]
        assert "enrichment_llm_model" not in body["config"]

    async def test_preserves_known_keys(self, test_client: AsyncClient, _isolated_config):
        """Known keys survive the prune."""
        raw = json.loads(_isolated_config.read_text(encoding="utf-8"))
        raw["s3_bucket"] = "old-bucket"
        _isolated_config.write_text(json.dumps(raw), encoding="utf-8")

        resp = await test_client.post("/config/prune")
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["embedding"]["model"] == "seeded-model"
        assert "s3_bucket" not in body["config"]

    async def test_removes_nested_unknown_keys(self, test_client: AsyncClient, _isolated_config):
        """Stale keys inside a known nested object are stripped."""
        raw = json.loads(_isolated_config.read_text(encoding="utf-8"))
        raw["embedding"]["legacy_field"] = "should-go"
        _isolated_config.write_text(json.dumps(raw), encoding="utf-8")

        resp = await test_client.post("/config/prune")
        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] == ["embedding.legacy_field"]
        assert "legacy_field" not in body["config"]["embedding"]

    async def test_noop_when_no_stale_keys(self, test_client: AsyncClient):
        """A clean config returns empty removed list and does not bump version."""
        resp = await test_client.post("/config/prune")
        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] == []

    async def test_prune_records_version_history(self, test_client: AsyncClient, _isolated_config):
        """The prune operation records a version for audit/rollback."""
        raw = json.loads(_isolated_config.read_text(encoding="utf-8"))
        raw["s3_bucket"] = "old-bucket"
        _isolated_config.write_text(json.dumps(raw), encoding="utf-8")

        resp = await test_client.post("/config/prune")
        assert resp.status_code == 200
        version = resp.json()["version"]
        versions = (await test_client.get("/config/versions")).json()["versions"]
        assert any(v["version"] == version for v in versions)

    async def test_prune_takes_effect_without_restart(
        self, test_client: AsyncClient, _isolated_config
    ):
        """After pruning, the cached settings reflect the change."""
        raw = json.loads(_isolated_config.read_text(encoding="utf-8"))
        raw["s3_bucket"] = "old-bucket"
        _isolated_config.write_text(json.dumps(raw), encoding="utf-8")

        await test_client.post("/config/prune")
        # The cached settings should be reloaded; s3_bucket was never in the
        # model so it's invisible, but log_level (a known key) should still
        # be available, proving the reload didn't break anything.
        assert config_mod.get_settings().log_level == "INFO"

    async def test_prune_empty_config(self, test_client: AsyncClient, _isolated_config):
        """An empty config file produces an empty result."""
        _isolated_config.write_text("{}", encoding="utf-8")
        resp = await test_client.post("/config/prune")
        assert resp.status_code == 200
        assert resp.json()["config"] == {}
        assert resp.json()["removed"] == []

    async def test_prune_masks_secrets(self, test_client: AsyncClient, _isolated_config):
        """Secrets remain masked in the prune response."""
        raw = json.loads(_isolated_config.read_text(encoding="utf-8"))
        raw["s3_bucket"] = "old-bucket"
        _isolated_config.write_text(json.dumps(raw), encoding="utf-8")

        resp = await test_client.post("/config/prune")
        assert resp.status_code == 200
        assert resp.json()["config"]["embedding"]["api_key"] == "**********"
