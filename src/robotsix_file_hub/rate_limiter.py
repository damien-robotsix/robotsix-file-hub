"""slowapi-based IP rate limiting for the file-hub FastAPI app.

A single :class:`slowapi.Limiter` instance is shared by every route
handler via the ``@limiter.limit(...)`` decorator.  All endpoints use
one fleet-safe uniform default of ``60/minute`` per client IP
(``get_remote_address``); the original PR that would have introduced
per-endpoint values is no longer recoverable, so the uniform default
is applied everywhere.

When a client exceeds the limit the middleware raises
:class:`slowapi.errors.RateLimitExceeded`; the handler registered on
the app returns an RFC 9457 ``application/problem+json`` envelope so
429s match every other error response emitted by the service.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

DEFAULT_RATE_LIMIT = "60/minute"

limiter = Limiter(key_func=get_remote_address)
