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
                "s3_bucket": "seeded-bucket",
                "auth_token": "",
                "s3_secret_key": "real-secret",
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
        """s3_secret_key is a SecretStr; it must never leave the process."""
        body = (await test_client.get("/config")).json()
        assert body["config"]["s3_secret_key"] == "**********"

    async def test_non_secrets_are_intact(self, test_client: AsyncClient):
        body = (await test_client.get("/config")).json()
        assert body["config"]["s3_bucket"] == "seeded-bucket"


class TestPutConfig:
    async def test_partial_update_keeps_other_keys(self, test_client: AsyncClient):
        resp = await test_client.put("/config", json={"log_level": "DEBUG"})
        assert resp.status_code == 200
        cfg = resp.json()["config"]
        assert cfg["log_level"] == "DEBUG"
        assert cfg["s3_bucket"] == "seeded-bucket"

    async def test_echoed_mask_keeps_the_secret(self, test_client: AsyncClient):
        """A client that GETs, edits one field and PUTs it all back is sending
        the mask for the secret. Treating that literally destroys it."""
        current = (await test_client.get("/config")).json()["config"]
        current["log_level"] = "WARNING"
        await test_client.put("/config", json=current)
        after = (await test_client.get("/config")).json()["config"]
        assert after["log_level"] == "WARNING"
        assert after["s3_secret_key"] == "**********"

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
        assert after["s3_secret_key"] == "**********"

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
        await test_client.put("/config", json={"s3_secret_key": "rotated-secret"})
        sidecar = _isolated_config.with_suffix(_isolated_config.suffix + ".versions")
        raw = sidecar.read_text(encoding="utf-8") if sidecar.exists() else ""
        assert "rotated-secret" not in raw
        assert "real-secret" not in raw
