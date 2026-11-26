# Projections: what each view counts (F-006, issue #8)

Securo shows a projected balance in several places. Since F-006 they agree wherever they describe the same thing:
the same accounts, the same day and the same currency give the same balance to the cent. This file is the written
comparison: what every view counts, the differences kept on purpose and why, and the tests that hold it.

## The views

| View | Screen / API | Accounts | Currency | Days |
| --- | --- | --- | --- | --- |
| Dashboard card ("projected balance") | Dashboard; `GET /api/dashboard/summary` | every open account, or the collection's | user's primary | through the end of the month |
| Dashboard chart | Dashboard; `GET /api/dashboard/balance-history` | open accounts except loans, or the collection's | user's primary | one month a day per point; the current month stops at today |
| Cash Flow report | Reports; `GET /api/reports/cash-flow`; MCP `get_cash_flow` | every open account, or the filter's | user's primary | past months walked back from today, future months walked forward |
| Calendar | Transactions calendar; `GET /api/transactions/calendar` | every open account, or the filter's | user's primary | the month's grid |
| Predictive forecast | MCP `get_cashflow_forecast` only (no screen) | included checking, savings and wallet accounts, plus open cards | workspace currency | today plus the horizon |
| Budgets | Budgets page; `GET /api/budgets/vs-actual`, `/forecast` | every account | user's primary | per month |
| Upcoming | Dashboard widget; `GET /api/dashboard/projected-transactions` | every open account | item's own, plus primary | from the workspace's today for 30 days, `to` inclusive |

## What every balance view counts the same way

Each line holds for the card, the chart, the Cash Flow report, the calendar and the predictive forecast, except
where "Kept differences" below says otherwise.

| Component | Counted |
| --- | --- |
| Posted rows | on their date, at the full amount, including transfer legs and rows excluded from P&L (they move a balance even when they stay out of income and expense bars) |
| Pending rows and future-dated posted rows (installments, rows generated ahead) | on their date; one dated today or earlier is not in the balance yet and counts tomorrow, the first projected day |
| Recurring occurrences | on their date while the rule is active, from the day after today; an occurrence that already exists as a row counts once, as that row |
| Rules treated as transfers ("Investments", one account only) | move the balance on their date; never in income and expense bars |
| Rules on a closed account, rules in an ignored category, ignored rows | never |
| Open receivable invoices (claims) | on the due date, all accounts only (a claim has no account until it is paid) |
| Connected accounts | the provider's balance is taken as it is; our own recurring placeholder rows always count, the provider's own pending rows count only from tomorrow |
| Foreign currency | posted rows at their stamped rate, projected items at the latest rate, into the viewer's currency |

Shared code: `dashboard_service._get_recurring_projections` (with `on_open_account`),
`_materialized_recurring_occurrences`, `_get_forecast_transactions`, and the fork helpers in
`backend/app/fork/projection_consistency.py` (chart and calendar) and `backend/app/fork/predictive_scheduled.py`
(predictive).

## Kept differences

Each one answers a different question than the other views, so making it equal would make it wrong.

- **Predictive forecast: money to pay bills.** Only accounts switched on for "Available balance" (F-001) plus card
  spending when it happens; no card debt already owed at the start (it is not due today); no investment or loan
  accounts and no claims; the workspace currency (it is a workspace figure, read over MCP); no account filter;
  a detected pattern counts unless a scheduled row of the same merchant already stands for it; the optional
  discretionary burn is on top. A recurring transfer from an included account into a switched-off one counts as
  money leaving. A row it cannot convert is left out and reported, never counted 1:1.
- **Accrual mode, Cash Flow report.** With credit cards in accrual mode the Cash Flow report is a bill-date cash
  view: a card purchase leaves the balance on its bill's due date, while the card, chart and calendar move it on
  the purchase date. Rows outside P&L (card payments, transfers) move on their own date in both modes.
- **Dashboard chart.** The current month stops at today; the card carries the rest of the month. The month's
  first point starts from the balance before the month, so an account's opening-balance row (source
  `opening_balance`) shows in the level, never as a day's movement. Loans stay out.
- **Budgets** count spending, not balances: posted and scheduled spending per category, scheduled refunds and
  credits netting against it (Q9), and a category's net spending never below zero.
- **Upcoming** lists recurring occurrences only (not pending rows or installments).
- **Calendar claims** move the balance and the day's projected totals but have no item row of their own.
- **Group splits.** Balance views move by the full amount of a row; P&L views (bars, budgets) use the user's share.
- **Missing exchange rate.** The card, chart, Cash Flow, calendar and budgets convert at 1:1 and the card flags
  it (`fx_fallback`); the predictive forecast leaves the row out and lists the currency.

## Known limits

- **Connected accounts, past days.** A provider's balance may or may not already hold its own pending rows, and
  each view guesses that differently for days before today. Compared from tomorrow on.
- **Accrual, recurring card purchases.** The Cash Flow report places a recurring card purchase on its purchase
  date, not on the bill's due date, in accrual mode.
- **Baseline mode** (`baseline=true` on the Cash Flow report) replaces recurring rules with a daily mean, so
  rules treated as transfers do not move it.

## Tests

- `backend/tests/fork/test_projection_reconciliation.py`: one synthetic base plus one component at a time; every
  view read at the same six dates, all accounts and checking only, cash and accrual mode. `MOVES` lists what each
  component should do; `LEFT_OUT` lists the kept differences above. `_projection_data.py` builds the components,
  `_projection_views.py` reads the views.
- `test_predictive_scheduled.py` (detected pattern against a pending row, switched-off account, overdue rows),
  `test_budget_forecast_report.py` (scheduled and recurring refunds), `test_net_worth_order.py`,
  `frontend/src/fork/projection-consistency/upcoming-widget.test.tsx` (workspace date).
- `test_projection_table.py` prints the full view-by-date table when `FORK_F006_TABLE` is set (diagnostic only).
