"""SQLAlchemy query-performance monitoring via engine event listeners.

Attaches to the async engine's underlying ``sync_engine`` to time every
cursor execution, log slow queries as structured WARNINGs, and warn when
the connection pool nears exhaustion.  Everything routes through the
standard-library :mod:`logging` so output flows through the shared
robotsix-llmio JSON logger configured in ``main.py``.

There is zero visibility otherwise into whether queries are slow or the
pool is nearing exhaustion — performance regressions stay invisible until
a production incident.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import Pool

logger = logging.getLogger(__name__)

# Key under which per-connection query start timestamps are stacked on
# ``connection.info``.  A list is used because a single connection can have
# nested cursor executions (e.g. server-side cursors).
_QUERY_START_KEY = "robotsix_query_start_times"

# Warn when the pool is checked out beyond this fraction of ``pool_size``.
_DEFAULT_POOL_WARN_FRACTION = 0.8


def _operation(statement: str) -> str:
    """Return the SQL operation keyword (``SELECT``/``INSERT``/...)."""
    stripped = statement.lstrip()
    if not stripped:
        return "UNKNOWN"
    return stripped.split(None, 1)[0].upper()


def _table_name(statement: str) -> str:
    """Best-effort extraction of the primary table a statement touches."""
    tokens = statement.replace("\n", " ").split()
    upper = [tok.upper() for tok in tokens]
    for keyword in ("FROM", "INTO", "UPDATE", "TABLE"):
        if keyword in upper:
            idx = upper.index(keyword)
            if idx + 1 < len(tokens):
                return tokens[idx + 1].strip('"`').split("(")[0]
    return "unknown"


def _query_hash(statement: str) -> str:
    """Stable short hash of the statement text for grouping in logs."""
    digest = hashlib.sha256(statement.encode("utf-8", "replace")).hexdigest()
    return digest[:12]


def _log_query(statement: str, duration_ms: float, threshold_ms: float) -> None:
    """Emit a DEBUG line for every query and a WARNING for slow ones."""
    fields: dict[str, Any] = {
        "duration_ms": round(duration_ms, 2),
        "query_hash": _query_hash(statement),
        "table": _table_name(statement),
        "operation": _operation(statement),
    }
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("database query executed", extra=fields)
    if duration_ms >= threshold_ms:
        logger.warning(
            "slow database query (%.2f ms >= %.0f ms threshold)",
            duration_ms,
            threshold_ms,
            extra={**fields, "threshold_ms": threshold_ms},
        )


def _check_pool(pool: Pool, warn_fraction: float) -> None:
    """Warn when pool utilization exceeds ``warn_fraction`` of ``pool_size``."""
    size_fn = getattr(pool, "size", None)
    checkedout_fn = getattr(pool, "checkedout", None)
    if not callable(size_fn) or not callable(checkedout_fn):
        return
    pool_size = size_fn()
    if pool_size <= 0:
        return
    checked_out = checkedout_fn()
    if checked_out > pool_size * warn_fraction:
        overflow_fn = getattr(pool, "overflow", None)
        overflow = overflow_fn() if callable(overflow_fn) else 0
        logger.warning(
            "database connection pool near exhaustion (%d/%d checked out, %d overflow)",
            checked_out,
            pool_size,
            overflow,
            extra={
                "checked_out": checked_out,
                "pool_size": pool_size,
                "overflow": overflow,
                "utilization": round(checked_out / pool_size, 2),
            },
        )


def register_query_monitoring(
    engine: AsyncEngine | Engine,
    threshold_ms: float = 500.0,
    pool_warn_fraction: float = _DEFAULT_POOL_WARN_FRACTION,
) -> None:
    """Attach query-timing and pool-exhaustion listeners to ``engine``.

    Works on both async and sync engines: event listeners always bind to
    the underlying synchronous :class:`~sqlalchemy.engine.Engine` (an
    :class:`~sqlalchemy.ext.asyncio.AsyncEngine` exposes it as
    ``sync_engine``), which is where SQLAlchemy dispatches cursor and pool
    events.

    :param threshold_ms: queries taking at least this many milliseconds are
        logged at WARNING level with structured fields.
    :param pool_warn_fraction: warn on connection checkout once more than
        this fraction of ``pool_size`` connections are in use.
    """
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        conn.info.setdefault(_QUERY_START_KEY, []).append(time.perf_counter())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        start_times = conn.info.get(_QUERY_START_KEY)
        if not start_times:
            return
        duration_ms = (time.perf_counter() - start_times.pop()) * 1000.0
        _log_query(statement, duration_ms, threshold_ms)

    @event.listens_for(sync_engine.pool, "checkout")
    def _on_checkout(
        dbapi_connection: Any,
        connection_record: Any,
        connection_proxy: Any,
    ) -> None:
        _check_pool(sync_engine.pool, pool_warn_fraction)
