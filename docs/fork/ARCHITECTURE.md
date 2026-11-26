# Architecture

Upstream's own map is `AGENTS.md` and `docs/adr/`. This file records what the fork needs on top: how the
projection code is laid out at `v0.22.0`, where fork code goes, and which files several features share.

## Upstream at v0.22.0, the parts we extend

### Three projection engines

| Engine | Entry point | Used by | Combines | Leaves out |
| --- | --- | --- | --- | --- |
| Cash Flow report | `report_service.get_cash_flow_report` (`GET /api/reports/cash-flow`) | Reports page, MCP `get_cash_flow` | balance at today (all open account types), posted flows, pending and future rows, recurring occurrences, invoice claims; `baseline=true` replaces recurring with a daily mean of up to 12 months | budgets; transfers in the flow bars |
| Predictive cash flow | `cashflow_forecast_service.generate_cashflow_forecast` (`GET /api/forecast/cashflow`) | MCP `get_cashflow_forecast` only, no screen | liquid balance (checking, savings, wallet), manual and detected recurring, 90-day discretionary burn; since F-006 pending and future-dated rows on included accounts and cards | invoice claims, budgets, collections, accounting mode |
| Budget forecast | `budget_service.get_multi_month_forecast` (`GET /api/budgets/forecast`) | no screen | budget minus posted and recurring spend per month | history; balances |

Shared helpers sit in `dashboard_service.py`: `_get_recurring_projections`, `_dated_occurrences`,
`_materialized_recurring_occurrences`, `_get_forecast_transactions`, `_balance_at`, `_total_balance_by_currency`.
Occurrence expansion is `recurring_transaction_service.get_occurrences_in_range` with a `[start, end)` window.
The dashboard projection, the balance-history chart, Upcoming (`get_projected_transactions`) and the calendar
(`transaction_calendar_service`) each assemble these differently. Since F-006 (#8) they reconcile to the cent
where they describe the same thing; `PROJECTIONS.md` is the comparison, the kept differences and the tests.
The Cash Flow report still returns `float`.

### Accounts, available balance, collections

- `Account` has no include or exclude flag, only `is_closed`.
- The dashboard's "Available balance" is computed in the browser (`frontend/src/pages/dashboard.tsx`): open
  checking, savings and wallet accounts, converted to the primary currency, scoped to the active collection.
- Collections filter only in the frontend: pages send `account_ids`. The backend takes `account_ids` (None = all).
- Provider reserved balances (ADR 0022) are display-only and never summed.

### Budgets

Per category, per month, optional `is_recurring` (the latest recurring row at or before a month applies unless a
month-specific row exists: `_build_budget_map`). No category-group budgets, no stored rollover.
`get_budget_vs_actual` already computes actual and projected (recurring plus scheduled) spend per category.

### Recurring, transfers, installments

- `recurring_transactions`: one account, no destination; frequencies weekly to yearly; weekend adjustment;
  `is_active`, `end_date`, `auto_generate`, `next_occurrence`. No skip, no exceptions, no effective-dated change,
  hard delete (generated rows keep living with the link nulled).
- Generation (`generate_pending`, hourly Celery beat plus a button) advances `next_occurrence` with no row lock
  and no unique key on (schedule, date): two workers can create the same occurrence twice.
- Transfers: two rows sharing `transfer_pair_id`; `create_transfer` handles cross-currency with
  `destination_amount` or the rate at the transfer date. Reports exclude them through `_query_filters`.
- Installments: individual rows with `installment_*` columns and `installment_series_id`; edit and delete take
  `apply_to` = this, future or all.

### MCP

`backend/mcp_server/tools/*.py`, declared with `@tool(...)`, registered by import in `tools/__init__.py`. Tools
call services directly. Writes are `propose_*` tools: the first call validates and returns a preview
(`kind`, `proposed` or `target` plus `changes`, `apply_endpoint`); a second call with `apply=true` from an external
token with a write role performs it. In the app, `proposal-card.tsx` applies a proposal through REST.

### Things with tests that will notice us

`tests/test_workspace_boundary_schema.py` (every table has `workspace_id`), `tests/test_write_permission_coverage.py`
(every mutating route has a write gate), `tests/api_contract/baseline.json` (every route), 
`core/access_token_scopes.py` (new routes are closed to tokens until mapped), `backup_service` coverage guard
(every workspace table is in the backup), `tests/test_schema_matches_migrations_postgres.py` (models equal
migrations), `frontend/src/locales/i18n.test.ts` (all 20 locale files have the same keys).

## Where fork code goes

| Kind | Location | Hook into upstream |
| --- | --- | --- |
| Services, schemas, pure calculators | `backend/app/fork/` | a call from the upstream service or router |
| Routers for new resources | `backend/app/fork/api/`, mounted under existing prefixes | one include in `app/main.py` |
| Models and columns | `backend/app/fork/models.py` for new tables; a column on an upstream model only when it is a plain attribute of that row | the column line, import in `app/models/__init__.py` |
| Migrations | `backend/alembic/fork_versions/` (`f001`, `f002`, ... on the `fork` branch label) | `version_locations` in `alembic.ini`, `upgrade heads` in the migrate command |
| MCP tools | `backend/mcp_server/tools/fork_*.py` | one import in `tools/__init__.py` |
| Backend tests | `backend/tests/fork/` | none |
| Frontend components, hooks, strings | `frontend/src/fork/` (strings in `frontend/src/fork/locales/en.json` and `pt-BR.json`) | a render or import at the place the feature shows; one bundle merge in `lib/i18n.ts` |
| Tools, E2E, deploy files | `fork/` | none |
| Workflows | `.github/workflows/fork-*.yml` | none |

Calculators that produce money are pure functions over plain inputs (rows in, `Decimal` out), so they can be
tested without a database and reused by REST, MCP and every report that needs the same number.

## Shared files: who may touch what at the same time

Two sessions never edit the same upstream file in parallel without the coordinator knowing. The map:

| File | F-001 | F-002 | F-003 | F-004 | F-005 | F-006 |
| --- | --- | --- | --- | --- | --- | --- |
| `app/models/account.py`, `schemas/account.py`, `services/account_service.py` | x | | | | | |
| `services/report_service.py` (`get_cash_flow_report`) | | x | x | | | x |
| `services/cashflow_forecast_service.py` | x | | x | | | x |
| `services/dashboard_service.py` (projection helpers) | | | | x | x | x |
| `services/budget_service.py` | | x | | | | |
| `services/recurring_transaction_service.py`, model, schema, router | | | | x | x | |
| `services/transaction_calendar_service.py` | | | | x | | x |
| `mcp_server/tools/forecast.py`, `proposals.py` | x | x | x | x | x | |
| `frontend/src/pages/dashboard.tsx`, `accounts.tsx` | x | | | | | |
| `frontend/src/pages/reports.tsx`, `components/reports/` | | x | x | | | |
| `frontend/src/pages/recurring.tsx` | | | | x | x | |
| `frontend/src/lib/api.ts`, `types/index.ts` | x | x | x | x | x | x |
| `tests/api_contract/baseline.json`, `core/access_token_scopes.py` | x | x | x | x | x | |
| fork migration ids | f001 | none expected | f00n | f00n | f00n | none |

Consequences: F-001 and F-002 can run in parallel (they meet only in `api.ts`, `types/index.ts` and the contract
baseline, which merge by hand in minutes). F-004 and F-005 share almost every recurring file and run one after
the other. F-006 touches every engine and starts after F-002, with its findings feeding F-003.
