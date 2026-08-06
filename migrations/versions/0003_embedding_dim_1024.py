"""Widen the embedding column from 384 to 1024 dimensions.

Embeddings moved from the in-process sentence-transformers model
(all-MiniLM-L6-v2, 384-dim) to the configured OpenAI-compatible endpoint
serving bge-m3 (1024-dim). pgvector columns are fixed-width, so the
column has to move with the model or every insert fails on a dimension
mismatch.

Existing vectors cannot be converted — they are outputs of a different
model, not a different encoding of the same thing — so they are dropped
and the column is recreated empty. Re-index affected files to repopulate;
until then search degrades to keyword-only, which is the same fallback
used when the embedding endpoint is unreachable.

Revision ID: 0003_embedding_dim_1024
Revises: 0002_pgvector_embedding
Create Date: 2026-08-06 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_embedding_dim_1024"
down_revision: str | None = "0002_pgvector_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE file_records DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE file_records ADD COLUMN embedding vector(1024)")


def downgrade() -> None:
    op.execute("ALTER TABLE file_records DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE file_records ADD COLUMN embedding vector(384)")
