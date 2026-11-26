// Smoke: the seeded stack answers the Cash Flow report the same way on the screen, over REST and over MCP.
import { expect, test } from '@playwright/test'

import { loginInBrowser, mcpClient, rest, restToken, saveEvidence, screenshot } from '../lib/stack'

type Report = {
  summary: Record<string, unknown>
  trend: { date: string; value: number }[]
  meta: { type: string; currency: string; forecast_start_date: string | null; lowest_balance: number | null }
}

test('Cash Flow report: browser, REST and MCP agree on the seeded projection', async ({ page }) => {
  const token = await restToken()

  // REST, as the Reports page calls it.
  const viaRest = await rest<Report>(token, 'GET', '/api/reports/cash-flow?months=6&interval=monthly')
  saveEvidence('smoke-cash-flow.rest.json', viaRest)
  expect(viaRest.meta.type).toBe('cash_flow')
  expect(viaRest.trend.length).toBeGreaterThan(0)
  expect(viaRest.meta.forecast_start_date).not.toBeNull()

  // MCP, as an external agent calls it.
  const mcp = await mcpClient(token)
  const viaMcp = (await mcp.call('get_cash_flow', { months: 6, interval: 'monthly' })) as Report
  saveEvidence('smoke-cash-flow.mcp.json', viaMcp)
  expect(viaMcp.trend).toEqual(viaRest.trend)
  expect(viaMcp.meta.lowest_balance).toEqual(viaRest.meta.lowest_balance)

  // Browser: the real login screen, then the Cash Flow tab of Reports.
  await loginInBrowser(page)
  await page.goto('/reports')
  await page.getByRole('tab', { name: 'Cash Flow' }).or(page.getByRole('button', { name: 'Cash Flow' })).first().click()
  const response = await page.waitForResponse((r) => r.url().includes('/api/reports/cash-flow') && r.ok())
  const viaScreen = (await response.json()) as Report
  saveEvidence('smoke-cash-flow.screen.json', { url: response.url(), body: viaScreen })
  const params = new URL(response.url()).searchParams
  if (params.get('months') === '6' && params.get('interval') === 'monthly') {
    expect(viaScreen.trend).toEqual(viaRest.trend)
  }
  await expect(page.locator('.recharts-surface').first()).toBeVisible()
  // Recharts animates for about a second; the evidence should show the drawn forecast.
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(2000)
  await screenshot(page, 'smoke-cash-flow.screen')
})
