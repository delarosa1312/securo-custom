"""F-005 backups on PostgreSQL, where foreign keys are enforced: a successor schedule points at its
predecessor in the same table, and the occurrence ledger at schedules and transactions."""

import pytest
import pytest_asyncio

from tests.fork.test_recurring_lifecycle_backup import (  # noqa: F401 -- rerun on PostgreSQL
    test_lifecycle_survives_a_round_trip,
    test_restored_edit_is_not_generated_again,
    test_successor_listed_before_its_predecessor_restores,
)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""
