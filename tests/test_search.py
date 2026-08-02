"""Integration tests for the search endpoints.

Covers POST /files/search and POST /search with the full application
stack (test client + test database session).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.robotsix_file_hub.models import FileRecord


# ── POST /files/search endpoint tests ──────────────────────────────


async def test_search_empty_db(test_client: AsyncClient) -> None:
    """POST /files/search returns empty results when no files exist."""
    response = await test_client.post(
        "/files/search",
        json={"query": "budget report"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []
    assert data["total"] == 0
    assert data["query"] == "budget report"


async def test_search_keyword_only(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /files/search returns keyword-ranked results (no embeddings)."""
    # Pre-populate files with enrichment fields (no embeddings)
    test_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="budget_2024.xlsx",
                size=100,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                checksum="aa",
                storage_key="/tmp/budget.xlsx",
                summary="Annual budget report for FY 2024",
                tags="budget,finance,annual",
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="vacation_photo.png",
                size=200,
                content_type="image/png",
                checksum="bb",
                storage_key="/tmp/vacation.png",
                summary="Beach vacation photo",
                tags="vacation,photo,beach",
                source="upload",
            ),
            FileRecord(
                id="f3",
                filename="budget_q1.pdf",
                size=150,
                content_type="application/pdf",
                checksum="cc",
                storage_key="/tmp/q1.pdf",
                summary="Q1 budget review",
                tags="budget,quarterly",
                source="upload",
            ),
        ]
    )
    await test_db_session.commit()

    response = await test_client.post(
        "/files/search",
        json={"query": "budget report"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["query"] == "budget report"

    results = data["results"]
    # budget_2024.xlsx should rank higher (budget in filename + summary + tags)
    assert results[0]["filename"] == "budget_2024.xlsx"
    # budget_q1.pdf should be second
    assert results[1]["filename"] == "budget_q1.pdf"

    # Each result should have a relevance score
    for r in results:
        assert "relevance" in r
        assert isinstance(r["relevance"], (int, float))
        assert 0.0 <= r["relevance"] <= 1.0
        # Metadata fields should be present
        assert "id" in r
        assert "category" in r
        assert "summary" in r
        assert "tags" in r


async def test_search_keyword_no_results(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /files/search returns empty when no files match the query."""
    test_db_session.add(
        FileRecord(
            id="f1",
            filename="photo.png",
            size=100,
            content_type="image/png",
            checksum="aa",
            storage_key="/tmp/photo.png",
            summary="A nice photo",
            tags="photo",
            source="upload",
        )
    )
    await test_db_session.commit()

    response = await test_client.post(
        "/files/search",
        json={"query": "budget finance spreadsheet"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []
    assert data["total"] == 0


async def test_search_pagination(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /files/search respects offset and limit."""
    for i in range(5):
        test_db_session.add(
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
    await test_db_session.commit()

    # Page 1: first 2
    resp1 = await test_client.post(
        "/files/search",
        json={"query": "report", "offset": 0, "limit": 2},
    )
    # Page 2: next 2
    resp2 = await test_client.post(
        "/files/search",
        json={"query": "report", "offset": 2, "limit": 2},
    )
    # Page 3: remaining 1
    resp3 = await test_client.post(
        "/files/search",
        json={"query": "report", "offset": 4, "limit": 2},
    )

    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1["results"]) == 2
    assert data1["total"] == 5
    assert data1["offset"] == 0
    assert data1["limit"] == 2

    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["results"]) == 2
    assert data2["total"] == 5

    assert resp3.status_code == 200
    data3 = resp3.json()
    assert len(data3["results"]) == 1
    assert data3["total"] == 5
    assert data3["offset"] == 4


async def test_search_default_pagination(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /files/search with no pagination params uses defaults."""
    test_db_session.add(
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
    await test_db_session.commit()

    response = await test_client.post(
        "/files/search",
        json={"query": "document"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["offset"] == 0
    assert data["limit"] == 50
    assert data["total"] == 1


async def test_search_with_hybrid_scoring(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /files/search uses vector similarity when embeddings are available.

    Mocks generate_embedding_async to return a canned vector and verifies
    that hybrid scoring produces different (improved) rankings.
    """
    # File with matching keywords but distant embedding
    file1_embedding = [1.0, 0.0, 0.0]
    # File with fewer keyword matches but close embedding
    file2_embedding = [0.0, 1.0, 0.0]

    test_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="vacation_beach_photo.png",
                size=100,
                content_type="image/png",
                checksum="aa",
                storage_key="/tmp/vacation.png",
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
                storage_key="/tmp/report.pdf",
                summary="Financial analysis",
                tags="finance",
                embedding=file2_embedding,
                source="upload",
            ),
        ]
    )
    await test_db_session.commit()

    # Mock generate_embedding_async to return a vector close to file2's embedding
    canned_query_embedding = [0.1, 0.9, 0.0]

    with patch(
        "src.robotsix_file_hub.search.generate_embedding_async",
        new=AsyncMock(return_value=canned_query_embedding),
    ):
        response = await test_client.post(
            "/files/search",
            json={"query": "financial report"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    results = data["results"]

    # With query embedding close to file2, file2 should rank higher
    # even though file1 has more keyword matches ("vacation" doesn't match "financial report")
    assert results[0]["id"] == "f2"
    assert results[1]["id"] == "f1"


async def test_search_fallback_when_embedding_fails(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /files/search falls back to keyword-only when embedding API fails."""
    test_db_session.add(
        FileRecord(
            id="f1",
            filename="budget.xlsx",
            size=100,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            checksum="aa",
            storage_key="/tmp/budget.xlsx",
            summary="Budget spreadsheet",
            tags="budget,finance",
            embedding=[1.0, 2.0],
            source="upload",
        )
    )
    await test_db_session.commit()

    # Mock generate_embedding_async to return None (simulating API failure)
    with patch(
        "src.robotsix_file_hub.search.generate_embedding_async",
        new=AsyncMock(return_value=None),
    ):
        response = await test_client.post(
            "/files/search",
            json={"query": "budget"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["results"][0]["filename"] == "budget.xlsx"


# ── POST /search endpoint tests (with filters) ─────────────────────


async def test_search_endpoint_empty_db(test_client: AsyncClient) -> None:
    """POST /search returns empty results when no files exist."""
    response = await test_client.post(
        "/search",
        json={"query": "budget report"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []
    assert data["total"] == 0
    assert data["query"] == "budget report"


async def test_search_endpoint_keyword(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search returns keyword-ranked results (SQLite fallback path)."""
    test_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="budget_2024.xlsx",
                size=100,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                checksum="aa",
                storage_key="/tmp/budget.xlsx",
                summary="Annual budget report for FY 2024",
                tags="budget,finance,annual",
                category="finance",
                source="upload",
            ),
            FileRecord(
                id="f2",
                filename="vacation_photo.png",
                size=200,
                content_type="image/png",
                checksum="bb",
                storage_key="/tmp/vacation.png",
                summary="Beach vacation photo",
                tags="vacation,photo,beach",
                category="personal",
                source="upload",
            ),
        ]
    )
    await test_db_session.commit()

    response = await test_client.post(
        "/search",
        json={"query": "budget report"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["results"][0]["filename"] == "budget_2024.xlsx"


async def test_search_endpoint_category_filter(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search with category filter only returns matching category."""
    test_db_session.add_all(
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
    await test_db_session.commit()

    response = await test_client.post(
        "/search",
        json={"query": "report", "category": "hr"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["results"][0]["filename"] == "report_b.pdf"


async def test_search_endpoint_tags_filter(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search with tags filter only returns files containing the tag."""
    test_db_session.add_all(
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
    await test_db_session.commit()

    response = await test_client.post(
        "/search",
        json={"query": "photo", "tags": "vacation"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["results"][0]["filename"] == "photo_a.png"


async def test_search_endpoint_date_range_filter(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search with date range filters by created_at."""
    test_db_session.add_all(
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
    await test_db_session.commit()

    # Filter for reports after 2024
    response = await test_client.post(
        "/search",
        json={
            "query": "report",
            "created_after": "2024-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["results"][0]["filename"] == "new_report.pdf"


async def test_search_endpoint_multiple_filters(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search with category + tags + date range combined."""
    test_db_session.add_all(
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
    await test_db_session.commit()

    response = await test_client.post(
        "/search",
        json={
            "query": "report",
            "category": "finance",
            "tags": "finance",
            "created_after": "2024-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["results"][0]["filename"] == "match.pdf"


async def test_search_endpoint_pagination(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search respects offset and limit."""
    for i in range(5):
        test_db_session.add(
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
    await test_db_session.commit()

    resp1 = await test_client.post(
        "/search",
        json={"query": "report", "offset": 0, "limit": 2},
    )
    resp2 = await test_client.post(
        "/search",
        json={"query": "report", "offset": 2, "limit": 2},
    )
    resp3 = await test_client.post(
        "/search",
        json={"query": "report", "offset": 4, "limit": 2},
    )

    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1["results"]) == 2
    assert data1["total"] == 5

    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["results"]) == 2

    assert resp3.status_code == 200
    data3 = resp3.json()
    assert len(data3["results"]) == 1


async def test_search_endpoint_hybrid_scoring(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search uses vector similarity when embeddings are available."""
    file1_embedding = [1.0, 0.0, 0.0]
    file2_embedding = [0.0, 1.0, 0.0]

    test_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="vacation_beach_photo.png",
                size=100,
                content_type="image/png",
                checksum="aa",
                storage_key="/tmp/vacation.png",
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
                storage_key="/tmp/report.pdf",
                summary="Financial analysis",
                tags="finance",
                embedding=file2_embedding,
                source="upload",
            ),
        ]
    )
    await test_db_session.commit()

    canned_query_embedding = [0.1, 0.9, 0.0]

    # NOTE: the new /search endpoint imports from search.search_files_pg
    # which in turn imports generate_embedding_async — patch at the search
    # module level to cover both paths.
    with patch(
        "src.robotsix_file_hub.search.generate_embedding_async",
        new=AsyncMock(return_value=canned_query_embedding),
    ):
        response = await test_client.post(
            "/search",
            json={"query": "financial report"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    results = data["results"]
    # file2 should rank higher due to closer embedding
    assert results[0]["id"] == "f2"
    assert results[1]["id"] == "f1"


async def test_search_endpoint_no_results(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> None:
    """POST /search returns empty when no files match the query."""
    test_db_session.add(
        FileRecord(
            id="f1",
            filename="photo.png",
            size=100,
            content_type="image/png",
            checksum="aa",
            storage_key="/tmp/photo.png",
            summary="A nice photo",
            tags="photo",
            source="upload",
        )
    )
    await test_db_session.commit()

    response = await test_client.post(
        "/search",
        json={"query": "budget finance spreadsheet"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []
    assert data["total"] == 0
