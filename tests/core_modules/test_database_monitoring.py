"""Query-performance monitoring listeners.

Covers the behaviour added for the ``db_query_performance_monitoring``
survey gap: slow queries are logged at WARNING with structured fields,
fast queries are not, and the connection pool warns as it nears
exhaustion.
"""

import logging
import os
import tempfile
from collections.abc import AsyncGenerator, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.robotsix_file_hub import database_monitoring as dbm
from src.robotsix_file_hub.database_monitoring import (
    _check_pool,
    _operation,
    _table_name,
    register_query_monitoring,
)


class _RecordCollector(logging.Handler):
    """Logging handler that keeps every record it receives."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def monitoring_records() -> Iterator[list[logging.LogRecord]]:
    """Capture records emitted by the monitoring module's own logger.

    A handler is attached directly to the module logger so capture does
    not depend on log propagation (the app logger is configured with a
    scoped handler by robotsix-llmio at import time).
    """
    handler = _RecordCollector()
    dbm.logger.addHandler(handler)
    original_level = dbm.logger.level
    dbm.logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        dbm.logger.removeHandler(handler)
        dbm.logger.setLevel(original_level)


@pytest.fixture
async def file_engine() -> AsyncGenerator[AsyncEngine]:
    """On-disk SQLite async engine (so a real queue pool is used)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "monitoring.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            yield engine
        finally:
            await engine.dispose()


def _warnings(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.levelno == logging.WARNING]


async def test_slow_query_logged_as_warning(
    file_engine: AsyncEngine, monitoring_records: list[logging.LogRecord]
) -> None:
    # threshold_ms=0 makes every query count as slow.
    register_query_monitoring(file_engine, threshold_ms=0.0)

    async with file_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    slow = [r for r in _warnings(monitoring_records) if "slow database query" in r.getMessage()]
    assert slow, "a slow query should produce a WARNING record"
    record = slow[0]
    assert hasattr(record, "duration_ms")
    assert record.operation == "SELECT"
    assert hasattr(record, "query_hash")
    assert record.threshold_ms == 0.0


async def test_fast_query_not_logged_at_warning(
    file_engine: AsyncEngine, monitoring_records: list[logging.LogRecord]
) -> None:
    # An enormous threshold means no real query ever counts as slow.
    register_query_monitoring(file_engine, threshold_ms=10_000_000.0)

    async with file_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    assert not [
        r for r in _warnings(monitoring_records) if "slow database query" in r.getMessage()
    ], "a fast query must not be logged at WARNING level"


async def test_pool_exhaustion_warns_when_utilization_high(
    monitoring_records: list[logging.LogRecord],
) -> None:
    class _FakePool:
        def size(self) -> int:
            return 10

        def checkedout(self) -> int:
            return 9  # 90% > 80% warn fraction

        def overflow(self) -> int:
            return 0

    _check_pool(_FakePool(), 0.8)

    pool_warnings = [
        r for r in _warnings(monitoring_records) if "pool near exhaustion" in r.getMessage()
    ]
    assert pool_warnings, "high pool utilization should warn"
    record = pool_warnings[0]
    assert record.checked_out == 9
    assert record.pool_size == 10
    assert record.utilization == 0.9


async def test_pool_not_exhausted_does_not_warn(
    monitoring_records: list[logging.LogRecord],
) -> None:
    class _FakePool:
        def size(self) -> int:
            return 10

        def checkedout(self) -> int:
            return 3  # 30% < 80% warn fraction

        def overflow(self) -> int:
            return 0

    _check_pool(_FakePool(), 0.8)

    assert not [
        r for r in _warnings(monitoring_records) if "pool near exhaustion" in r.getMessage()
    ], "a healthy pool must not warn"


async def test_pool_checkout_listener_fires_on_real_engine(
    monitoring_records: list[logging.LogRecord],
) -> None:
    # pool_size=1 / max_overflow=0 means a single checkout is 100% utilized.
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "pool.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", pool_size=1, max_overflow=0)
        register_query_monitoring(engine, threshold_ms=10_000_000.0)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    assert [r for r in _warnings(monitoring_records) if "pool near exhaustion" in r.getMessage()], (
        "checking out the only connection should trip the pool warning"
    )


def test_statement_parsing_helpers() -> None:
    assert _operation("  select * from t") == "SELECT"
    assert _operation("INSERT INTO file_records VALUES (1)") == "INSERT"
    assert _table_name("SELECT id FROM file_records WHERE id = 1") == "file_records"
    assert _table_name("INSERT INTO jobs (a) VALUES (1)") == "jobs"
    assert _table_name("UPDATE file_records SET a = 1") == "file_records"
