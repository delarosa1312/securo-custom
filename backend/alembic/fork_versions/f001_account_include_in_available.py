"""account include_in_available: leave an account out of the available balance (F-001)

Revision ID: f001
Revises: f000

Additive. Every existing account starts included, so nothing an installation
shows changes until someone switches an account off.
"""

import sqlalchemy as sa
from alembic import op

revision = "f001"
down_revision = "f000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "include_in_available",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "include_in_available")
