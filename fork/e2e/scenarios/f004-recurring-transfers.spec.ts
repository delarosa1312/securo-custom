// F-004: a recurring transfer made on the screen moves both accounts and no income or expense (screen, REST,
// MCP); a cross-currency one without the amount received is refused; edits and cancelling go through MCP;
// transfers scheduled one by one convert into a recurring transfer only on request, with no number moving; and
// two generation runs at once write one pair.
import { expect, test } from '@playwright/test'

import { loginInBrowser, mcpClient, rest, restToken, saveEvidence, screenshot, stack } from '../lib/stack'

type Account = { id: string; name: string; currency: string }
type Recurring = {
  id: string
  description: string
  amount: string
  account_id: string
  destination_account_id: string | null
  destination_amount: string | null
  next_occurrence: string
  is_active: boolean
}
type Summary = {
  projected_income_primary: number
  projected_expenses_primary: number
  projected_balance_primary: number
}
type Projected = { recurring_id: string | null; account_id: string; type: string; amount: number }
type Series = { series_id: string; description: string; count: number; frequency: string; day_of_month: number }
type Transaction = { id: string; transfer_pair_id: string | null; type: string; account_id: string }

const CHECKING = 'Synthetic Checking'
const SAVINGS = 'Synthetic Savings'
const PLAN = 'Synthetic savings plan'
const TOP_UP = 'Synthetic reserve top-up'
const GENERATED = 'Synthetic generation check'

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function monthDay(offset: number, day: number): string {
  const now = new Date()
  return iso(new Date(now.getFullYear(), now.getMonth() + offset, day))
}
const monthOf = (date: string) => `${date.slice(0, 7)}-01`

async function restRaw(token: string, method: string, apiPath: string, body?: unknown) {
  const res = await fetch(`${stack.backend}${apiPath}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return { status: res.status, body: (await res.json().catch(() => null)) as unknown }
}

test('Recurring transfers: screen, REST, MCP, conversion and generation', async ({ page }) => {
  const token = await restToken()
  const accounts = await rest<Account[]>(token, 'GET', '/api/accounts')
  const byName = (name: string) => accounts.find((a) => a.name === name)!
  const checking = byName(CHECKING)
  const savings = byName(SAVINGS)
  const existing = await rest<Recurring[]>(token, 'GET', '/api/recurring-transactions')
  expect(existing.some((r) => r.description === PLAN), 'run on a freshly seeded stack').toBe(false)

  // The plan's first occurrence: the 10th on or after today, as the form's start date is today.
  const firstDay = new Date().getDate() <= 10 ? monthDay(0, 10) : monthDay(1, 10)
  const month = monthOf(firstDay)
  const summary = (ids?: string[]) =>
    rest<Summary>(token, 'GET', `/api/dashboard/summary?month=${month}${(ids ?? []).map((id) => `&account_ids=${id}`).join('')}`)
  const before = { all: await summary(), checking: await summary([checking.id]), savings: await summary([savings.id]) }

  // Screen: the recurring form as a transfer.
  await loginInBrowser(page)
  await page.goto('/recurring')
  await page.getByRole('button', { name: 'Add Recurring' }).first().click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Description').fill(PLAN)
  await dialog.getByLabel('Amount', { exact: true }).fill('200.00')
  await dialog.locator('select').filter({ has: page.locator('option[value="transfer"]') }).selectOption('transfer')
  await expect(dialog.getByText('Category', { exact: true })).toHaveCount(0)
  await dialog.getByLabel('From account').selectOption({ label: CHECKING })
  expect(await dialog.getByLabel('To account').locator('option').allTextContents()).not.toContain(CHECKING)
  await dialog.getByLabel('To account').selectOption({ label: SAVINGS })
  await dialog.locator('input[type="number"]').fill('10')
  await screenshot(page, 'f004.form.transfer')
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/api/recurring-transactions'))
  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  const response = await posted
  expect(response.status()).toBe(201)
  const created = (await response.json()) as Recurring
  saveEvidence('f004.screen.create.json', { request: response.request().postDataJSON(), response: created })
  expect(response.request().postDataJSON()).toMatchObject({
    type: 'debit', category_id: null, account_id: checking.id, destination_account_id: savings.id, amount: 200,
  })
  expect(created.next_occurrence).toBe(firstDay)
  const row = page.getByRole('row').filter({ hasText: PLAN })
  await expect(row.getByLabel(`${CHECKING} to ${SAVINGS}`)).toBeVisible()
  await screenshot(page, 'f004.list.transfer')

  // REST: both accounts move by 200, consolidated income, expenses and balance do not.
  const after = { all: await summary(), checking: await summary([checking.id]), savings: await summary([savings.id]) }
  const projected = await rest<Projected[]>(token, 'GET', `/api/dashboard/projected-transactions?month=${month}`)
  saveEvidence('f004.rest.projections.json', { before, after, projected })
  expect(after.all.projected_income_primary).toBeCloseTo(before.all.projected_income_primary, 2)
  expect(after.all.projected_expenses_primary).toBeCloseTo(before.all.projected_expenses_primary, 2)
  expect(after.all.projected_balance_primary).toBeCloseTo(before.all.projected_balance_primary, 2)
  expect(after.checking.projected_balance_primary).toBeCloseTo(before.checking.projected_balance_primary - 200, 2)
  expect(after.savings.projected_balance_primary).toBeCloseTo(before.savings.projected_balance_primary + 200, 2)
  expect(after.checking.projected_expenses_primary).toBeCloseTo(before.checking.projected_expenses_primary, 2)
  expect(after.savings.projected_income_primary).toBeCloseTo(before.savings.projected_income_primary, 2)
  const legs = projected.filter((p) => p.recurring_id === created.id).map((p) => [p.account_id, p.type, p.amount])
  expect(legs.sort()).toEqual([[checking.id, 'debit', 200], [savings.id, 'credit', 200]].sort())

  // Cross-currency without the amount received: refused with the reason.
  const euro = await rest<Account>(token, 'POST', '/api/accounts', {
    name: 'Synthetic Euro wallet', type: 'checking', balance: '0.00', currency: 'EUR',
  })
  const refused = await restRaw(token, 'POST', '/api/recurring-transactions', {
    description: 'Synthetic euro top-up', amount: '100.00', currency: checking.currency, type: 'debit',
    frequency: 'monthly', day_of_month: 15, start_date: monthDay(1, 15), account_id: checking.id,
    destination_account_id: euro.id,
  })
  saveEvidence('f004.rest.cross-currency-refused.json', refused)
  expect(refused.status).toBe(400)
  expect(JSON.stringify(refused.body)).toContain('amount received in the destination account')

  // MCP, as an external agent: list, a preview that writes nothing, an edit, cancelling.
  const mcp = await mcpClient(token)
  const listed = (await mcp.call('list_recurring_transactions')) as { items: Record<string, unknown>[] }
  expect(listed.items.find((r) => r.id === created.id)).toMatchObject({ account_id: checking.id, to_account_id: savings.id })
  const preview = (await mcp.call('propose_create_recurring_transfer', {
    description: 'Synthetic preview only', amount: 75, from_account_id: savings.id, to_account_id: checking.id,
    frequency: 'monthly', day_of_month: 20,
  })) as Record<string, unknown>
  expect(preview.kind).toBe('create_recurring_transfer')
  expect(preview).not.toHaveProperty('applied')
  const sameMcp = (await mcp.call('propose_create_recurring_transfer', {
    description: 'Synthetic loop', amount: 75, from_account_id: savings.id, to_account_id: savings.id, frequency: 'weekly',
  })) as { error?: string }
  expect(sameMcp.error).toContain('same account')
  const updated = (await mcp.call('propose_update_recurring_transfer', { recurring_id: created.id, amount: 250, apply: true })) as Record<string, unknown>
  expect(updated.applied).toBe(true)
  const afterEdit = await summary([checking.id])
  expect(afterEdit.projected_balance_primary).toBeCloseTo(before.checking.projected_balance_primary - 250, 2)
  const recurringNow = await rest<Recurring[]>(token, 'GET', '/api/recurring-transactions')
  expect(recurringNow.some((r) => r.description === 'Synthetic preview only')).toBe(false)

  // Conversion: three transfers scheduled one by one on the 20th of the next three months.
  for (const offset of [1, 2, 3]) {
    await rest(token, 'POST', '/api/transactions/transfer', {
      from_account_id: checking.id, to_account_id: savings.id, amount: '300.00', date: monthDay(offset, 20), description: TOP_UP,
    })
  }
  const range = `from=${monthDay(1, 1)}&to=${monthDay(4, 1)}`
  const beforeConversion = {
    checking: await rest(token, 'GET', `/api/transactions?account_id=${checking.id}&${range}&limit=500`),
    projected: await rest(token, 'GET', `/api/dashboard/projected-transactions?${range}`),
    summary: await rest(token, 'GET', `/api/dashboard/summary?month=${monthDay(2, 1)}`),
  }
  const candidates = await rest<{ series: Series[]; left_alone: number }>(token, 'GET', '/api/recurring-transactions/transfer-conversions')
  const series = candidates.series.find((s) => s.description === TOP_UP)!
  expect(series).toMatchObject({ count: 3, frequency: 'monthly', day_of_month: 20 })
  const viaMcp = (await mcp.call('list_transfer_conversions')) as { series: Series[] }
  expect(viaMcp.series.map((s) => s.series_id)).toEqual(candidates.series.map((s) => s.series_id))
  const convertPreview = (await mcp.call('propose_convert_transfer_series', { series_id: series.series_id })) as Record<string, unknown>
  expect(convertPreview.kind).toBe('convert_transfer_series')
  expect(convertPreview).not.toHaveProperty('applied')
  const stillThere = await rest<{ series: Series[] }>(token, 'GET', '/api/recurring-transactions/transfer-conversions')
  expect(stillThere.series.map((s) => s.series_id)).toContain(series.series_id)
  const converted = (await mcp.call('propose_convert_transfer_series', { series_id: series.series_id, apply: true })) as Record<string, unknown>
  expect(converted).toMatchObject({ applied: true, linked_transfers: 3 })
  const afterConversion = {
    checking: await rest(token, 'GET', `/api/transactions?account_id=${checking.id}&${range}&limit=500`),
    projected: await rest(token, 'GET', `/api/dashboard/projected-transactions?${range}`),
    summary: await rest(token, 'GET', `/api/dashboard/summary?month=${monthDay(2, 1)}`),
  }
  saveEvidence('f004.conversion.json', { candidates, convertPreview, converted, beforeConversion, afterConversion })
  expect(afterConversion.projected).toEqual(beforeConversion.projected)
  expect(afterConversion.summary).toEqual(beforeConversion.summary)
  const strip = (body: unknown) =>
    JSON.parse(JSON.stringify(body), (key, value) => (key === 'recurring_transaction_id' || key === 'updated_at' ? undefined : value))
  expect(strip(afterConversion.checking)).toEqual(strip(beforeConversion.checking))
  const again = await rest<{ series: Series[] }>(token, 'GET', '/api/recurring-transactions/transfer-conversions')
  expect(again.series.map((s) => s.series_id)).not.toContain(series.series_id)

  // Generation: a schedule due today, two Generate requests at once, one pair.
  const due = await rest<Recurring>(token, 'POST', '/api/recurring-transactions', {
    description: GENERATED, amount: '50.00', currency: checking.currency, type: 'debit', frequency: 'monthly',
    day_of_month: new Date().getDate(), start_date: iso(new Date()), account_id: checking.id,
    destination_account_id: savings.id,
  })
  const runs = await Promise.all([
    rest<{ generated: number }>(token, 'POST', '/api/recurring-transactions/generate'),
    rest<{ generated: number }>(token, 'POST', '/api/recurring-transactions/generate'),
  ])
  const rows = (await rest<{ items: Transaction[] }>(token, 'GET', `/api/transactions?q=${encodeURIComponent(GENERATED)}&limit=50`)).items
  saveEvidence('f004.generation.json', { due, runs, rows })
  expect(rows.map((r) => r.type).sort()).toEqual(['credit', 'debit'])
  expect(new Set(rows.map((r) => r.transfer_pair_id)).size).toBe(1)
  expect(rows[0].transfer_pair_id).not.toBeNull()
  expect(runs.reduce((sum, r) => sum + r.generated, 0)).toBeLessThanOrEqual(1)
  await rest(token, 'PATCH', `/api/recurring-transactions/${due.id}`, { is_active: false })

  // Cancelling through MCP deactivates it, as for any schedule.
  const beforeCancel = await summary([checking.id])
  const cancelled = (await mcp.call('propose_cancel_recurring_transaction', { recurring_id: created.id, apply: true })) as Record<string, unknown>
  expect(cancelled).toMatchObject({ applied: true, is_active: false })
  saveEvidence('f004.mcp.json', { listed, preview, sameMcp, updated, cancelled })
  expect((await summary([checking.id])).projected_balance_primary).toBeCloseTo(beforeCancel.projected_balance_primary + 250, 2)
  await page.goto('/recurring')
  await expect(page.getByRole('row').filter({ hasText: PLAN }).getByText('Inactive')).toBeVisible()
  await screenshot(page, 'f004.list.cancelled')
})
