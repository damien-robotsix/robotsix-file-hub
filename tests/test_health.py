"""Tests for the health check endpoint."""

from httpx import AsyncClient


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
