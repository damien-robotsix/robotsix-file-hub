"""Add upload_metadata column to file_records.

Persists the optional provenance/context payload a pushing component
(e.g. ``robotsix-auto-mail`` forwarding an unzipped mail attachment)
supplies alongside the file bytes at upload time.  The payload is stored
verbatim as a JSON string holding a free-text ``context``, a ``tags``
list, and an arbitrary ``provenance`` (``source``) key->value string
map.  All fields are optional, so the column is nullable and existing
uploads with no metadata are fully backward-compatible.

Revision ID: 0005_upload_metadata
Revises: 0004_metadata_source
Create Date: 2026-09-04 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_upload_metadata"
down_revision: str | None = "0004_metadata_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS guards against the column already being present when
    # init_db/create_all (which creates the full ORM schema, including
    # upload_metadata) ran before this migration on the same database.
    op.execute("ALTER TABLE file_records ADD COLUMN IF NOT EXISTS upload_metadata TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE file_records DROP COLUMN IF EXISTS upload_metadata")
