"""Tests for the deploy-spec endpoint."""

from httpx import AsyncClient


async def test_deploy_spec_returns_contract_version_header(
    test_client: AsyncClient,
) -> None:
    """GET /deploy-spec returns the central-deploy-contract-version header."""
    response = await test_client.get("/deploy-spec")
    assert response.status_code == 200
    assert response.headers["central-deploy-contract-version"] == "1"


async def test_deploy_spec_returns_yaml(test_client: AsyncClient) -> None:
    """GET /deploy-spec returns YAML content."""
    response = await test_client.get("/deploy-spec")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-yaml")
    assert "services:" in response.text
    assert "file-hub:" in response.text


async def test_deploy_spec_no_auth_required(test_client: AsyncClient) -> None:
    """GET /deploy-spec does not require authentication."""
    response = await test_client.get("/deploy-spec")
    assert response.status_code == 200
