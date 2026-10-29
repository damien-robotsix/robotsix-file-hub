"""Tests for the health / probe endpoints (robotsix-http ``create_health_router``)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient

import src.robotsix_file_hub.main as main_module
from src.robotsix_file_hub.storage import StorageError


async def test_health_returns_ok(test_client: AsyncClient) -> None:
    """GET /health returns 200 with status ok."""
    response = await test_client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


async def test_health_live_returns_ok(test_client: AsyncClient) -> None:
    """GET /health/live returns 200 with status ok and no dependency checks."""
    response = await test_client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_returns_ok(test_client: AsyncClient) -> None:
    """GET /health/ready returns 200 when DB and storage are reachable."""
    response = await test_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_db_failure(test_client: AsyncClient) -> None:
    """GET /health/ready returns 503 naming ``db`` when the database is down."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = Exception("db down")

    with patch.object(main_module, "engine", mock_engine):
        response = await test_client.get("/health/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unready"
    assert data["failures"] == ["db: db down"]
    mock_engine.connect.assert_called_once()


async def test_health_ready_storage_failure(test_client: AsyncClient) -> None:
    """GET /health/ready returns 503 naming ``storage`` when storage is down."""
    mock_storage = AsyncMock()
    mock_storage.save.side_effect = StorageError("storage down")

    with patch.object(main_module, "create_storage_backend", return_value=mock_storage):
        response = await test_client.get("/health/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unready"
    assert data["failures"] == ["storage: storage down"]


async def test_health_ready_non_storage_error_reported(test_client: AsyncClient) -> None:
    """Any backend exception (not only StorageError) is reported, not a 500."""
    mock_storage = AsyncMock()
    mock_storage.save.side_effect = OSError("disk gone")

    with patch.object(main_module, "create_storage_backend", return_value=mock_storage):
        response = await test_client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["failures"] == ["storage: disk gone"]


async def test_health_ready_check_is_bounded_by_timeout(test_client: AsyncClient) -> None:
    """A hung dependency is reported as timed out instead of hanging the probe."""

    async def hang(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(3600)

    mock_storage = AsyncMock()
    mock_storage.save.side_effect = hang

    with patch.object(main_module, "create_storage_backend", return_value=mock_storage):
        response = await asyncio.wait_for(test_client.get("/health/ready"), timeout=10)

    assert response.status_code == 503
    assert response.json()["failures"] == ["storage: timed out after 2.0s"]


def test_no_hand_rolled_probe_routes_in_main() -> None:
    """Probe routes come from the shared router, not hand-rolled handlers."""
    assert not hasattr(main_module, "health")
    assert not hasattr(main_module, "health_live")
