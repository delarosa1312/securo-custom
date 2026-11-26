"""F-006 harness on PostgreSQL: the date windows, the P&L flag column and the closed-account subquery (#8)."""
import pytest
import pytest_asyncio

from app.services import admin_service
from tests.fork.test_projection_reconciliation import ACCRUAL_CASES, MOVES, _reconcile


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test already starts in a fresh PostgreSQL schema."""


@pytest.mark.parametrize("component", list(MOVES))
@pytest.mark.parametrize("scope", ["all", "checking"])
@pytest.mark.asyncio
async def test_views_reconcile_on_postgres(session, test_user, test_workspace, monkeypatch, component, scope):
    await _reconcile(session, test_user, test_workspace, monkeypatch, component, scope)


@pytest.mark.parametrize("component", ACCRUAL_CASES)
@pytest.mark.asyncio
async def test_views_reconcile_in_accrual_mode_on_postgres(session, test_user, test_workspace, monkeypatch,
                                                           component):
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", "accrual")
    await _reconcile(session, test_user, test_workspace, monkeypatch, component, "all")
