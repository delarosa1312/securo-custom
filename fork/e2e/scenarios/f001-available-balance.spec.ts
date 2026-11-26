// F-001: the savings account is switched off in the edit dialog; available figures leave it out on the screen,
// over REST and over MCP, totals and reports stay put, and switching it back on (through an MCP proposal) gives
// back exactly the untouched responses.
import { expect, test, type Page } from '@playwright/test'

import { loginInBrowser, mcpClient, rest, restToken, saveEvidence, screenshot } from '../lib/stack'

type Account = {
  id: string
  name: string
  type: string
  current_balance: number
  balance_primary: number | null
  include_in_available: boolean
}
type Available = {
  total_primary: number
  by_currency: Record<string, number>
  accounts: { id: string }[]
  excluded_accounts: { id: string; name: string }[]
}
type Forecast = { summary: { starting_liquid_balance: string; runway_days: number | null } }

const SAVINGS = 'Synthetic Savings'
const CHECKING = 'Synthetic Checking'
const cents = (value: number) => String(Math.round(Math.abs(value) * 100))
const digits = (text: string | null) => (text ?? '').replace(/\D/g, '')
const balance = (a: Account) => Number(a.balance_primary ?? a.current_balance)

// Totals, reports and history: none of these may move with the setting.
const unchangedReads = (savingsId: string) => [
  '/api/dashboard/summary',
  '/api/reports/net-worth',
  '/api/reports/cash-flow?months=6&interval=monthly',
  '/api/reports/income-expenses',
  `/api/accounts/${savingsId}/summary`,
  `/api/accounts/${savingsId}/balance-history`,
  '/api/transactions',
]

async function readAll(token: string, paths: string[]) {
  return Object.fromEntries(await Promise.all(paths.map(async (p) => [p, await rest(token, 'GET', p)] as const)))
}

function headline(page: Page) {
  return page.getByText('Available balance', { exact: true }).locator('xpath=following-sibling::p[1]')
}

test('Available balance setting: screen, REST and MCP', async ({ page }) => {
  const token = await restToken()
  let accounts = await rest<Account[]>(token, 'GET', '/api/accounts')
  const byName = (name: string) => accounts.find((a) => a.name === name)!
  const savingsId = byName(SAVINGS).id
  if (!byName(SAVINGS).include_in_available) {
    // A rerun after a failed run: start from the untouched state.
    await rest(token, 'PATCH', `/api/accounts/${savingsId}`, { include_in_available: true })
    accounts = await rest<Account[]>(token, 'GET', '/api/accounts')
  }
  const checking = balance(byName(CHECKING))
  const savings = balance(byName(SAVINGS))

  // Untouched: every liquid account counts, as upstream.
  const untouched = {
    available: await rest<Available>(token, 'GET', '/api/accounts/available-balance'),
    forecast: await rest<Forecast>(token, 'GET', '/api/forecast/cashflow?horizon_days=90'),
    reads: await readAll(token, unchangedReads(savingsId)),
  }
  saveEvidence('f001.untouched.rest.json', untouched)
  expect(untouched.available.total_primary).toBeCloseTo(checking + savings, 2)
  expect(untouched.available.excluded_accounts).toEqual([])
  expect(Number(untouched.forecast.summary.starting_liquid_balance)).toBeCloseTo(checking + savings, 2)

  // Screen: the switch in the edit dialog.
  await loginInBrowser(page)
  await page.goto('/dashboard')
  await expect(headline(page)).toBeVisible()
  expect(digits(await headline(page).textContent())).toBe(cents(checking + savings))
  await screenshot(page, 'f001.dashboard.untouched')

  await page.goto('/accounts')
  const row = page
    .locator('div', { has: page.getByText(SAVINGS, { exact: true }) })
    .filter({ has: page.getByRole('button', { name: 'Edit', exact: true }) })
    .last()
  await row.getByRole('button', { name: 'Edit', exact: true }).click()
  const dialog = page.getByRole('dialog')
  const toggle = dialog.getByRole('switch', { name: 'Include in available balance' })
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await screenshot(page, 'f001.dialog.switched-off')
  const patched = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes(`/api/accounts/${savingsId}`))
  await dialog.getByRole('button', { name: 'Save', exact: true }).click()
  const patchResponse = await patched
  expect(patchResponse.status()).toBe(200)
  saveEvidence('f001.screen.patch.json', { request: patchResponse.request().postDataJSON(), response: await patchResponse.json() })
  expect(patchResponse.request().postDataJSON()).toMatchObject({ include_in_available: false })
  await expect(row.getByText(/Not in available balance/)).toBeVisible()
  await expect(page.getByText(/Not in available balance/)).toHaveCount(1)
  await screenshot(page, 'f001.accounts.marker')

  // REST after the switch.
  const off = {
    account: await rest<Account>(token, 'GET', `/api/accounts/${savingsId}`),
    available: await rest<Available>(token, 'GET', '/api/accounts/available-balance'),
    forecast: await rest<Forecast>(token, 'GET', '/api/forecast/cashflow?horizon_days=90'),
    reads: await readAll(token, unchangedReads(savingsId)),
  }
  saveEvidence('f001.off.rest.json', off)
  expect(off.account.include_in_available).toBe(false)
  expect(off.available.total_primary).toBeCloseTo(checking, 2)
  expect(off.available.excluded_accounts.map((a) => a.name)).toEqual([SAVINGS])
  expect(Number(off.forecast.summary.starting_liquid_balance)).toBeCloseTo(checking, 2)
  for (const [path, body] of Object.entries(untouched.reads)) expect(off.reads[path], path).toEqual(body)

  // Screen: the dashboard headline and its chips.
  await page.goto('/dashboard')
  await expect(headline(page)).toBeVisible()
  expect(digits(await headline(page).textContent())).toBe(cents(checking))
  const availableBlock = page.getByText('Available balance', { exact: true }).locator('..')
  await expect(availableBlock.getByRole('link', { name: new RegExp(CHECKING) })).toHaveCount(1)
  await expect(availableBlock.getByRole('link', { name: new RegExp(SAVINGS) })).toHaveCount(0)
  await screenshot(page, 'f001.dashboard.switched-off')

  // MCP, as an external agent.
  const mcp = await mcpClient(token)
  const listed = (await mcp.call('list_accounts')) as { items: (Account & { name: string })[] }
  expect(listed.items.find((a) => a.name === SAVINGS)?.include_in_available).toBe(false)
  expect(listed.items.find((a) => a.name === CHECKING)?.include_in_available).toBe(true)
  const viaMcp = (await mcp.call('get_available_balance')) as { available_balance: number; by_currency: Record<string, number> }
  expect(viaMcp.available_balance).toBe(off.available.total_primary)
  expect(viaMcp.by_currency).toEqual(off.available.by_currency)

  const preview = (await mcp.call('propose_set_account_available', { account_id: savingsId, include_in_available: true })) as Record<string, unknown>
  expect(preview.kind).toBe('set_account_available')
  expect(preview.changes).toEqual({ include_in_available: true })
  expect(preview).not.toHaveProperty('applied')
  expect((await rest<Account>(token, 'GET', `/api/accounts/${savingsId}`)).include_in_available).toBe(false)
  const card = accounts.find((a) => a.type === 'credit_card')!
  const refused = (await mcp.call('propose_set_account_available', { account_id: card.id, include_in_available: false })) as { error?: string }
  expect(refused.error).toContain('never part of the available balance')

  const applied = (await mcp.call('propose_set_account_available', { account_id: savingsId, include_in_available: true, apply: true })) as Record<string, unknown>
  expect(applied.applied).toBe(true)
  saveEvidence('f001.mcp.json', { listed, viaMcp, preview, refused, applied })

  // Switched back on: the responses equal the untouched ones.
  const back = {
    available: await rest<Available>(token, 'GET', '/api/accounts/available-balance'),
    forecast: await rest<Forecast>(token, 'GET', '/api/forecast/cashflow?horizon_days=90'),
    reads: await readAll(token, unchangedReads(savingsId)),
  }
  saveEvidence('f001.back-on.rest.json', back)
  expect(back.available).toEqual(untouched.available)
  expect(back.forecast).toEqual(untouched.forecast)
  for (const [path, body] of Object.entries(untouched.reads)) expect(back.reads[path], path).toEqual(body)
  await page.goto('/dashboard')
  await expect(headline(page)).toBeVisible()
  expect(digits(await headline(page).textContent())).toBe(cents(checking + savings))
})
