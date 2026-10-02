"""Tests for the shared robotsix-http FastAPI exception handlers.

``main.py`` installs the fleet handlers (validation / domain /
external_http_error / unhandled) so pydantic validation errors and
unexpected exceptions render the canonical ``{"error": {"code", "detail"}}``
envelope.  FastAPI's default ``HTTPException`` handler is intentionally kept,
so route-level ``HTTPException`` responses (404 / 400) keep the top-level
``{"detail": ...}`` shape the rest of the suite relies on.  These tests
verify the fleet handlers are wired onto the app and that a pydantic
validation failure renders the fleet envelope.
"""

from fastapi.exceptions import RequestValidationError
from httpx import AsyncClient
from robotsix_http.client import ExternalHTTPError
from robotsix_http.fastapi import (
    DomainError,
    domain_error_handler,
    external_http_error_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.robotsix_file_hub.main import app


def test_fleet_exception_handlers_are_registered() -> None:
    """The fleet handlers must be wired onto the app with their fleet impls."""
    handlers = app.exception_handlers
    assert handlers.get(RequestValidationError) is validation_exception_handler
    assert handlers.get(DomainError) is domain_error_handler
    assert handlers.get(ExternalHTTPError) is external_http_error_handler
    assert handlers.get(Exception) is unhandled_exception_handler


def test_http_exception_keeps_fastapi_default_handler() -> None:
    """HTTPException keeps FastAPI's default handler (top-level ``detail``)."""
    # The fleet ``http_exception_handler`` is deliberately NOT installed, so the
    # registered handler must not be one of the fleet handlers.
    handler = app.exception_handlers.get(StarletteHTTPException)
    assert handler is not None
    assert handler not in (
        validation_exception_handler,
        domain_error_handler,
        external_http_error_handler,
        unhandled_exception_handler,
    )


async def test_validation_error_uses_fleet_envelope(test_client: AsyncClient) -> None:
    """A pydantic validation failure returns 422 in the fleet envelope."""
    # POST /search with a body missing the required ``query`` field triggers a
    # RequestValidationError before the endpoint body runs.
    response = await test_client.post("/search", json={})
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["detail"], list)
    assert body["error"]["detail"], "validation detail should not be empty"
