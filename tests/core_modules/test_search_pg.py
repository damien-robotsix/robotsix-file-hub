"""Tests for search_files — the Python-based hybrid search function.

``search_files_pg`` falls back to ``search_files`` on non-PostgreSQL
backends (e.g. SQLite in tests), so these tests also provide coverage
for the fallback path of ``search_files_pg``.
"""

import os
import tempfile
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.robotsix_file_hub.models import Base, FileRecord


@pytest.fixture
async def _search_db_session() -> AsyncGenerator[AsyncSession]:
    """In-memory SQLite session for search_files orchestration tests.

    Creates a fresh database with the FileRecord table so tests can
    seed records and call ``search_files`` directly without relying
    on the application stack or ``conftest.py`` imports.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()


async def test_search_files_keyword_basic(
    _search_db_session: AsyncSession,
) -> None:
    """search_files returns keyword-ranked results when embeddings unavailable."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="budget_2024.xlsx",
                size=100,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                checksum="aa",
                storage_key="/tmp/b.xlsx",
                summary="Annual budget report",
                tags="budget,finance",
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="photo.png",
                size=200,
                content_type="image/png",
                checksum="bb",
                storage_key="/tmp/p.png",
                summary="Beach photo",
                tags="vacation",
                source="upload",
            ),
            FileRecord(
                id="f3",
                filename="budget_q1.pdf",
                size=150,
                content_type="application/pdf",
                checksum="cc",
                storage_key="/tmp/q.pdf",
                summary="Q1 budget review",
                tags="budget,quarterly",
                source="upload",
            ),
        ]
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(_search_db_session, "budget report")

    assert response.total == 2
    assert response.query == "budget report"
    assert response.offset == 0
    assert response.limit == 50
    assert len(response.results) == 2

    # f1: "budget"(filename+summary+tags=18) + "report"(summary=5) = 23/36 ≈ 0.6389
    # f3: "budget"(filename+summary+tags=18) + "report"(0) = 18/36 = 0.5
    assert response.results[0].id == "f1"
    assert response.results[0].relevance == pytest.approx(23.0 / 36.0, abs=1e-4)
    assert response.results[1].id == "f3"
    assert response.results[1].relevance == pytest.approx(18.0 / 36.0, abs=1e-4)


async def test_search_files_hybrid_ranking(
    _search_db_session: AsyncSession,
) -> None:
    """search_files uses hybrid scoring (vector_weight=0.7) when embeddings available."""
    from src.robotsix_file_hub.search import search_files

    file1_embedding = [1.0, 0.0, 0.0]  # distant from canned query
    file2_embedding = [0.0, 1.0, 0.0]  # close to canned query

    _search_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="vacation_beach_photo.png",
                size=100,
                content_type="image/png",
                checksum="aa",
                storage_key="/tmp/v.png",
                summary="Beach vacation photo from summer trip",
                tags="vacation,beach,summer,photo",
                embedding=file1_embedding,
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="report.pdf",
                size=200,
                content_type="application/pdf",
                checksum="bb",
                storage_key="/tmp/r.pdf",
                summary="Financial analysis",
                tags="finance",
                embedding=file2_embedding,
                source="upload",
            ),
        ]
    )
    await _search_db_session.commit()

    canned_query_embedding = [0.1, 0.9, 0.0]

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=canned_query_embedding),
    ):
        response = await search_files(_search_db_session, "financial report")

    assert response.total == 2
    # file2 ranks higher: its embedding is much closer to the query vector
    assert response.results[0].id == "f2"
    assert response.results[1].id == "f1"
    for r in response.results:
        assert 0.0 <= r.relevance <= 1.0


async def test_search_files_no_results(
    _search_db_session: AsyncSession,
) -> None:
    """search_files returns empty results when no files match the query."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add(
        FileRecord(
            id="f1",
            filename="photo.png",
            size=100,
            content_type="image/png",
            checksum="aa",
            storage_key="/tmp/p.png",
            summary="A nice photo",
            tags="photo",
            source="upload",
        )
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(_search_db_session, "budget spreadsheet")

    assert response.total == 0
    assert response.results == []
    assert response.query == "budget spreadsheet"


async def test_search_files_pagination(
    _search_db_session: AsyncSession,
) -> None:
    """search_files respects offset and limit parameters."""
    from src.robotsix_file_hub.search import search_files

    for i in range(5):
        _search_db_session.add(
            FileRecord(
                id=f"f{i}",
                filename=f"report_{i}.pdf",
                size=100,
                content_type="application/pdf",
                checksum=f"aa{i}",
                storage_key=f"/tmp/r{i}.pdf",
                summary=f"Report number {i}",
                tags="report",
                source="upload",
            )
        )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        p1 = await search_files(_search_db_session, "report", offset=0, limit=2)
        p2 = await search_files(_search_db_session, "report", offset=2, limit=2)
        p3 = await search_files(_search_db_session, "report", offset=4, limit=2)

    assert len(p1.results) == 2
    assert p1.total == 5
    assert p1.offset == 0
    assert p1.limit == 2

    assert len(p2.results) == 2
    assert p2.total == 5
    assert p2.offset == 2

    assert len(p3.results) == 1
    assert p3.total == 5
    assert p3.offset == 4


async def test_search_files_default_pagination(
    _search_db_session: AsyncSession,
) -> None:
    """search_files uses default offset=0, limit=50 when not specified."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add(
        FileRecord(
            id="f1",
            filename="doc.txt",
            size=100,
            content_type="text/plain",
            checksum="aa",
            storage_key="/tmp/doc.txt",
            summary="A document",
            tags="doc",
            source="upload",
        )
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(_search_db_session, "document")

    assert response.offset == 0
    assert response.limit == 50
    assert response.total == 1


async def test_search_files_keyword_fallback_none(
    _search_db_session: AsyncSession,
) -> None:
    """search_files falls back to keyword-only when embedding generation returns None."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add(
        FileRecord(
            id="f1",
            filename="budget.xlsx",
            size=100,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            checksum="aa",
            storage_key="/tmp/b.xlsx",
            summary="Budget spreadsheet",
            tags="budget,finance",
            embedding=[1.0, 2.0],
            source="upload",
        )
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(_search_db_session, "budget")

    assert response.total == 1
    assert response.results[0].id == "f1"
    # keyword-only: "budget" in filename(10) + summary(5) + tags(3) = 18/18 = 1.0
    assert response.results[0].relevance == pytest.approx(1.0)


async def test_search_files_keyword_fallback_exception(
    _search_db_session: AsyncSession,
) -> None:
    """search_files falls back to keyword-only when embedding generation raises."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add(
        FileRecord(
            id="f1",
            filename="budget.xlsx",
            size=100,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            checksum="aa",
            storage_key="/tmp/b.xlsx",
            summary="Budget spreadsheet",
            tags="budget,finance",
            embedding=[1.0, 2.0],
            source="upload",
        )
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(side_effect=RuntimeError("model offline")),
    ):
        response = await search_files(_search_db_session, "budget")

    assert response.total == 1
    assert response.results[0].id == "f1"


async def test_search_files_category_filter(
    _search_db_session: AsyncSession,
) -> None:
    """search_files filters results by category."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="report_a.pdf",
                size=100,
                content_type="application/pdf",
                checksum="aa",
                storage_key="/tmp/a.pdf",
                summary="Report A",
                tags="report",
                category="finance",
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="report_b.pdf",
                size=200,
                content_type="application/pdf",
                checksum="bb",
                storage_key="/tmp/b.pdf",
                summary="Report B",
                tags="report",
                category="hr",
                source="upload",
            ),
        ]
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(_search_db_session, "report", category="hr")

    assert response.total == 1
    assert response.results[0].id == "f2"


async def test_search_files_tags_filter(
    _search_db_session: AsyncSession,
) -> None:
    """search_files filters results by tags substring match."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="photo_a.png",
                size=100,
                content_type="image/png",
                checksum="aa",
                storage_key="/tmp/a.png",
                summary="Photo A",
                tags="photo,vacation",
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="photo_b.png",
                size=200,
                content_type="image/png",
                checksum="bb",
                storage_key="/tmp/b.png",
                summary="Photo B",
                tags="photo,work",
                source="upload",
            ),
        ]
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(_search_db_session, "photo", tags="vacation")

    assert response.total == 1
    assert response.results[0].id == "f1"


async def test_search_files_date_range_filter(
    _search_db_session: AsyncSession,
) -> None:
    """search_files filters results by created_after / created_before."""
    from datetime import UTC, datetime

    from src.robotsix_file_hub.search import search_files

    _search_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="old_report.pdf",
                size=100,
                content_type="application/pdf",
                checksum="aa",
                storage_key="/tmp/old.pdf",
                summary="Old report",
                tags="report",
                created_at=datetime(2020, 1, 15, tzinfo=UTC),
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="new_report.pdf",
                size=200,
                content_type="application/pdf",
                checksum="bb",
                storage_key="/tmp/new.pdf",
                summary="New report",
                tags="report",
                created_at=datetime(2025, 6, 1, tzinfo=UTC),
                source="upload",
            ),
        ]
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(
            _search_db_session,
            "report",
            created_after=datetime(2024, 1, 1, tzinfo=UTC),
        )

    assert response.total == 1
    assert response.results[0].id == "f2"


async def test_search_files_multiple_filters(
    _search_db_session: AsyncSession,
) -> None:
    """search_files applies category, tags, and date filters together."""
    from datetime import UTC, datetime

    from src.robotsix_file_hub.search import search_files

    _search_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="match.pdf",
                size=100,
                content_type="application/pdf",
                checksum="aa",
                storage_key="/tmp/match.pdf",
                summary="Matching report",
                tags="report,finance",
                category="finance",
                created_at=datetime(2025, 3, 1, tzinfo=UTC),
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="wrong_cat.pdf",
                size=200,
                content_type="application/pdf",
                checksum="bb",
                storage_key="/tmp/wrong_cat.pdf",
                summary="Matching report",
                tags="report,finance",
                category="hr",
                created_at=datetime(2025, 3, 1, tzinfo=UTC),
                source="upload",
            ),
            FileRecord(
                id="f3",
                filename="wrong_date.pdf",
                size=150,
                content_type="application/pdf",
                checksum="cc",
                storage_key="/tmp/wrong_date.pdf",
                summary="Matching report",
                tags="report,finance",
                category="finance",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
                source="upload",
            ),
        ]
    )
    await _search_db_session.commit()

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=None),
    ):
        response = await search_files(
            _search_db_session,
            "report",
            category="finance",
            tags="finance",
            created_after=datetime(2024, 1, 1, tzinfo=UTC),
        )

    assert response.total == 1
    assert response.results[0].id == "f1"


async def test_search_files_weight_constants(
    _search_db_session: AsyncSession,
) -> None:
    """search_files respects settings.search_vector_weight in hybrid scoring."""
    from src.robotsix_file_hub import search as search_module
    from src.robotsix_file_hub.search import search_files

    record_embedding = [0.0, 1.0, 0.0]
    _search_db_session.add(
        FileRecord(
            id="f1",
            filename="report.pdf",
            size=100,
            content_type="application/pdf",
            checksum="aa",
            storage_key="/tmp/r.pdf",
            summary="Financial report",
            tags="finance",
            embedding=record_embedding,
            source="upload",
        )
    )
    await _search_db_session.commit()

    canned_query = [0.0, 1.0, 0.0]  # cosine similarity = 1.0 (exact match)

    # vector_weight=0.0 → keyword-only score
    with (
        patch.object(search_module.settings, "search_vector_weight", 0.0),
        patch(
            "src.robotsix_file_hub.search.generate_embedding",
            new=AsyncMock(return_value=canned_query),
        ),
    ):
        response_kw = await search_files(_search_db_session, "financial report")

    # keyword: "financial"(summary=5) + "report"(filename=10, summary=5) = 20/36
    kw_only = 20.0 / 36.0
    assert response_kw.results[0].relevance == pytest.approx(kw_only, abs=1e-4)

    # vector_weight=1.0 → vector-only score
    with (
        patch.object(search_module.settings, "search_vector_weight", 1.0),
        patch(
            "src.robotsix_file_hub.search.generate_embedding",
            new=AsyncMock(return_value=canned_query),
        ),
    ):
        response_vec = await search_files(_search_db_session, "financial report")

    # vector score = 1.0 (exact cosine match)
    assert response_vec.results[0].relevance == pytest.approx(1.0, abs=1e-4)


async def test_search_files_semantic_match_no_keyword_overlap(
    _search_db_session: AsyncSession,
) -> None:
    """search_files returns files with close embeddings even without keyword overlap."""
    from src.robotsix_file_hub.search import search_files

    _search_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="img_001.png",
                size=100,
                content_type="image/png",
                checksum="aa",
                storage_key="/tmp/img.png",
                summary="Sunset photo",
                tags="photo,sunset",
                embedding=[1.0, 0.0],
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="budget.xlsx",
                size=200,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                checksum="bb",
                storage_key="/tmp/b.xlsx",
                summary="Financial budget",
                tags="finance,budget",
                embedding=[0.0, 1.0],
                source="upload",
            ),
        ]
    )
    await _search_db_session.commit()

    # Query words don't match any filename/summary/tags — only embedding matters
    canned_query = [1.0, 0.0]  # close to f1

    with patch(
        "src.robotsix_file_hub.search.generate_embedding",
        new=AsyncMock(return_value=canned_query),
    ):
        response = await search_files(_search_db_session, "zyxwv abcde")

    assert response.total == 2
    # f1 ranks first — its embedding is closer to the query vector
    assert response.results[0].id == "f1"
    assert response.results[1].id == "f2"
