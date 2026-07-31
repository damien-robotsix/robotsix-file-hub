"""Tests for authentication dependencies (get_current_user)."""


async def test_get_current_user_returns_token_with_valid_bearer() -> None:
    """get_current_user returns the token string when a valid bearer token is supplied."""
    from fastapi.security import HTTPAuthorizationCredentials

    from src.robotsix_file_hub.auth import get_current_user
    from src.robotsix_file_hub.config import Settings

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="secret")
    settings = Settings(auth_token="secret")
    result = await get_current_user(credentials=creds, settings=settings, x_api_key=None)
    assert result == "secret"


async def test_get_current_user_returns_anonymous_when_auth_disabled() -> None:
    """get_current_user returns 'anonymous' when auth_token is empty."""
    from src.robotsix_file_hub.auth import get_current_user
    from src.robotsix_file_hub.config import Settings

    settings = Settings(auth_token="")
    result = await get_current_user(credentials=None, settings=settings, x_api_key=None)
    assert result == "anonymous"


async def test_get_current_user_raises_401_on_missing_token() -> None:
    """get_current_user raises 401 when auth_token is set but no token is provided."""
    import pytest
    from fastapi import HTTPException

    from src.robotsix_file_hub.auth import get_current_user
    from src.robotsix_file_hub.config import Settings

    settings = Settings(auth_token="secret")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None, settings=settings, x_api_key=None)
    assert exc_info.value.status_code == 401


async def test_get_current_user_raises_401_on_wrong_token() -> None:
    """get_current_user raises 401 when the supplied token does not match."""
    import pytest
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from src.robotsix_file_hub.auth import get_current_user
    from src.robotsix_file_hub.config import Settings

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
    settings = Settings(auth_token="secret")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=creds, settings=settings, x_api_key=None)
    assert exc_info.value.status_code == 401


async def test_get_current_user_with_api_key_header() -> None:
    """get_current_user accepts X-API-Key header as an alternative to Bearer."""
    from src.robotsix_file_hub.auth import get_current_user
    from src.robotsix_file_hub.config import Settings

    settings = Settings(auth_token="secret")
    result = await get_current_user(credentials=None, settings=settings, x_api_key="secret")
    assert result == "secret"


async def test_get_current_user_raises_401_on_wrong_api_key() -> None:
    """get_current_user raises 401 when X-API-Key header value does not match."""
    import pytest
    from fastapi import HTTPException

    from src.robotsix_file_hub.auth import get_current_user
    from src.robotsix_file_hub.config import Settings

    settings = Settings(auth_token="secret")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None, settings=settings, x_api_key="wrong")
    assert exc_info.value.status_code == 401
