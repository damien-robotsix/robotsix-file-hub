"""Tests for the slowapi rate limiting layer.

Verifies that requests within the configured limit return normally
and that a burst beyond the limit receives a standards-conforming
RFC 9457 ``application/problem+json`` 429 response.
"""

from httpx import AsyncClient

# Matches src/robotsix_file_hub/rate_limiter.py — keep in sync.
DEFAULT_LIMIT_HITS = 60


async def test_request_within_limit_returns_200(test_client: AsyncClient) -> None:
    """A small number of requests to any rate-limited endpoint must succeed.

    ``/deploy-spec`` is used because the probe routes (``/health*``) come
    from the shared robotsix-http router and are deliberately not
    rate-limited, so orchestrator probes are never answered with 429.
    """
    for _ in range(5):
        response = await test_client.get("/deploy-spec")
        assert response.status_code == 200


async def test_burst_beyond_limit_returns_429_with_problem_json(
    test_client: AsyncClient,
) -> None:
    """Exceeding the rate limit returns HTTP 429 with RFC 9457 envelope."""
    # Fire DEFAULT_LIMIT_HITS requests (the default limit). All must succeed.
    for _ in range(DEFAULT_LIMIT_HITS):
        response = await test_client.get("/deploy-spec")
        assert response.status_code == 200, "request within limit should not be rejected"

    # The next request must be rate-limited.
    response = await test_client.get("/deploy-spec")
    assert response.status_code == 429

    # Verify RFC 9457 Content-Type and envelope fields.
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 429
    assert body["title"] == "Too Many Requests"
    assert body["type"] == "about:blank"
    assert body["detail"]
    assert body["instance"]
