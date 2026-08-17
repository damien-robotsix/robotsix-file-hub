"""Tests for the SPA UI serving at the root path."""

from pathlib import Path

import pytest
from httpx import AsyncClient

import src.robotsix_file_hub.main as main_module


@pytest.fixture
def ui_static_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the app's UI static directory at a temp dir with a fake build."""
    (tmp_path / "index.html").write_text("<!doctype html><title>File Hub</title>")
    (tmp_path / "vite.svg").write_text("<svg></svg>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('hi')")
    monkeypatch.setattr(main_module, "_UI_STATIC_DIR", tmp_path)
    return tmp_path


async def test_root_serves_ui(test_client: AsyncClient, ui_static_dir: Path) -> None:
    """GET / returns the index.html when the UI directory is present."""
    response = await test_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "File Hub" in response.text


async def test_spa_client_route_falls_back_to_index(
    test_client: AsyncClient, ui_static_dir: Path
) -> None:
    """GET /upload (a client-side route) returns index.html for a full-page refresh."""
    response = await test_client.get("/upload")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "File Hub" in response.text


async def test_static_asset_served(
    test_client: AsyncClient, ui_static_dir: Path
) -> None:
    """GET /assets/app.js returns the asset file."""
    response = await test_client.get("/assets/app.js")
    assert response.status_code == 200
    assert "console.log" in response.text


async def test_svg_asset_served(
    test_client: AsyncClient, ui_static_dir: Path
) -> None:
    """GET /vite.svg returns the SVG asset."""
    response = await test_client.get("/vite.svg")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]


async def test_api_route_takes_precedence(
    test_client: AsyncClient, ui_static_dir: Path
) -> None:
    """A known API route still returns its normal response, not index.html."""
    response = await test_client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_no_ui_dir_returns_404_json(
    test_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET / returns a JSON 404 when the UI directory does not exist."""
    monkeypatch.setattr(main_module, "_UI_STATIC_DIR", tmp_path / "missing")
    response = await test_client.get("/")
    assert response.status_code == 404
    assert "detail" in response.json()