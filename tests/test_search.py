"""Tests for the hybrid NL search endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.robotsix_file_hub.models import FileRecord

# ── Keyword scoring unit tests ─────────────────────────────────────


async def test_keyword_score_exact_match() -> None:
    """_keyword_score returns 1.0 when all tokens match all fields."""
    from src.robotsix_file_hub.search import _keyword_score

    record = FileRecord(
        id="test",
        filename="report.pdf",
        size=100,
        content_type="application/pdf",
        checksum="abc",
        storage_key="/tmp/r.pdf",
        summary="A financial report",
        tags="finance,report",
    )
    score = _keyword_score(record, ["report", "financial"])
    # "report" in filename (10) + summary (5) + tags (3) = 18
    # "financial" in summary (5) = 5
    # total = 23, max = 2 * (10+5+3) = 36
    assert score == pytest.approx(23 / 36)


async def test_keyword_score_no_match() -> None:
    """_keyword_score returns 0.0 when no token matches."""
    from src.robotsix_file_hub.search import _keyword_score

    record = FileRecord(
        id="test",
        filename="image.png",
        size=100,
        content_type="image/png",
        checksum="abc",
        storage_key="/tmp/i.png",
        summary="A photo",
        tags="photo,image",
    )
    score = _keyword_score(record, ["document", "text"])
    assert score == 0.0


async def test_keyword_score_empty_tokens() -> None:
    """_keyword_score returns 0.0 for empty token list."""
    from src.robotsix_file_hub.search import _keyword_score

    record = FileRecord(
        id="test",
        filename="file.txt",
        size=10,
        content_type="text/plain",
        checksum="abc",
        storage_key="/tmp/f.txt",
    )
    score = _keyword_score(record, [])
    assert score == 0.0


# ── Cosine similarity unit tests ───────────────────────────────────


async def test_cosine_similarity_identical() -> None:
    """Cosine similarity of identical vectors is 1.0."""
    from src.robotsix_file_hub.search import _cosine_similarity

    vec = [1.0, 2.0, 3.0]
    assert _cosine_similarity(vec, vec) == pytest.approx(1.0)


async def test_cosine_similarity_orthogonal() -> None:
    """Cosine similarity of orthogonal vectors is 0.0."""
    from src.robotsix_file_hub.search import _cosine_similarity

    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


async def test_cosine_similarity_zero_magnitude() -> None:
    """Cosine similarity returns 0.0 when one vector has zero magnitude."""
    from src.robotsix_file_hub.search import _cosine_similarity

    assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ── Embedding parsing unit tests ───────────────────────────────────


async def test_parse_embedding_valid() -> None:
    """_parse_embedding returns a float list for valid JSON."""
    from src.robotsix_file_hub.search import _parse_embedding

    result = _parse_embedding("[1.0, 2.0, 3.0]")
    assert result == [1.0, 2.0, 3.0]


async def test_parse_embedding_none() -> None:
    """_parse_embedding returns None for None input."""
    from src.robotsix_file_hub.search import _parse_embedding

    assert _parse_embedding(None) is None


async def test_parse_embedding_invalid_json() -> None:
    """_parse_embedding returns None for invalid JSON."""
    from src.robotsix_file_hub.search import _parse_embedding

    assert _parse_embedding("not-json") is None


async def test_parse_embedding_empty_list() -> None:
    """_parse_embedding returns None for an empty list."""
    from src.robotsix_file_hub.search import _parse_embedding

    assert _parse_embedding("[]") is None


# ── Hybrid score unit tests ────────────────────────────────────────


async def test_hybrid_score_vector_only() -> None:
    """_hybrid_score with vector_weight=1.0 uses only vector similarity."""
    from src.robotsix_file_hub.search import _hybrid_score

    record = FileRecord(
        id="test",
        filename="file.txt",
        size=10,
        content_type="text/plain",
        checksum="abc",
        storage_key="/tmp/f.txt",
        embedding=[1.0, 0.0, 0.0],
    )
    query_embedding = [1.0, 0.0, 0.0]
    score = _hybrid_score(record, ["nothing"], query_embedding, vector_weight=1.0)
    assert score == pytest.approx(1.0)


async def test_hybrid_score_keyword_only() -> None:
    """_hybrid_score with vector_weight=0.0 uses only keyword score."""
    from src.robotsix_file_hub.search import _hybrid_score

    record = FileRecord(
        id="test",
        filename="report.pdf",
        size=10,
        content_type="application/pdf",
        checksum="abc",
        storage_key="/tmp/r.pdf",
        summary="A report",
    )
    score = _hybrid_score(record, ["report"], None, vector_weight=0.0)
    # "report" in filename (10) + in summary (5) = 15 / max(1*(10+5+3)=18) = 0.8333
    assert score == pytest.approx(15 / 18)


async def test_hybrid_score_fallback_no_embedding() -> None:
    """_hybrid_score falls back to keyword-only when record has no embedding."""
    from src.robotsix_file_hub.search import _hybrid_score

    record = FileRecord(
        id="test",
        filename="report.pdf",
        size=10,
        content_type="application/pdf",
        checksum="abc",
        storage_key="/tmp/r.pdf",
        summary="A report",
        embedding=None,
    )
    query_embedding = [1.0, 0.0]
    score = _hybrid_score(record, ["report"], query_embedding, vector_weight=0.7)
    # Falls back to keyword: "report" in filename (10) + summary (5) = 15/18
    assert score == pytest.approx(15 / 18)


async def test_hybrid_score_fallback_no_query_embedding() -> None:
    """_hybrid_score falls back to keyword-only when query_embedding is None."""
    from src.robotsix_file_hub.search import _hybrid_score

    record = FileRecord(
        id="test",
        filename="report.pdf",
        size=10,
        content_type="application/pdf",
        checksum="abc",
        storage_key="/tmp/r.pdf",
        summary="A report",
        embedding=[1.0, 0.0],
    )
    score = _hybrid_score(record, ["report"], None, vector_weight=0.7)
    # Falls back to keyword: "report" in filename (10) + summary (5) = 15/18
    assert score == pytest.approx(15 / 18)


# ── Search endpoint integration tests ──────────────────────────────


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
