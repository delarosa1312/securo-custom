"""recurring schedule lifecycle (F-005): series links and recoverable deletion

Revision ID: f003
Revises: f002

Additive. `recurring_transactions` gains `predecessor_id` (the schedule a "from this date on" change
continues), `deleted_at` (set while the schedule sits in Deleted) and `active_before_delete` (what a
restore gives back). Existing schedules keep nulls and behave as before.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f003"
down_revision = "f002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column("predecessor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("active_before_delete", sa.Boolean(), nullable=True),
    )
    op.create_foreign_key(
        "recurring_transactions_predecessor_id_fkey",
        "recurring_transactions",
        "recurring_transactions",
        ["predecessor_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "recurring_transactions_predecessor_id_fkey", "recurring_transactions", type_="foreignkey"
    )
    op.drop_column("recurring_transactions", "active_before_delete")
    op.drop_column("recurring_transactions", "deleted_at")
    op.drop_column("recurring_transactions", "predecessor_id")
