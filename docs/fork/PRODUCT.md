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
therefore represented by a recurring rule until native recurring transfers arrive with F-004 (#6). It stays in total
balance, net worth and its cash row, the Cash Flow report, the dashboard projection, account history and every
other report. Cards, loans and investments are never available money; closed accounts never count. Example:
current account 500, cash 100, reserve 1,000 switched off, card owing 300: available 600, after paying the card
200, total balance 1,300.

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
