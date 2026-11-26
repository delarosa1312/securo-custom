"""Fork tables (Securo Custom). Columns on upstream rows stay on their upstream model."""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

OCCURRENCE_STATES = ("generated", "skipped", "overridden")


class RecurringOccurrence(Base):
    """One occurrence of a recurring schedule that has been dealt with (F-004, fork migration f002).

    The unique key makes writing the same occurrence twice impossible, whoever runs generation.
    `occurrence_date` is the nominal date (before the weekend adjustment), the date the schedule's
    pointer walks. F-004 writes only `generated`; `skipped` and `overridden` are for #7.
    """

    __tablename__ = "recurring_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "recurring_transaction_id", "occurrence_date", name="uq_recurring_occurrence"
        ),
        CheckConstraint(
            "state IN ('generated', 'skipped', 'overridden')",
            name="ck_recurring_occurrences_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    recurring_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recurring_transactions.id", ondelete="CASCADE")
    )
    occurrence_date: Mapped[date] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(20), default="generated")
    # The row written for it (the source leg of a transfer); null when it was skipped or the
    # row was deleted later.
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
