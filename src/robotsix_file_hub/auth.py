"""Authentication dependencies for bearer-token / API-key protected endpoints."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings

_bearer_scheme = HTTPBearer(auto_error=False)


def _validate_token(
    credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
    settings: Settings,
) -> str:
    """Extract and validate a bearer token or API key against the configured token.

    Returns the validated token string, or raises 401 Unauthorized.
    """
    token: str | None = None
    if credentials is not None:
        token = credentials.credentials
    elif x_api_key is not None:
        token = x_api_key

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token != settings.auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return token


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    settings: Annotated[Settings, Depends(Settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> str:
    """Dependency that extracts and validates a bearer token or API key.

    Returns the validated token as the current user identity.
    Raises 401 Unauthorized on missing or invalid tokens.

    When ``auth_token`` is empty auth is disabled — returns ``"anonymous"``.
    """
    if not settings.auth_token:
        return "anonymous"
    return _validate_token(credentials, x_api_key, settings)
