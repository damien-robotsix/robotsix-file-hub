"""FastAPI application for robotsix-file-hub."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pythonjsonlogger.json import JsonFormatter
from sqlalchemy import text

from .config import get_settings
from .database import engine, init_db
from .routes.config import router as config_router
from .routes.files import router as files_router
from .routes.search import router as search_router
from .routes.tasks import router as tasks_router
from .storage import StorageError, create_storage_backend
from .tasks import start_workers, stop_workers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown lifecycle: create DB schema, start workers, stop workers."""
    await init_db()
    await start_workers()
    try:
        yield
    finally:
        await stop_workers()


settings = get_settings()

# Configure logging: UTC ISO-8601 timestamps to stdout, structured JSON
logging.Formatter.converter = time.gmtime
handler = logging.StreamHandler()
handler.setFormatter(
    JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        timestamp=True,
    )
)
logging.basicConfig(
    level=settings.log_level,
    handlers=[handler],
)

app = FastAPI(
    title="robotsix-file-hub",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(files_router)
app.include_router(search_router)
app.include_router(tasks_router)
app.include_router(config_router)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
async def health() -> dict[str, str]:
    """Report service liveness by probing the database and storage backend.

    Returns:
        dict with keys ``status``, ``db``, and ``storage`` reflecting
        reachability of each dependency.
    """
    db_status = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    storage_status = "ok"
    try:
        storage = create_storage_backend()
        await storage.save("__health_check__", b"health-check")
        await storage.delete("__health_check__")
    except StorageError:
        storage_status = "error"

    return {
        "status": "ok" if db_status == "ok" and storage_status == "ok" else "degraded",
        "db": db_status,
        "storage": storage_status,
    }


_DEPLOY_SPEC_PATH = Path("deploy/docker-compose.yml")
_UI_STATIC_DIR = Path("static")


@app.get("/deploy-spec")
async def deploy_spec() -> Response:
    spec_content = _DEPLOY_SPEC_PATH.read_text()
    return Response(
        content=spec_content,
        media_type="application/x-yaml",
        headers={"central-deploy-contract-version": "1"},
    )


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_ui(full_path: str) -> FileResponse:
    """Serve the built SPA, falling back to index.html for client-side routes.

    The frontend is a React SPA with client-side routes (``/files``,
    ``/search``, ``/upload``, ...).  A browser refresh on one of those
    paths must serve the SPA shell rather than a 404, so any unknown
    path is answered with ``index.html``.  API routes are unaffected:
    they are registered before this catch-all and therefore win.

    When the UI static directory does not exist (e.g. local development
    with only the Vite dev server) the endpoint returns a 404, keeping
    the same behaviour as before the SPA mount was added.
    """
    static_dir = _UI_STATIC_DIR.resolve()
    requested = (static_dir / full_path).resolve()
    if requested.is_file() and requested.is_relative_to(static_dir):
        return FileResponse(requested)
    index = static_dir / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Not Found")
