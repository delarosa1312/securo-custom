"""recurring transfers and the recurring occurrence ledger (F-004)

Revision ID: f002
Revises: f001

Additive. `recurring_transactions` gains a nullable destination account and destination amount
(a schedule with a destination is a transfer); `recurring_occurrences` records each occurrence
generation wrote, unique per schedule and nominal date. Existing schedules keep a null
destination and behave as before.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f002"
down_revision = "f001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column("destination_account_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("destination_amount", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.create_foreign_key(
        "recurring_transactions_destination_account_id_fkey",
        "recurring_transactions",
        "accounts",
        ["destination_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "recurring_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recurring_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recurring_transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "recurring_transaction_id", "occurrence_date", name="uq_recurring_occurrence"
        ),
        sa.CheckConstraint(
            "state IN ('generated', 'skipped', 'overridden')",
            name="ck_recurring_occurrences_state",
        ),
    )
    op.create_index(
        "ix_recurring_occurrences_workspace_id", "recurring_occurrences", ["workspace_id"]
    )
    # Deleting transactions sets transaction_id to null here; the index keeps that from scanning.
    op.create_index(
        "ix_recurring_occurrences_transaction_id", "recurring_occurrences", ["transaction_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_recurring_occurrences_transaction_id", table_name="recurring_occurrences")
    op.drop_index("ix_recurring_occurrences_workspace_id", table_name="recurring_occurrences")
    op.drop_table("recurring_occurrences")
    op.drop_constraint(
        "recurring_transactions_destination_account_id_fkey",
        "recurring_transactions",
        type_="foreignkey",
    )
    op.drop_column("recurring_transactions", "destination_amount")
    op.drop_column("recurring_transactions", "destination_account_id")
