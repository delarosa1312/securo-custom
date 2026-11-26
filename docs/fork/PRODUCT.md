# Product context

## Who and why

One household moving from Meu Dinheiro Web to self-hosted Securo for data ownership, a modern interface, read and
write MCP access and extensibility. Meu Dinheiro Web is a reference for expected financial behavior, not something
to clone. The main use is forward-looking planning, not only expense history.

Questions the product must answer:

- How much money is actually available to spend, leaving reserved funds out?
- What income, expenses and obligations fall in the coming months?
- Can a purchase be afforded without compromising later obligations?
- How do changes in salary, subscriptions, financing and living costs move future balances?
- How much can realistically be accumulated over 3, 6, 12 and 24 months?

Priorities, in order of weight: financial correctness, accurate future planning, little manual upkeep, consistent
reports, ease of use, MCP access, upstream compatibility, visual consistency.

## Financial terms

| Term | Meaning here |
| --- | --- |
| Account balance | What one account holds or owes now. Connected accounts: the provider figure. Manual: the ledger sum. |
| Total balance | All open accounts of every type (cards and loans negative), plus assets where the view says so. |
| Available balance | Money that can be spent now: open checking, savings and wallet accounts that are marked as included. Reserved accounts are left out of this figure only. |
| Reserved funds | Money set aside (an emergency fund, a savings pocket). It keeps its balance, history and net worth share. |
| Net worth | Everything owned minus everything owed. The available-balance setting never changes it. |
| Posted transaction | Money that actually moved. Part of balances and history. |
| Pending transaction | An expected payment that has not happened. Part of projections, not of the current balance. |
| Recurring schedule | A rule that produces future occurrences (salary, rent, a subscription, a transfer). |
| Installment | One payment of a purchase split in parts; stored as individual future rows. |
| Internal transfer | Money moving between the household's own accounts. Never income or expense in a consolidated view; it does move each account's balance. |
| Budget | A monthly spending target per category. Used to monitor and to forecast, not as an envelope that blocks spending. |
| Remaining budget | Budget for the month minus what is already posted and what is already projected for that category, never below zero. |
| Budget-derived forecast | The remaining budget shown as expected spending in a projection. Virtual: it never becomes a ledger row. |
| Exceptional expense | A one-off (a wedding, a renovation, a costly device, a reimbursed purchase). Stays in history, can be left out of the lifestyle baseline. |
| Discretionary baseline | The estimate of ordinary variable spending used when no budget covers it. |

The "Include in available balance" setting (F-001) is per account, on by default, and exists on checking,
savings and wallet accounts only. Switched off, the account leaves exactly these figures: the dashboard's
Available balance and its chips, the card widget's "left after paying" figure, the predictive forecast, and
`GET /api/accounts/available-balance` (MCP `get_available_balance`). In the predictive forecast everything about
the account leaves: its starting balance, its scheduled flows (manual and detected recurring) and its spending
history in the discretionary burn, so the runway counts down from available money only. Money moved between an
included account and a switched-off one counts only through a scheduled recurring rule (for example "move 500 to
the reserve" monthly on the current account, which leaves available on its day); transfers that exist only in
history are ignored, as upstream ignores internal moves. A regular saving into a switched-off account is
a recurring transfer (F-004) from the current account to the reserve. It stays in total
balance, net worth and its cash row, the Cash Flow report, the dashboard projection, account history and every
other report. Cards, loans and investments are never available money; closed accounts never count. Example:
current account 500, cash 100, reserve 1,000 switched off, card owing 300: available 600, after paying the card
200, total balance 1,300.

The budget-aware Cash Flow mode (F-002, "Include budgets") is off by default and changes only the Cash Flow
report (screen, `GET /api/reports/cash-flow?budget_forecast=true`, MCP `get_cash_flow`). Each budgeted category's
remaining budget is spread evenly per day (this month from tomorrow, later months over all their days) and
subtracted from the projected balance with all accounts; with an account or collection filter it is shown apart
as "Unallocated Budget". It excludes "Include estimate" and alone offers 24 months. Example: restaurant budget 250,
posted 100, pending 40 on the 25th, today the 20th of a 30-day month: 110 more spending, 11.00 a day from the 21st.
Transition from provision schedules: `docs/fork/guides/provisions-to-budgets.md`.

A recurring transfer (F-004) is a recurring schedule with a destination account: type "Transfer" in the recurring
form, `destination_account_id` on `POST` and `PATCH /api/recurring-transactions`, MCP
`propose_create_recurring_transfer` and `propose_update_recurring_transfer` (listing and cancelling are the
ordinary recurring tools). Each occurrence becomes one transfer pair, written whole or not at all and never
twice: posted on a manual account, pending on a bank-connected one, where the bank's own row is expected. It is
never income or expense: projected income and expenses, the Cash Flow report, budgets and spending leave it out,
while balances, Upcoming, the calendar and per-account views move both accounts. In the predictive forecast only
a transfer crossing the available boundary counts: into a switched-off account it leaves available money, out of
one it arrives. Both accounts must be open and in the workspace; a closed or moved account makes the occurrence
wait. Between currencies the schedule needs the amount received each time (`destination_amount`); no rate is
applied. Editing, deactivating, end dates and deleting work as for any schedule and never change transfers
already generated; a transfer whose account was closed can still be deactivated, end-dated or deleted. Known
limits: a bank or statement row for the same move is not matched to a generated leg, so it shows beside it until
the user deletes one; the dashboard balance chart moves with transfer schedules once F-006 (#8) lands. Example:
200 from Checking (1,000) to Savings (1,000) on the 10th of every month: on the 10th
Checking 800, Savings 1,200, total balance 2,000, monthly income and expenses unchanged.

Transfers already scheduled one by one are converted only on request, one series at a time.
`GET /api/recurring-transactions/transfer-conversions` (MCP `list_transfer_conversions`) groups future transfers
with the same accounts, amounts and description whose dates follow one frequency, and changes nothing;
`POST /api/recurring-transactions/transfer-conversions/{series_id}/apply` (MCP `propose_convert_transfer_series`)
creates the recurring transfer, ending at the series' last transfer unless `open_ended` or a later `end_date` is
given, and links those transfers to it as its occurrences. No balance or forecast moves.

Recurring lifecycle (F-005). One occurrence can be skipped (it is never recorded and leaves every projection;
undo puts it back), recorded now as scheduled, or recorded now with its own amount or date (the series keeps its
values). A change "from a date on" ends the series the day before and continues it with the new values, so
earlier months keep the old ones; pending and later rows already recorded follow the change, posted history never
does, and the confirmation lists both before anything is written. "Correct the whole series" changes it in place,
for a mistake. Ending a series on a last date cancels every occurrence after it; deactivating pauses it. Delete
moves the whole series to Deleted, where it generates, projects and matches nothing and can be restored as it
was (occurrences that fell while it was deleted are recorded as skipped); its recorded rows stay. Delete
permanently, only from Deleted, erases it with its skips and edits and keeps its rows without the link. Overdue
occurrences (dated today or earlier, never recorded, counted nowhere) are listed with Record, Skip and Link (to a
row typed by hand). Every action works for transfer schedules, both legs together (Link excepted), and has REST
routes under `/api/recurring-transactions/{id}/` (`occurrences`, `occurrences/{date}/skip|record|link`,
`change-from`, `end`, `trash`, `restore`, `purge`; `GET /api/recurring-transactions/overdue`; `?status=` on the
list) and MCP tools (`list_recurring_occurrences`, `list_overdue_occurrences`, `propose_skip_occurrence`,
`propose_record_occurrence`, `propose_link_occurrence`, `propose_change_recurring_from`,
`propose_end_recurring`, `propose_delete_recurring`, `propose_restore_recurring`). Example: rent 1,500 on the
10th; "from 1 March, 1,550": the series ends on 28 February and a new part starts on 10 March; January and
February stay 1,500 in every report and March on projects 1,550; a March rent already recorded ahead with its
own amount keeps it.

Three layers a projection may combine, each at most once per unit of spending:

1. Scheduled: pending rows, future-dated rows, installments, recurring occurrences.
2. Budget-derived: remaining budget of budgeted categories.
3. Historical: the discretionary baseline, only for spending that layers 1 and 2 do not cover.

## Requirements

Feature specifications live in the GitHub issues. The backlog:

| Id | Feature | Priority | Phase |
| --- | --- | --- | --- |
| F-001 | Independent available-balance setting per account | High | 1 |
| F-002 | Budget-aware cash-flow forecasting | Critical | 1 |
| F-003 | Configurable discretionary spending predictions | Medium | 3 |
| F-004 | Native recurring transfers | High | 2 |
| F-005 | Recurring transaction lifecycle (one occurrence, from a date, skip, restore) | High | 2 |
| F-006 | Projection consistency across dashboard, reports, upcoming and calendar | High | 2 |
| F-007 | Installment and pending payment lifecycle (investigation first) | Medium | 3 |

Every feature: REST and MCP access, English and pt-BR, upstream defaults preserved, synthetic fixtures only.

## Existing data

Production holds migrated accounts, balances, history, categories, groups, payees, rules, pending rows,
installments, transfers and native recurring schedules. Some individual future rows legitimately remain
(installments, transfers, one-time obligations). Recurring "provision" expenses for household and leisure spending
are a workaround for the missing budget-aware forecast.

No feature removes provisions, alters records or repeats the migration by itself. Where a feature replaces a
workaround, it ships a transition guide with a preview and the user applies it.

## Non-goals

- A new finance application, a redesign, or a rewrite of the reporting engine.
- Strict envelope budgeting.
- Opaque predictions: a forecast must be explainable line by line.
- Automatic conversion or deletion of production records.
- Changes to the migration tool (`securo-migration` is a separate project).
- Personal accounts, categories or amounts hardcoded anywhere.
