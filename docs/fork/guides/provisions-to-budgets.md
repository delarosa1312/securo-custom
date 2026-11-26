# From provision schedules to budgets

A "provision" is a recurring expense that does not pay anyone: a monthly "Groceries 1,800" or "Leisure 600"
schedule kept only so the Cash Flow forecast counts spending that is never scheduled. With the budget-aware mode
of the Cash Flow report (issue #5) a monthly budget does that job: what is left of it each month is counted as
spending, and it shrinks by itself as real spending is posted.

This guide moves from provisions to budgets one category at a time, comparing the forecast before and after each
step. Nothing here is automatic: Securo never changes, pauses or deletes a schedule or a budget for you.

## How the budget mode counts spending

For each budgeted category and month:

    remaining = budget - posted - scheduled, never below zero

- **Posted**: spending already booked this month, net of refunds in the same category (as on the Budgets page).
- **Scheduled**: pending rows, future-dated rows (installments) and recurring occurrences in that category and
  month. A provision schedule is one of these.
- **Remaining** is spread evenly per day: this month over the days left, starting tomorrow; a later month over
  all its days. Weekly and monthly views are sums of those days, to the cent.
- With **all accounts** it is subtracted from the projected balance ("Budgeted Spending"). With an account or
  collection filter it is shown apart as "Unallocated Budget" and not subtracted, because a budget has no account.
- It cannot be combined with "Include estimate" yet, and only this mode offers the 24 month horizon.
- Budgets on categories that never count as spending (ignored, or treated as transfers) are left out.

Because a provision in a budgeted category counts as scheduled, a budget and a provision of the same amount never
add up twice: during the transition the forecast does not move.

## Worked example (synthetic numbers)

Today is the 20th of a 30-day month. A "Groceries provision" schedule takes 1,800 on the 25th of every month.
Groceries actually posted this month: 1,500.

| Step | Groceries budget | Provision | Counted for the rest of this month | Each later month |
| --- | --- | --- | --- | --- |
| Before (mode off) | none | active | 1,800 on the 25th | 1,800 on the 25th |
| 1. Budget of 1,800, mode on | 1,800 | active | 1,800 on the 25th (scheduled; remaining 0) | 1,800 on the 25th (remaining 0) |
| 2. Provision set to Inactive | 1,800 | inactive | 300 (1,800 - 1,500), 30.00 a day | 1,800 spread evenly, 58.06 or 58.07 a day in a 31-day month |

Step 1 changes nothing: the provision counts as scheduled and fills the budget, so the budget adds nothing on
top. Step 2 is where the forecast becomes right: the 1,500 already spent is no longer forecast a second time, and
later months spend evenly instead of in one lump.

If a provision already created a real row this month and you kept it, that row counts as posted spending.

## Steps

1. **List the provisions.** On Recurring, note each schedule that only stands in for spending: its category,
   amount and frequency. Leave schedules that pay someone (rent, subscriptions, installments) alone: they are real
   scheduled payments and keep counting as such.
2. **Write down the forecast before.** Reports, Cash Flow, all accounts, 12M, monthly: note the projected balance
   of each month (or keep the REST response from the commands below).
3. **Create the budgets.** On Budgets, give each provision's category a monthly budget, recurring, with the amount
   you expect to spend (often the provision's amount). Use "Suggest budgets" if you prefer your history's average.
   A provision on a category that groups several kinds of spending may become budgets on two or three categories.
4. **Turn on "Include budgets"** on the Cash Flow tab. With each budget equal to its provision, every month's
   projected balance should match step 2: the provisions still fill their budgets. A budget larger than its
   provision adds the difference. The panel "Where budgeted spending comes from" shows, per month and category, the budget, posted, scheduled,
   remaining and the amount in the forecast.
5. **Deactivate one provision by hand.** Edit the schedule on Recurring and set it to Inactive (keep it for now
   rather than deleting it, so you can switch it back). In the panel its amount moves from Scheduled to Remaining
   for later months, and this month counts only what is left after the posted spending.
6. **Compare and repeat.** Check the month-end balances against step 2, then continue with the next provision.
   When all are inactive and the forecast looks right for a month or two, delete the inactive schedules if you
   want; past rows they created stay in history either way.

To undo at any step: set the provision back to Active, or delete the budget, or turn "Include budgets" off.

## The same comparison through REST and MCP

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Workspace-Id: $WORKSPACE" \
  "$SECURO/api/reports/cash-flow?months=12&interval=monthly"
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Workspace-Id: $WORKSPACE" \
  "$SECURO/api/reports/cash-flow?months=12&interval=monthly&budget_forecast=true"
```

An access token needs `reports:read` and, for the second call, `budgets:read`. The response adds a
`budgetExpenses` summary figure, `budget_outflow` on each trend point and a `budget_forecast` block with the per
month and category explanation. With `account_ids` the figure is `unallocatedBudget` and the points are left as
they are, since the budget is not part of those accounts' flows. Over MCP, `get_cash_flow` takes
`budget_forecast: true` and returns the same body.

## Known limits

- An unmaterialized recurring occurrence dated earlier this month (a schedule that was due but never generated)
  counts as scheduled on the Budgets page but is not in the Cash Flow forecast, so that amount is in neither.
  Generate or post it to fix the forecast. Issue #8 works on these differences.
- In accrual mode the Cash Flow places recurring card purchases on their purchase date while budgets count them
  in the bill's month; at the last month of the horizon this can count one card cycle of those schedules twice.
- A scheduled refund (a pending or recurring credit) in a budgeted category does not free budget until it posts:
  budgets count scheduled debits only. While it is pending the forecast counts the refund as income and the
  budget as still to be spent, so the projected end balance drops by the refund's amount on the day it posts.
  Issue #8 will count scheduled credits as negative scheduled spending, on the Budgets page too.
- Spending you share with someone in a group counts at your share, as on the Budgets page, while the account
  still moves by the full amount you paid.
