// F-005: one occurrence at a time (record an overdue one, skip, edit one), a change from a date on, ending,
// deleting, restoring and deleting permanently, on the screen, over REST and over MCP; a transfer schedule's
// occurrence skipped and recorded with both legs.
import { expect, test, type Page } from '@playwright/test'

import { loginInBrowser, mcpClient, rest, restToken, saveEvidence, screenshot as capture } from '../lib/stack'

type Account = { id: string; name: string; currency: string }
type Recurring = {
  id: string
  description: string
  amount: string
  next_occurrence: string
  end_date: string | null
  is_active: boolean
  predecessor_id: string | null
  deleted_at: string | null
}
type Overdue = { recurring_id: string; nominal_date: string; date: string }
type Occurrence = { nominal_date: string; date: string; amount: string; state: string; transaction_id: string | null }
type Projected = { recurring_id: string | null; date: string; amount: number; type: string; account_id: string }
type Transaction = { id: string; date: string; amount: string; type: string; recurring_transaction_id: string | null; status: string }

const CHECKING = 'Synthetic Checking'
const SAVINGS = 'Synthetic Savings'
const RENT = 'Synthetic F-005 rent'
const GYM = 'Synthetic F-005 gym'
const PLAN = 'Synthetic F-005 savings plan'

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function monthDay(offset: number, day: number): string {
  const now = new Date()
  return iso(new Date(now.getFullYear(), now.getMonth() + offset, day))
}
const today = iso(new Date())

/** Dialogs and menus fade in and out: capture once every animation has finished. */
async function screenshot(page: Page, name: string) {
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running'))
  await capture(page, name)
}

async function rowMenu(page: Page, description: string, item: string) {
  await page.getByRole('button', { name: `Actions for ${description}` }).first().click()
  await page.getByRole('menuitem', { name: item, exact: true }).click()
}

test('Recurring lifecycle: occurrences, change from a date, end, delete, restore, MCP, transfers', async ({ page }) => {
  // The scenario needs the 10th of last month and of this month behind today.
  test.skip(new Date().getDate() <= 10, 'needs a date after the 10th')
  const token = await restToken()
  const accounts = await rest<Account[]>(token, 'GET', '/api/accounts')
  const checking = accounts.find((a) => a.name === CHECKING)!
  const savings = accounts.find((a) => a.name === SAVINGS)!
  const existing = await rest<Recurring[]>(token, 'GET', '/api/recurring-transactions?status=all')
  expect(existing.some((r) => r.description === RENT), 'run on a freshly seeded stack').toBe(false)

  const schedule = (body: Record<string, unknown>) =>
    rest<Recurring>(token, 'POST', '/api/recurring-transactions', {
      currency: checking.currency, type: 'debit', frequency: 'monthly', account_id: checking.id, auto_generate: false,
      ...body,
    })
  const rent = await schedule({ description: RENT, amount: '1500.00', day_of_month: 10, start_date: monthDay(-1, 10) })
  const gym = await schedule({ description: GYM, amount: '90.00', day_of_month: 5, start_date: monthDay(-1, 5) })
  for (const day of [monthDay(-1, 5), monthDay(0, 5)]) {
    await rest(token, 'POST', `/api/recurring-transactions/${gym.id}/occurrences/${day}/record`, {})
  }

  // Q12: the rent's 10ths behind today are overdue and nowhere in this month's projections.
  const overdue = await rest<Overdue[]>(token, 'GET', '/api/recurring-transactions/overdue')
  expect(overdue.filter((o) => o.recurring_id === rent.id).map((o) => o.nominal_date)).toEqual([monthDay(-1, 10), monthDay(0, 10)])
  const thisMonth = await rest<Projected[]>(token, 'GET', `/api/dashboard/projected-transactions?month=${monthDay(0, 1)}`)
  expect(thisMonth.filter((p) => p.recurring_id === rent.id)).toEqual([])
  const mcp = await mcpClient(token)
  const mcpOverdue = (await mcp.call('list_overdue_occurrences')) as { items: { recurring_id: string; nominal_date: string }[] }
  expect(mcpOverdue.items.filter((o) => o.recurring_id === rent.id).map((o) => o.nominal_date)).toEqual([monthDay(-1, 10), monthDay(0, 10)])

  // Screen: tabs and the overdue section; record last month's rent, skip this month's.
  await loginInBrowser(page)
  await page.goto('/recurring')
  const section = page.getByTestId('overdue-section')
  await expect(section).toContainText('overdue occurrences')
  await expect(page.getByTestId('recurring-tab-active')).toHaveAttribute('data-state', 'active')
  await screenshot(page, 'f005.list.tabs-overdue')
  const rentRows = section.getByTestId('overdue-row').filter({ hasText: RENT })
  await expect(rentRows).toHaveCount(2)
  await rentRows.first().getByRole('button', { name: 'Record' }).click()
  const record = page.getByTestId('record-occurrence-dialog')
  await expect(record).toBeVisible()
  await screenshot(page, 'f005.dialog.record-overdue')
  await record.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(record).toHaveCount(0)
  await expect(rentRows).toHaveCount(1)
  await rentRows.first().getByRole('button', { name: 'Skip' }).click()
  await expect(rentRows).toHaveCount(0)
  let occurrences = await rest<Occurrence[]>(token, 'GET', `/api/recurring-transactions/${rent.id}/occurrences?from=${monthDay(-1, 1)}&to=${monthDay(3, 1)}`)
  expect(occurrences.slice(0, 3).map((o) => [o.nominal_date, o.state])).toEqual([
    [monthDay(-1, 10), 'recorded'], [monthDay(0, 10), 'skipped'], [monthDay(1, 10), 'scheduled'],
  ])
  const recorded = occurrences[0]
  expect(recorded.date).toBe(monthDay(-1, 10))

  // The Occurrences dialog from the row menu: edit next-but-one month's rent on its own (recorded ahead).
  await rowMenu(page, RENT, 'Occurrences')
  const dialog = page.getByTestId('occurrences-dialog')
  await expect(dialog.getByTestId('occurrence-row').first()).toBeVisible()
  await screenshot(page, 'f005.dialog.occurrences')
  const later = dialog.getByTestId('occurrence-row').filter({ hasText: 'Scheduled' }).nth(1)
  await later.getByRole('button', { name: 'Edit' }).click()
  const edit = page.getByTestId('record-occurrence-dialog')
  await edit.getByLabel('Amount').fill('1650.00')
  await edit.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(edit).toHaveCount(0)
  await expect(dialog.getByText('Edited')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  // Edit the amount: "Apply the change from when?", from the next occurrence on.
  await rowMenu(page, RENT, 'Edit')
  const form = page.getByRole('dialog')
  await form.getByLabel('Amount', { exact: true }).fill('1600.00')
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  const scope = page.getByTestId('change-from-dialog')
  await expect(scope.getByTestId('change-impact')).toContainText('keeps its values')
  await screenshot(page, 'f005.dialog.change-from')
  const applied = page.waitForResponse((r) => r.url().endsWith(`/${rent.id}/change-from`) && r.request().method() === 'POST'
    && r.request().postDataJSON().preview === false)
  await scope.getByTestId('change-apply').click()
  const change = await (await applied).json()
  saveEvidence('f005.screen.change-from.json', change)
  await expect(scope).toHaveCount(0)
  const successor = change.schedule as Recurring
  expect(successor).toMatchObject({ predecessor_id: rent.id, amount: '1600.00', next_occurrence: monthDay(1, 10) })
  await expect(page.getByTestId('series-since')).toBeVisible()
  await screenshot(page, 'f005.list.since')
  const projected = await rest<Projected[]>(token, 'GET', `/api/dashboard/projected-transactions?from=${monthDay(1, 1)}&to=${monthDay(4, 1)}`)
  const rentProjected = projected.filter((p) => p.recurring_id === successor.id).map((p) => [p.date, p.amount])
  expect(rentProjected).toEqual([[monthDay(1, 10), 1600], [monthDay(3, 10), 1600]])
  const oldRow = await rest<Transaction>(token, 'GET', `/api/transactions/${recorded.transaction_id}`)
  expect(oldRow).toMatchObject({ amount: '1500.00', recurring_transaction_id: rent.id })

  // End series (the gym): last occurrence today; nothing after it, Inactive tab.
  await rowMenu(page, GYM, 'End series...')
  const end = page.getByTestId('series-end-dialog')
  await expect(end.getByText(/Last occurrence on/).first()).toBeVisible()
  await screenshot(page, 'f005.dialog.end')
  await end.getByTestId('series-confirm').click()
  await expect(end).toHaveCount(0)
  await page.getByTestId('recurring-tab-inactive').click()
  await expect(page.getByRole('row').filter({ hasText: GYM })).toBeVisible()
  const gymNow = (await rest<Recurring[]>(token, 'GET', '/api/recurring-transactions?status=inactive')).find((r) => r.id === gym.id)
  expect(gymNow).toMatchObject({ end_date: today, is_active: false })
  await page.getByTestId('recurring-tab-active').click()

  // Delete: to Deleted, the scheduled row recorded ahead deleted too (ticked by default), history kept.
  await rowMenu(page, RENT, 'Delete')
  const trash = page.getByTestId('series-delete-dialog')
  await expect(trash.getByTestId('delete-scheduled')).toBeChecked()
  await screenshot(page, 'f005.dialog.delete')
  await trash.getByTestId('series-confirm').click()
  await expect(trash).toHaveCount(0)
  await page.getByTestId('recurring-tab-deleted').click()
  await expect(page.getByRole('row').filter({ hasText: RENT })).toHaveCount(2)
  await screenshot(page, 'f005.list.deleted')
  const deleted = await rest<Recurring[]>(token, 'GET', '/api/recurring-transactions?status=deleted')
  expect(deleted.map((r) => r.id).sort()).toEqual([rent.id, successor.id].sort())
  const afterDelete = await rest<Projected[]>(token, 'GET', `/api/dashboard/projected-transactions?from=${monthDay(1, 1)}&to=${monthDay(4, 1)}`)
  expect(afterDelete.filter((p) => p.recurring_id === successor.id)).toEqual([])
  expect((await rest<Transaction>(token, 'GET', `/api/transactions/${recorded.transaction_id}`)).recurring_transaction_id).toBe(rent.id)

  // Restore: back as it was, next occurrence named.
  await rowMenu(page, RENT, 'Restore')
  const restore = page.getByTestId('series-restore-dialog')
  await expect(restore.getByText(/The next one is/)).toBeVisible()
  await screenshot(page, 'f005.dialog.restore')
  await restore.getByTestId('series-confirm').click()
  await expect(restore).toHaveCount(0)
  await page.getByTestId('recurring-tab-active').click()
  await expect(page.getByRole('row').filter({ hasText: RENT })).toHaveCount(1)
  occurrences = await rest<Occurrence[]>(token, 'GET', `/api/recurring-transactions/${successor.id}/occurrences?from=${monthDay(1, 1)}&to=${monthDay(3, 1)}`)
  expect(occurrences.map((o) => [o.nominal_date, o.state])).toEqual([[monthDay(1, 10), 'scheduled'], [monthDay(2, 10), 'scheduled']])

  // MCP: a preview writes nothing; delete to Deleted, then permanently, from the chat.
  const preview = (await mcp.call('propose_delete_recurring', { recurring_id: successor.id })) as Record<string, unknown>
  expect(preview).toMatchObject({ kind: 'delete_recurring', mode: 'recoverable' })
  expect(preview).not.toHaveProperty('applied')
  expect((await rest<Recurring[]>(token, 'GET', '/api/recurring-transactions?status=deleted')).some((r) => r.id === rent.id)).toBe(false)
  const viaMcp = (await mcp.call('propose_delete_recurring', { recurring_id: successor.id, apply: true })) as Record<string, unknown>
  expect(viaMcp.applied).toBe(true)

  // Delete permanently from Deleted: the series goes, its recorded row stays without the link.
  await page.reload()
  await page.getByTestId('recurring-tab-deleted').click()
  await rowMenu(page, RENT, 'Delete permanently')
  const purge = page.getByTestId('series-purge-dialog')
  await expect(purge).toContainText('1 recorded transaction stays')
  await screenshot(page, 'f005.dialog.purge')
  await purge.getByTestId('series-confirm').click()
  await expect(purge).toHaveCount(0)
  const all = await rest<Recurring[]>(token, 'GET', '/api/recurring-transactions?status=all')
  expect(all.some((r) => r.description === RENT)).toBe(false)
  expect(await rest<Transaction>(token, 'GET', `/api/transactions/${recorded.transaction_id}`)).toMatchObject({
    amount: '1500.00', recurring_transaction_id: null,
  })

  // Transfer schedule: skip one occurrence (both legs leave the projection), record the next as its pair.
  const plan = await schedule({
    description: PLAN, amount: '200.00', day_of_month: 15, start_date: monthDay(1, 15), destination_account_id: savings.id,
  })
  const skipPreview = (await mcp.call('propose_skip_occurrence', { recurring_id: plan.id, nominal_date: monthDay(1, 15) })) as Record<string, unknown>
  expect(skipPreview).toMatchObject({ kind: 'skip_occurrence' })
  const skipped = (await mcp.call('propose_skip_occurrence', { recurring_id: plan.id, nominal_date: monthDay(1, 15), apply: true })) as Record<string, unknown>
  expect(skipped.applied).toBe(true)
  const planProjected = (await rest<Projected[]>(token, 'GET', `/api/dashboard/projected-transactions?from=${monthDay(1, 1)}&to=${monthDay(3, 1)}`))
    .filter((p) => p.recurring_id === plan.id).map((p) => [p.date, p.type, p.account_id])
  expect(planProjected.sort()).toEqual([[monthDay(2, 15), 'credit', savings.id], [monthDay(2, 15), 'debit', checking.id]].sort())
  const pair = await rest<{ transaction_id: string }>(token, 'POST', `/api/recurring-transactions/${plan.id}/occurrences/${monthDay(2, 15)}/record`, {})
  const legs = (await rest<{ items: Transaction[] }>(token, 'GET', `/api/transactions?q=${encodeURIComponent(PLAN)}&limit=50`)).items
  expect(legs.map((l) => l.type).sort()).toEqual(['credit', 'debit'])
  saveEvidence('f005.rest-mcp.json', { overdue, mcpOverdue, occurrences, projected, deleted, preview, viaMcp, skipPreview, skipped, planProjected, pair, legs })
})
