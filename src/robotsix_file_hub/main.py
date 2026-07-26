"""Minimal FastAPI application stub for robotsix-file-hub."""

from fastapi import FastAPI

app = FastAPI(title="robotsix-file-hub", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
