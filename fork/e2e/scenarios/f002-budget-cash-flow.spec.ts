// F-002 (#5): the budget-aware Cash Flow mode on the seeded stack, through the screen, REST and MCP.
// Every expected figure is derived from the Budgets page's own endpoint (`/api/budgets/comparison`), so the
// report and the Budgets page are checked against each other, not against numbers copied into this file.
import { expect, test, type Page } from '@playwright/test'

import { loginInBrowser, mcpClient, rest, restToken, saveEvidence, screenshot } from '../lib/stack'

type Breakdown = { key: string; value: number }
type Point = { date: string; value: number; breakdowns: Record<string, number> }
type Category = { name: string; budget: string; posted: string; scheduled: string; remaining: string; forecast: string }
type Month = {
  month: string; spread_from: string | null; spread_to: string | null; days: number
  remaining: string; forecast: string; categories: Category[]
}
type Report = {
  summary: { primary_value: number; breakdowns: Breakdown[] }
  trend: Point[]
  meta: Record<string, unknown>
  budget_forecast?: { placement: string; rule: string; total: string; months: Month[] }
}
type Comparison = { category_name: string; budget_amount: string | null; actual_amount: string; projected_amount: string }

const cents = (value: number | string) => Math.round(Number(value) * 100)
const figure = (report: Report, key: string) => report.summary.breakdowns.find((b) => b.key === key)?.value
const sumBudget = (report: Report) => report.trend.reduce((acc, p) => acc + cents(p.breakdowns.budget_outflow ?? 0), 0)

async function settle(page: Page) {
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1500) // Recharts animation
}

test('F-002 budget-aware Cash Flow: REST, MCP, Budgets page and screen agree', async ({ page }) => {
  test.setTimeout(180_000)
  const token = await restToken()
  const today = new Date()
  const monthKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
  const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate()
  const daysLeft = daysInMonth - today.getDate()

  // 1. Mode off is upstream's response: no budget fields anywhere.
  const off = await rest<Report>(token, 'GET', '/api/reports/cash-flow?months=6&interval=monthly')
  saveEvidence('f002-off.rest.json', off)
  expect(off.budget_forecast).toBeUndefined()
  expect(off.trend.every((p) => !('budget_outflow' in p.breakdowns))).toBe(true)
  expect(figure(off, 'budgetExpenses')).toBeUndefined()

  // 2. Mode on, all accounts: subtracted, and the end balance moves by exactly the budget total.
  const on = await rest<Report>(token, 'GET', '/api/reports/cash-flow?months=6&interval=monthly&budget_forecast=true')
  saveEvidence('f002-on.rest.json', on)
  const plan = on.budget_forecast!
  expect(plan.placement).toBe('subtracted')
  expect(plan.rule).toBe('even_daily')
  expect(cents(plan.total)).toBeGreaterThan(0)
  expect(sumBudget(on)).toBe(cents(plan.total))
  expect(cents(figure(on, 'budgetExpenses')!)).toBe(cents(plan.total))
  expect(cents(figure(off, 'endingBalance')!) - cents(figure(on, 'endingBalance')!)).toBe(cents(plan.total))
  expect(on.trend.map((p) => p.date)).toEqual(off.trend.map((p) => p.date))

  // 3. The current month agrees with the Budgets page: remaining = budget - projected, floored at zero.
  const comparison = await rest<Comparison[]>(token, 'GET', `/api/budgets/comparison?month=${monthKey}-01`)
  saveEvidence('f002-budgets-comparison.rest.json', comparison)
  const current = plan.months.find((m) => m.month === monthKey)!
  expect(current.days).toBe(daysLeft)
  for (const row of comparison.filter((r) => r.budget_amount !== null)) {
    const shown = current.categories.find((c) => c.name === row.category_name)
    if (!shown) continue // ignored or transfer-like categories are left out on purpose
    expect(cents(shown.budget), row.category_name).toBe(cents(row.budget_amount!))
    expect(cents(shown.posted), row.category_name).toBe(cents(row.actual_amount))
    expect(cents(shown.remaining), row.category_name).toBe(Math.max(cents(row.budget_amount!) - cents(row.projected_amount), 0))
    expect(cents(shown.forecast), row.category_name).toBe(daysLeft > 0 ? cents(shown.remaining) : 0)
  }
  expect(current.categories.length).toBeGreaterThan(0)

  // 4. Daily and weekly views are sums of the same days: the totals match to the cent.
  for (const interval of ['daily', 'weekly']) {
    const view = await rest<Report>(token, 'GET', `/api/reports/cash-flow?months=6&interval=${interval}&budget_forecast=true`)
    saveEvidence(`f002-on-${interval}.rest.json`, { total: view.budget_forecast!.total, points: view.trend.length })
    expect(sumBudget(view), interval).toBe(cents(plan.total))
    expect(view.budget_forecast).toEqual(plan)
  }

  // 5. Horizons: 3 and 24 months with the mode; 24 without it and baseline with it are refused.
  const three = await rest<Report>(token, 'GET', '/api/reports/cash-flow?months=3&interval=monthly&budget_forecast=true')
  const long = await rest<Report>(token, 'GET', '/api/reports/cash-flow?months=24&interval=monthly&budget_forecast=true')
  saveEvidence('f002-on-24m.rest.json', long)
  expect(three.budget_forecast!.months).toHaveLength(4)
  expect(long.budget_forecast!.months).toHaveLength(25)
  expect(sumBudget(long)).toBe(cents(long.budget_forecast!.total))
  const refused = async (query: string) => {
    const res = await fetch(`http://127.0.0.1:${process.env.FORK_STACK_BACKEND_PORT ?? 18000}/api/reports/cash-flow?${query}`,
      { headers: { Authorization: `Bearer ${token}` } })
    return { status: res.status, body: await res.json() }
  }
  const noMode24 = await refused('months=24')
  const withBaseline = await refused('months=6&budget_forecast=true&baseline=true')
  saveEvidence('f002-refusals.rest.json', { noMode24, withBaseline })
  expect(noMode24.status).toBe(422)
  expect(withBaseline.status).toBe(422)

  // 6. MCP returns the same forecast and explanation as REST, on and off.
  const mcp = await mcpClient(token)
  const viaMcp = (await mcp.call('get_cash_flow', { months: 6, interval: 'monthly', budget_forecast: true })) as Report
  const viaMcpOff = (await mcp.call('get_cash_flow', { months: 6, interval: 'monthly' })) as Report
  saveEvidence('f002-on.mcp.json', viaMcp)
  expect(viaMcp.budget_forecast).toEqual(plan)
  expect(viaMcp.trend).toEqual(on.trend)
  expect(viaMcp.summary).toEqual(on.summary)
  expect(viaMcpOff.trend).toEqual(off.trend)
  expect(viaMcpOff.budget_forecast).toBeUndefined()

  // 7. An account filter (checking only) shows the budget apart and does not subtract it.
  const accounts = await rest<{ id: string; name: string }[]>(token, 'GET', '/api/accounts')
  const checking = accounts.find((a) => a.name === 'Synthetic Checking')!
  const filteredOff = await rest<Report>(token, 'GET', `/api/reports/cash-flow?months=6&interval=monthly&account_ids=${checking.id}`)
  const filteredOn = await rest<Report>(token, 'GET', `/api/reports/cash-flow?months=6&interval=monthly&account_ids=${checking.id}&budget_forecast=true`)
  saveEvidence('f002-filtered-on.rest.json', filteredOn)
  expect(filteredOn.budget_forecast!.placement).toBe('unallocated')
  expect(figure(filteredOn, 'unallocatedBudget')).toBe(figure(on, 'budgetExpenses'))
  expect(filteredOn.trend.map((p) => p.value)).toEqual(filteredOff.trend.map((p) => p.value))
  expect(filteredOn.trend.map((p) => p.breakdowns)).toEqual(filteredOff.trend.map((p) => p.breakdowns))

  // 8. A new posted expense moves from "remaining" into the balance; the end balance does not change.
  const restaurants = current.categories.find((c) => c.name === 'Restaurants')!
  const categories = await rest<{ id: string; name: string }[]>(token, 'GET', '/api/categories')
  const restaurantsId = categories.find((c) => c.name === 'Restaurants')!.id
  const spend = '12.34'
  const todayIso = `${monthKey}-${String(today.getDate()).padStart(2, '0')}`
  const posted = await rest<{ id: string }>(token, 'POST', '/api/transactions', {
    account_id: checking.id, description: 'Synthetic F-002 dinner', amount: spend, date: todayIso,
    type: 'debit', category_id: restaurantsId, currency: 'USD', status: 'posted',
  })
  try {
    const after = await rest<Report>(token, 'GET', '/api/reports/cash-flow?months=6&interval=monthly&budget_forecast=true')
    saveEvidence('f002-after-expense.rest.json', after)
    const restaurantsAfter = after.budget_forecast!.months.find((m) => m.month === monthKey)!
      .categories.find((c) => c.name === 'Restaurants')!
    expect(cents(restaurantsAfter.posted)).toBe(cents(restaurants.posted) + cents(spend))
    const expectedRemaining = Math.max(cents(restaurants.remaining) - cents(spend), 0)
    expect(cents(restaurantsAfter.remaining)).toBe(expectedRemaining)
    if (daysLeft > 0 && cents(restaurants.remaining) >= cents(spend)) {
      expect(figure(after, 'endingBalance')).toBeCloseTo(figure(on, 'endingBalance')!, 2)
    }
  } finally {
    await rest(token, 'DELETE', `/api/transactions/${posted.id}`).catch(() => undefined)
  }

  // 9. The screen: off by default, then on with the figure, the panel, 24M, the views and the estimate switch.
  await loginInBrowser(page)
  await page.goto('/reports')
  await page.getByRole('button', { name: 'Cash Flow' }).first().click()
  await page.getByRole('button', { name: 'M', exact: true }).click()
  const toggle = page.getByTestId('cash-flow-budget-toggle').getByRole('button', { name: 'Include budgets' })
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByTestId('cash-flow-budget-panel')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '24M', exact: true })).toHaveCount(0)
  await settle(page)
  await screenshot(page, 'f002-1-off')

  const onResponse = page.waitForResponse((r) => r.url().includes('budget_forecast=true') && r.ok())
  await toggle.click()
  const screenOn = (await (await onResponse).json()) as Report
  saveEvidence('f002-on.screen.json', screenOn)
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  const panel = page.getByTestId('cash-flow-budget-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('Restaurants')
  await expect(page.getByText('Budgeted Spending').first()).toBeVisible()
  await settle(page)
  await screenshot(page, 'f002-2-on-monthly')
  await panel.scrollIntoViewIfNeeded()
  await screenshot(page, 'f002-3-panel')
  await panel.locator('xpath=following-sibling::*[1]').scrollIntoViewIfNeeded()
  await settle(page)
  await screenshot(page, 'f002-3b-breakdown')
  await page.evaluate(() => window.scrollTo(0, 0))

  for (const [label, name] of [['W', 'weekly'], ['D', 'daily']] as const) {
    const res = page.waitForResponse((r) => r.url().includes(`interval=${name}`) && r.url().includes('budget_forecast=true') && r.ok())
    await page.getByRole('button', { name: label, exact: true }).click()
    await res
    await settle(page)
    await screenshot(page, `f002-4-on-${name}`)
  }

  const res24 = page.waitForResponse((r) => r.url().includes('months=24') && r.ok())
  await page.getByRole('button', { name: '24M', exact: true }).click()
  await res24
  await settle(page)
  await screenshot(page, 'f002-5-on-24m-daily')

  // The estimate and the budget mode exclude each other; 24M goes with the budget mode.
  await page.getByRole('button', { name: 'Include estimate' }).click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('button', { name: '24M', exact: true })).toHaveCount(0)
  await settle(page)
  await screenshot(page, 'f002-6-estimate-turns-budgets-off')
  await toggle.click()
  await expect(page.getByRole('button', { name: 'Include estimate' })).toHaveAttribute('aria-pressed', 'false')

  // 10. A collection filter on the screen: the panel says the amount is not subtracted.
  const collection = await rest<{ id: string }>(token, 'POST', '/api/collections', {
    name: 'Synthetic F-002 checking', account_ids: [checking.id], wallet_ids: [],
  })
  try {
    await page.reload()
    await page.getByRole('button', { name: 'Cash Flow' }).first().click()
    await page.getByRole('button', { name: /All accounts/ }).first().click()
    const filtered = page.waitForResponse((r) => r.url().includes('account_ids=') && r.url().includes('budget_forecast=true') && r.ok())
    await page.getByRole('menuitem', { name: /Synthetic F-002 checking/ }).click()
    await page.waitForLoadState('networkidle')
    if ((await toggle.getAttribute('aria-pressed')) !== 'true') await toggle.click()
    const screenFiltered = (await (await filtered).json()) as Report
    expect(screenFiltered.budget_forecast!.placement).toBe('unallocated')
    await expect(page.getByText('Unallocated Budget').first()).toBeVisible()
    await settle(page)
    await screenshot(page, 'f002-7-collection-unallocated')
    await page.getByTestId('cash-flow-budget-panel').scrollIntoViewIfNeeded()
    await screenshot(page, 'f002-8-collection-panel')
  } finally {
    await rest(token, 'DELETE', `/api/collections/${collection.id}`).catch(() => undefined)
  }
})
