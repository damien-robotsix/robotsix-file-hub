"""FastAPI application for robotsix-file-hub."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pythonjsonlogger.json import JsonFormatter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIASGIMiddleware
from sqlalchemy import text

from .config import get_settings
from .database import engine, init_db
from .rate_limiter import DEFAULT_RATE_LIMIT, limiter
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

# Rate limiting: IP-based (get_remote_address), enforced by the slowapi
# ASGI middleware, which reads the Limiter from ``app.state.limiter``.
app.state.limiter = limiter
app.add_middleware(SlowAPIASGIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return RFC 9457 ``application/problem+json`` for 429 responses.

    Matches the error envelope used for every other error in the
    service, so clients get a uniform problem-detail body.
    """
    return JSONResponse(
        status_code=429,
        content={
            "type": "about:blank",
            "title": "Too Many Requests",
            "status": 429,
            "detail": str(exc.detail),
            "instance": str(request.url),
        },
        headers={"Content-Type": "application/problem+json"},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return 400 for malformed request bodies/params.

    FastAPI's default for schema-validation failures is 422; this service
    treats an invalid request shape as a client error and reports it with
    the same ``{"detail": ...}`` envelope as the other error responses.
    """
    try:
        first = exc.errors()[0]
        detail = f"{'.'.join(str(loc) for loc in first['loc'])}: {first['msg']}"
    except IndexError, KeyError, TypeError:
        detail = "Invalid request"
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": detail},
    )


app.include_router(files_router)
app.include_router(search_router)
app.include_router(tasks_router)
app.include_router(config_router)


@app.get("/health/live")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def health_live(request: Request) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def health(request: Request) -> dict[str, str]:
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
@limiter.limit(DEFAULT_RATE_LIMIT)
async def deploy_spec(request: Request) -> Response:
    spec_content = _DEPLOY_SPEC_PATH.read_text()
    return Response(
        content=spec_content,
        media_type="application/x-yaml",
        headers={"central-deploy-contract-version": "1"},
    )


@app.get("/{full_path:path}", include_in_schema=False)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def serve_ui(request: Request, full_path: str) -> FileResponse:
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
