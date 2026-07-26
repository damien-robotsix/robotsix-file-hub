"""FastAPI application for robotsix-file-hub."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .database import engine
from .models import Base
from .routes.files import router as files_router
from .tasks import start_workers, stop_workers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await start_workers()
    try:
        yield
    finally:
        await stop_workers()


settings = Settings()

app = FastAPI(
    title="robotsix-file-hub",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(files_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
