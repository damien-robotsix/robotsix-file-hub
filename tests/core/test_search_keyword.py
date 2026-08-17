"""Unit tests for keyword scoring, cosine similarity, and hybrid scoring."""

import pytest

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
