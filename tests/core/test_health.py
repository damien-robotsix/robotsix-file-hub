"""Tests for the health check endpoint."""

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from src.robotsix_file_hub.storage import StorageError


async def test_health_returns_ok(test_client: AsyncClient) -> None:
    """GET /health returns 200 with status ok and sub-component statuses."""
    response = await test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "db" in data
    assert "storage" in data


async def test_health_content_type(test_client: AsyncClient) -> None:
    """GET /health returns JSON content type."""
    response = await test_client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


async def test_health_endpoint_accessible_without_auth(test_client: AsyncClient) -> None:
    """GET /health does not require authentication."""
    response = await test_client.get("/health")
    assert response.status_code == 200


async def test_health_live_returns_ok(test_client: AsyncClient) -> None:
    """GET /health/live returns 200 with status ok and no dependency checks."""
    response = await test_client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok"}


async def test_health_db_failure_returns_degraded(test_client: AsyncClient) -> None:
    """GET /health returns db=error and status=degraded when the database is down."""
    mock_engine = AsyncMock()
    mock_engine.connect.side_effect = Exception("db down")

    with patch("src.robotsix_file_hub.main.engine", mock_engine):
        response = await test_client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["db"] == "error"
    assert data["storage"] == "ok"
    assert data["status"] == "degraded"


async def test_health_storage_failure_returns_degraded(test_client: AsyncClient) -> None:
    """GET /health returns storage=error and status=degraded when storage is down."""
    import src.robotsix_file_hub.main as main_module

    mock_storage = AsyncMock()
    mock_storage.save.side_effect = StorageError("storage down")

    with patch.object(main_module, "create_storage_backend", return_value=mock_storage):
        response = await test_client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["db"] == "ok"
    assert data["storage"] == "error"
    assert data["status"] == "degraded"
