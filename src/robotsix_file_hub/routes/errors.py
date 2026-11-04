"""Shared helpers for building safe HTTP error responses."""

import logging

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


def internal_error(message: str, exc: BaseException) -> HTTPException:
    """Log *exc* server-side and return a generic ``500`` for the client.

    The raw exception text (which may contain SQL, hostnames, paths or
    credentials) is recorded in the server log with its traceback but is
    never included in the response body — the client only sees
    *message*.
    """
    logger.error("%s", message, exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=message,
    )
