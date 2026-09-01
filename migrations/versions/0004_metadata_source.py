"""Add metadata_source column to file_records.

Records the provenance of the enrichment metadata (summary/category/tags):
``"enrichment"`` for values written by the automatic pipeline, and
``"agent"``/``"manual"`` for curated values written via
``PATCH /files/{id}/metadata``.  Curated records are skipped by later
automatic enrichment/reindex passes unless explicitly forced.

Revision ID: 0004_metadata_source
Revises: 0003_embedding_dim_1024
Create Date: 2026-09-01 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_metadata_source"
down_revision: str | None = "0003_embedding_dim_1024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS guards against the column already being present when
    # init_db/create_all (which creates the full ORM schema, including
    # metadata_source) ran before this migration on the same database.
    op.execute("ALTER TABLE file_records ADD COLUMN IF NOT EXISTS metadata_source VARCHAR(16)")


def downgrade() -> None:
    op.execute("ALTER TABLE file_records DROP COLUMN IF EXISTS metadata_source")
