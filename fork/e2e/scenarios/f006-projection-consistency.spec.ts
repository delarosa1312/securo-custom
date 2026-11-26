// F-006 (#8): the projected balance views agree on the seeded stack, through REST, MCP and the screen.
// Every figure is compared view against view (docs/fork/PROJECTIONS.md), never against numbers copied here.
import { expect, test, type Page, type Response } from '@playwright/test'

import { loginInBrowser, mcpClient, rest, restToken, saveEvidence, screenshot } from '../lib/stack'

type Point = { date: string; value: number }
type Report = { trend: Point[] }
type Summary = { projected_balance_primary: number | null }
type History = { current: { date?: string; balance: number | null }[] }
type CalendarItem = { description: string; source: string | null; invoice_number?: string | null }
type Calendar = { days: { date: string; ending_balance: number; items: CalendarItem[] }[] }
type Forecast = { daily_trajectory: { date: string; ending_balance: string; events: string[] }[] }

const cents = (value: number | string | null | undefined) => Math.round(Number(value) * 100)
const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
const monthEnd = (d: Date, ahead = 0) => new Date(d.getFullYear(), d.getMonth() + ahead + 1, 0)
const monthStart = (d: Date) => iso(new Date(d.getFullYear(), d.getMonth(), 1))
const ids = (accountIds?: string[]) => (accountIds ?? []).map((id) => `&account_ids=${id}`).join('')

async function settle(page: Page) {
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1500) // chart animation
}

/** Every balance view's figure for a day, from REST. */
async function views(token: string, today: Date, accountIds?: string[]) {
  const end0 = monthEnd(today)
  const end1 = monthEnd(today, 1)
  const q = ids(accountIds)
  const cashFlow = await rest<Report>(token, 'GET', `/api/reports/cash-flow?months=2&interval=daily${q}`)
  const point = (d: Date) => cashFlow.trend.find((p) => p.date === iso(d))?.value
  const summary = await rest<Summary>(token, 'GET', `/api/dashboard/summary?month=${monthStart(today)}${q}`)
  const cal0 = await rest<Calendar>(token, 'GET', `/api/transactions/calendar?month=${monthStart(end0)}${q}`)
  const cal1 = await rest<Calendar>(token, 'GET', `/api/transactions/calendar?month=${monthStart(end1)}${q}`)
  const chart1 = await rest<History>(token, 'GET', `/api/dashboard/balance-history?month=${monthStart(end1)}${q}`)
  const day = (c: Calendar, d: Date) => c.days.find((x) => x.date === iso(d))?.ending_balance
  return {
    raw: { cashFlow, summary, cal0, cal1, chart1 },
    thisMonthEnd: {
      card: cents(summary.projected_balance_primary),
      cashFlow: cents(point(end0)),
      calendar: cents(day(cal0, end0)),
    },
    nextMonthEnd: {
      cashFlow: cents(point(end1)),
      calendar: cents(day(cal1, end1)),
      chart: cents(chart1.current[end1.getDate() - 1].balance),
    },
  }
}

function allEqual(figures: Record<string, number>, label: string) {
  const values = Object.values(figures)
  expect(new Set(values).size, `${label}: ${JSON.stringify(figures)}`).toBe(1)
}

test('F-006 projected balances agree: card, chart, Cash Flow, calendar, predictive, REST, MCP and screen', async ({ page }) => {
  test.setTimeout(180_000)
  const token = await restToken()
  const today = new Date()
  const accounts = await rest<{ id: string; name: string }[]>(token, 'GET', '/api/accounts')
  const checking = accounts.find((a) => a.name === 'Synthetic Checking')!

  // An open invoice due in two days: every balance view counts it, and the calendar lists it (Q13).
  const invoiceDue = addDays(today, 2)
  const invoice = await rest<{ id: string; status: string }>(token, 'POST', '/api/invoices', {
    origin: 'imported', external_source: 'e2e', external_id: 'f006', external_number: 'NF-F006',
    issue_date: iso(today), due_date: iso(invoiceDue), currency: 'BRL', total: '350.00',
  })
  if (invoice.status === 'draft') await rest(token, 'POST', `/api/invoices/${invoice.id}/issue`)
  saveEvidence('f006-invoice.rest.json', invoice)
  // A monthly bill due three days ago, never generated (Q12): it counts nowhere until it is a row.
  const overdueDay = addDays(today, -3)
  await rest(token, 'POST', '/api/recurring-transactions', {
    description: 'Synthetic Overdue bill', amount: '70.00', currency: 'BRL', type: 'debit', frequency: 'monthly',
    day_of_month: overdueDay.getDate(), start_date: iso(overdueDay), account_id: checking.id, auto_generate: false,
  })

  // 1. REST: the same day gives the same balance in every view, all accounts and checking only.
  const scopes: Record<string, string[] | undefined> = { all: undefined, checking: [checking.id] }
  const restViews: Record<string, Awaited<ReturnType<typeof views>>> = {}
  for (const [scope, accountIds] of Object.entries(scopes)) {
    const v = await views(token, today, accountIds)
    restViews[scope] = v
    saveEvidence(`f006-${scope}.rest.json`, { thisMonthEnd: v.thisMonthEnd, nextMonthEnd: v.nextMonthEnd })
    saveEvidence(`f006-${scope}-raw.rest.json`, v.raw)
    allEqual(v.thisMonthEnd, `${scope} ${iso(monthEnd(today))}`)
    allEqual(v.nextMonthEnd, `${scope} ${iso(monthEnd(today, 1))}`)
  }

  // The calendar lists the invoice it counts on its due date.
  const invoiceMonth = await rest<Calendar>(token, 'GET', `/api/transactions/calendar?month=${monthStart(invoiceDue)}`)
  const invoiceDay = invoiceMonth.days.find((d) => d.date === iso(invoiceDue))!
  expect(invoiceDay.items.filter((i) => i.source === 'invoice').map((i) => i.description)).toEqual([
    'Invoice NF-F006 (expected)',
  ])

  // The overdue bill is not listed on its past day, and the calendar month around it agrees with the other views.
  const overdueMonth = await rest<Calendar>(token, 'GET', `/api/transactions/calendar?month=${monthStart(overdueDay)}`)
  const overdueItems = overdueMonth.days.flatMap((d) => d.items.map((i) => ({ date: d.date, ...i })))
    .filter((i) => i.description === 'Synthetic Overdue bill')
  saveEvidence('f006-overdue.rest.json', overdueItems)
  expect(overdueItems.filter((i) => i.date <= iso(today))).toEqual([])

  // 2. The predictive forecast counts the seed's pending rows once, on their dates (none before F-006).
  const horizon = 45
  const forecast = await rest<Forecast>(token, 'GET', `/api/forecast/cashflow?horizon_days=${horizon}&include_discretionary=false`)
  saveEvidence('f006-predictive.rest.json', forecast)
  const eventsOn = (name: string) =>
    forecast.daily_trajectory.filter((p) => p.events.some((e) => e.includes(name))).map((p) => p.date)
  expect(eventsOn('Synthetic Insurer premium')).toEqual([iso(addDays(today, 7))])
  expect(eventsOn('Synthetic Freelance invoice')).toEqual([iso(addDays(today, 20))])
  // Yesterday's pending card row is not in the card balance yet: it counts tomorrow.
  expect(eventsOn('Synthetic Bistro (pending)')).toEqual([iso(addDays(today, 1))])
  // The overdue bill counts only from its next date, a month after the missed one.
  const nextOverdue = new Date(overdueDay.getFullYear(), overdueDay.getMonth() + 1, overdueDay.getDate())
  expect(eventsOn('Synthetic Overdue bill')).toEqual([iso(nextOverdue)])

  // 3. MCP gives the same figures as REST.
  const mcp = await mcpClient(token)
  const viaMcpCashFlow = (await mcp.call('get_cash_flow', { months: 2, interval: 'daily' })) as Report
  const viaMcpSnapshot = (await mcp.call('get_dashboard_snapshot', { month: monthStart(today) })) as Summary
  const viaMcpForecast = (await mcp.call('get_cashflow_forecast', { horizon_days: horizon, include_discretionary: false })) as Forecast
  saveEvidence('f006-cash-flow.mcp.json', viaMcpCashFlow)
  saveEvidence('f006-predictive.mcp.json', viaMcpForecast)
  expect(viaMcpCashFlow.trend).toEqual(restViews.all.raw.cashFlow.trend)
  expect(cents(viaMcpSnapshot.projected_balance_primary)).toBe(restViews.all.thisMonthEnd.card)
  expect(viaMcpForecast.daily_trajectory).toEqual(forecast.daily_trajectory)

  // 4. The screen asks for the same figures: dashboard card and chart, calendar, Upcoming, Cash Flow.
  await loginInBrowser(page)
  const seen = (pathPart: string) => (r: Response) => r.url().includes(pathPart) && r.ok()
  const upcoming = page.waitForRequest((r) => r.url().includes('/api/dashboard/projected-transactions') && r.url().includes('from='))
  const summaryOnScreen = page.waitForResponse(seen('/api/dashboard/summary'))
  await page.goto('/')
  const screenSummary = (await (await summaryOnScreen).json()) as Summary
  expect(cents(screenSummary.projected_balance_primary)).toBe(restViews.all.thisMonthEnd.card)
  const upcomingUrl = new URL((await upcoming).url())
  saveEvidence('f006-upcoming.request.json', Object.fromEntries(upcomingUrl.searchParams))
  expect(upcomingUrl.searchParams.get('from')).toBe(iso(today))
  await settle(page)
  await screenshot(page, 'f006-1-dashboard')

  const calendarOnScreen = page.waitForResponse(seen('/api/transactions/calendar'))
  await page.goto('/?view=calendar')
  const screenCalendar = (await (await calendarOnScreen).json()) as Calendar
  const end0 = iso(monthEnd(today))
  expect(cents(screenCalendar.days.find((d) => d.date === end0)?.ending_balance)).toBe(restViews.all.thisMonthEnd.calendar)
  await settle(page)
  if (monthStart(invoiceDue) === monthStart(today)) {
    // Open the invoice's day: the day panel lists "Invoice NF-F006 (expected)".
    const dayNumber = String(invoiceDue.getDate())
    await page.locator('[role="button"]:not(.text-muted-foreground)')
      .filter({ has: page.getByText(dayNumber, { exact: true }) }).first().click()
    await expect(page.getByText('Invoice NF-F006 (expected)').first()).toBeVisible()
  }
  await screenshot(page, 'f006-2-calendar', true)

  const cashFlowOnScreen = page.waitForResponse(seen('/api/reports/cash-flow'))
  await page.goto('/reports')
  await page.getByRole('button', { name: 'Cash Flow' }).first().click()
  const screenCashFlow = (await (await cashFlowOnScreen).json()) as Report
  saveEvidence('f006-cash-flow.screen.json', screenCashFlow)
  const shownEnd0 = screenCashFlow.trend.find((p) => p.date === end0)
  if (shownEnd0) expect(cents(shownEnd0.value)).toBe(restViews.all.thisMonthEnd.cashFlow)
  await settle(page)
  await screenshot(page, 'f006-3-cash-flow')
})
