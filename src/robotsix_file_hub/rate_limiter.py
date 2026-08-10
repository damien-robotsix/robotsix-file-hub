"""Rate-limiting configuration — one :class:`slowapi.Limiter` shared across the app.

The limiter is created at module level so route modules can import it for
``@limiter.limit(...)`` decorators without causing a circular import with
``main.py``.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import get_settings


def _rate_limit_key(request: Request) -> str:
    """Return a per-client key for rate-limit bucketing.

    Keys by client IP address.  When per-user authentication is added
    the key function can be extended to key by authenticated user ID
    when an ``Authorization`` header is present, falling back to IP.
    """
    return get_remote_address(request)


_settings = get_settings()

limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[_settings.rate_limit_default],
    storage_uri=_settings.rate_limit_storage_uri or None,
)
