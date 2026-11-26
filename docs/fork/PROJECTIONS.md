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
| Pending rows and future-dated posted rows (installments, rows generated ahead) | on their own date, even when a bill date or a reporting date lists them later; one dated today or earlier is not in the balance yet and counts tomorrow, the first projected day |
| Recurring occurrences | on their date while the rule is active, from the day after today; an occurrence that already exists as a row counts once, as that row |
| Rules treated as transfers ("Investments", one account only) | move the balance on their date; never in income and expense bars |
| Rules on a closed account, rules in an ignored category, ignored rows | never |
| Open receivable invoices (claims) | on the due date, all accounts only (a claim has no account until it is paid) |
| Connected accounts | the provider's balance is taken as it is; our own recurring placeholder rows always count; the provider's own pending rows dated today or earlier never count (the provider figure may already hold them), later ones count on their date |
| Foreign currency | into the viewer's currency; while rates do not move after a row or rule was stamped, every view gives the same figure (see Known limits) |

Shared code: `dashboard_service._get_recurring_projections` (with `on_open_account`),
`_materialized_recurring_occurrences`, `_get_forecast_transactions`, and the fork helpers in
`backend/app/fork/projection_consistency.py` (chart and calendar) and `backend/app/fork/predictive_scheduled.py`
(predictive).

## Kept differences

Each one answers a different question than the other views, so making it equal would make it wrong.

- **Predictive forecast: money to pay bills.** Only accounts switched on for "Available balance" (F-001) plus card
  spending when it happens after today; no card debt already owed (a card row dated today or earlier, pending or
  posted); card refunds and card bill payments do not count (paying the bill would count the spending a second
  time, so a scheduled payment from checking to a card leaves the forecast unchanged); no investment or loan
  accounts and no claims, and detected patterns only from the accounts it follows; the workspace currency (it is
  a workspace figure, read over MCP); no account filter; a detected pattern counts unless a scheduled row of the
  same merchant stands for it within half a period (a one-off purchase at the same merchant can stand for it); the
  optional discretionary burn is on top. A transfer from an included account into a switched-off one counts as
  money leaving. A row it cannot convert is left out and reported, never counted 1:1. The MCP tool's description
  says so.
- **Bill dates, Cash Flow report.** The Cash Flow report is a bill-date cash view: a row with a bill date leaves
  the balance on that date (every card purchase in accrual mode; a manual bill date in either mode), while the
  card, chart, calendar and predictive move it on its own date. Rows outside P&L (card payments, transfers) move
  on their own date in both modes.
- **Dashboard chart.** The current month stops at today; the card carries the rest of the month. The month's
  first point starts from the balance before the month, so an account's opening-balance row (source
  `opening_balance`) shows in the level, never as a day's movement. Loans stay out.
- **Budgets** count spending, not balances: posted and scheduled spending per category, scheduled refunds and
  credits netting against it (Q9), and a category's net spending never below zero.
- **Upcoming** lists recurring occurrences only (not pending rows or installments).
- **Calendar claims** move the balance and the day's projected totals but have no item row of their own.
- **Calendar date basis.** With the "reporting" basis the calendar lists a row on its reporting date; its balance
  still moves on the row's own date, as in the other views.
- **Group splits.** Balance views move by the full amount of a row; P&L views (bars, budgets) use the user's share.
- **Missing exchange rate.** Into the viewer's currency, the card, chart, Cash Flow, calendar and budgets convert
  at 1:1 and the card flags it (`fx_fallback`); the predictive forecast leaves the row out and lists the currency.
  A row in another currency than its account's, with no stored account amount, moves its account by 0 in the
  card, chart and calendar; the Cash Flow converts it.

## Known limits

- **Connected accounts, past days.** A provider's balance may or may not already hold its own pending rows, and
  each view guesses that differently for days before today. Compared from tomorrow on.
- **Accrual, recurring card purchases.** The Cash Flow report places a recurring card purchase on its purchase
  date, not on the bill's due date, in accrual mode.
- **Exchange rates that moved.** Rows and rules carry the conversion stamped when they were saved. The card and
  the predictive convert projected items at the latest rate; the calendar uses a rule's stamped amount, the Cash
  Flow a pending row's stamped amount and, for past days, the latest rate for today's balance with stamped rates
  for the flows; the card for a past month converts at that month's rate, the chart and calendar at the latest. When a rate moves after stamping the views differ by that change (USD at 5 then 6: up to 20 on a
  10 USD rule and row). Older than F-006; a follow-up.
- **Rules without an account** (legacy) count everywhere except the calendar, which lists only rules with an
  account.
- **Baseline mode** (`baseline=true` on the Cash Flow report) replaces recurring rules with a daily mean, so
  rules treated as transfers do not move it.

## Tests

- `backend/tests/fork/test_projection_reconciliation.py`: one synthetic base plus one component at a time; every
  view read at the same seven dates, all accounts and checking only, cash and accrual mode. `MOVES` lists what each
  component should do; `LEFT_OUT` and `CASH_FLOW_BILL_DATE` list the kept differences above. `_projection_data.py` builds the components,
  `_projection_views.py` reads the views.
- `test_predictive_scheduled.py` (detected pattern against a pending row, switched-off account, overdue rows, card
  spending, refunds and bill payments, detector scope), `test_projection_rate_lookups.py`,
  `test_projection_reconciliation_postgres.py` (the harness on PostgreSQL),
  `test_budget_forecast_report.py` (scheduled and recurring refunds), `test_net_worth_order.py`,
  `frontend/src/fork/projection-consistency/upcoming-widget.test.tsx` (workspace date).
- `test_projection_table.py` prints the full view-by-date table when `FORK_F006_TABLE` is set (diagnostic only).
