"""Tests for the deploy-spec endpoint."""

from pathlib import Path

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


def test_runtime_image_ships_the_deploy_directory() -> None:
    """The Dockerfile runtime stage copies `deploy/` into the image.

    The endpoint reads `deploy/docker-compose.yml` relative to WORKDIR, so a
    runtime stage that omits the directory returns 500 in the container. The
    tests above cannot catch that: they run from the repo root, where the file
    exists on disk regardless of what the image contains.
    """
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    _, _, runtime_stage = dockerfile.partition("AS runtime")
    assert runtime_stage, "no `AS runtime` stage found in Dockerfile"

    copies_deploy = any(
        line.strip().startswith("COPY") and "deploy/" in line for line in runtime_stage.splitlines()
    )
    assert copies_deploy, (
        "Dockerfile runtime stage must COPY deploy/ into the image, "
        "otherwise GET /deploy-spec raises FileNotFoundError at runtime"
    )
