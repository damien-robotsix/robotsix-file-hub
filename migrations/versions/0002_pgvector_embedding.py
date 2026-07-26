"""Add pgvector extension and change embedding column to vector type.

Revision ID: 0002_pgvector_embedding
Revises: 0001_initial
Create Date: 2026-07-26 01:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_pgvector_embedding"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE file_records DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE file_records ADD COLUMN embedding vector(384)")


def downgrade() -> None:
    op.execute("ALTER TABLE file_records DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE file_records ADD COLUMN embedding json")
