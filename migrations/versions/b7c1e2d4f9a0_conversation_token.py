"""conversation access token

Revision ID: b7c1e2d4f9a0
Revises: 4a5f52e34a99
Create Date: 2026-11-06 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1e2d4f9a0"
down_revision: str | Sequence[str] | None = "4a5f52e34a99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # SHA-256 of the per-conversation secret handed to the customer; rows without one cannot be used.
    op.add_column("conversations", sa.Column("token_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("conversations", "token_hash")
