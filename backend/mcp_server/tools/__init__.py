"""Auto-imports all tool modules so they register themselves into the
shared registry on startup.
"""
from mcp_server.tools import (  # noqa: F401
    transactions,
    accounts,
    categories,
    payees,
    budgets,
    reports,
    search,
    aggregate,
    proposals,
    knowledge,
    lifecycle,
    groups,
    forecast,
    rules,
    transfers,
    fork_accounts,  # fork F-001
    fork_recurring_transfers,  # fork F-004
    fork_recurring_lifecycle,  # fork F-005
)
