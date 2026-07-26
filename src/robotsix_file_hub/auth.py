"""Authentication dependency for bearer-token protected endpoints."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    settings: Annotated[Settings, Depends(Settings)],
) -> None:
    """Dependency that enforces bearer-token auth when ``auth_token`` is set.

    When ``auth_token`` is empty auth is disabled — all requests pass.
    Otherwise the request MUST include an ``Authorization: Bearer <token>``
    header matching the configured token.
    """
    if not settings.auth_token:
        return

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.credentials != settings.auth_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bearer token",
        )
