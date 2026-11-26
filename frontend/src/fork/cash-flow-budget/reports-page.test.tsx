import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import ReportsPage from '@/pages/reports'
import { renderWithProviders, t } from '@/test/utils'
import type { ReportResponse } from '@/types'

import type { CashFlowBudgetResponse } from './types'

const api = vi.hoisted(() => ({
  reports: { netWorth: vi.fn(), incomeExpenses: vi.fn(), cashFlow: vi.fn(), dataRange: vi.fn(), plugin: vi.fn() },
  info: { get: vi.fn() },
  plugins: { installed: vi.fn() },
  timezones: { list: vi.fn().mockResolvedValue({ timezones: [], default: null }) },
  uiLayouts: { get: async () => ({ dashboard: null, reports: null }), save: async () => {}, reset: async () => {} },
}))
const fork = vi.hoisted(() => ({ cashFlowWithBudgets: vi.fn() }))

vi.mock('@/lib/api', () => ({
  reports: api.reports, timezones: api.timezones, uiLayouts: api.uiLayouts, info: api.info, plugins: api.plugins,
}))
vi.mock('@/fork/cash-flow-budget/api', () => fork)
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

function report(type: string): ReportResponse {
  return {
    summary: {
      primary_value: 4820, change_amount: -180, change_percent: -3.6,
      breakdowns: [
        { key: 'startingBalance', label: 'Starting Balance', value: 5000, color: '#6366F1' },
        { key: 'projectedExpenses', label: 'Projected Expenses', value: 70, color: '#F43F5E' },
        { key: 'endingBalance', label: 'Ending Balance', value: 4820, color: '#8B5CF6' },
      ],
    },
    trend: [{ date: '2026-09-20', value: 4820, breakdowns: { inflow: 0, outflow: 40 }, change: null }],
    meta: { type, series_keys: ['balance'], currency: 'USD', interval: 'daily' },
    composition: [],
    category_trend: [],
  }
}

function budgetReport(): CashFlowBudgetResponse {
  const base = report('cash_flow')
  return {
    ...base,
    summary: {
      ...base.summary,
      breakdowns: [
        ...base.summary.breakdowns.slice(0, 2),
        { key: 'budgetExpenses', label: 'Budgeted Spending', value: 110, color: '#F59E0B' },
        base.summary.breakdowns[2],
      ],
    },
    trend: [{ date: '2026-09-20', value: 4820, breakdowns: { inflow: 0, outflow: 40, budget_outflow: 11 }, change: null }],
    budget_forecast: {
      placement: 'subtracted', rule: 'even_daily', total: '110.00',
      months: [{
        month: '2026-09', spread_from: '2026-09-13', spread_to: '2026-09-30', days: 18,
        budget: '250.00', posted: '100.00', scheduled: '40.00', remaining: '110.00', forecast: '110.00',
        categories: [{
          category_id: 'c1', name: 'Restaurants', color: null, icon: null, group_id: null, group_name: null,
          budget: '250.00', posted: '100.00', scheduled: '40.00', remaining: '110.00', forecast: '110.00',
        }],
      }],
    },
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 12, 12))
  vi.clearAllMocks()
  localStorage.clear()
  api.reports.netWorth.mockResolvedValue(report('net_worth'))
  api.reports.incomeExpenses.mockResolvedValue(report('income_expenses'))
  api.reports.cashFlow.mockResolvedValue(report('cash_flow'))
  api.reports.dataRange.mockResolvedValue({ min_date: null, max_date: null })
  api.info.get.mockResolvedValue({ features: { plugins: false } })
  api.plugins.installed.mockResolvedValue([])
  fork.cashFlowWithBudgets.mockResolvedValue(budgetReport())
})

afterEach(() => vi.useRealTimers())

async function openCashFlow() {
  const rendered = renderWithProviders(<ReportsPage />)
  await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
  await rendered.user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
  await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
  return rendered
}

const budgetsButton = () => screen.getByRole('button', { name: t('fork.budgetForecast.toggle') })
const estimateButton = () => screen.getByRole('button', { name: t('reports.includeEstimate') })

describe('Cash Flow: Include budgets (F-002)', () => {
  it('is off by default: the upstream request, no panel, no 24 months', async () => {
    await openCashFlow()
    expect(budgetsButton()).toHaveAttribute('aria-pressed', 'false')
    expect(api.reports.cashFlow.mock.calls.at(-1)).toEqual([6, 'monthly', false, undefined])
    expect(fork.cashFlowWithBudgets).not.toHaveBeenCalled()
    expect(screen.queryByTestId('cash-flow-budget-panel')).toBeNull()
    expect(screen.queryByRole('button', { name: t('reports.range24m') })).toBeNull()
  })

  it('asks for the budget mode and shows the figure, the panel and 24 months', async () => {
    const { user } = await openCashFlow()
    await user.click(budgetsButton())

    await waitFor(() => expect(fork.cashFlowWithBudgets).toHaveBeenCalledWith(6, 'monthly', undefined))
    expect(await screen.findByTestId('cash-flow-budget-panel')).toHaveTextContent('Restaurants')
    expect(screen.getAllByText(t('reports.budgetExpenses')).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: t('reports.range24m') }))
    await waitFor(() => expect(fork.cashFlowWithBudgets).toHaveBeenLastCalledWith(24, 'monthly', undefined))
  })

  it('turns the estimate off, and the estimate turns it off and leaves 24 months', async () => {
    const { user } = await openCashFlow()
    await user.click(estimateButton())
    expect(estimateButton()).toHaveAttribute('aria-pressed', 'true')

    await user.click(budgetsButton())
    expect(budgetsButton()).toHaveAttribute('aria-pressed', 'true')
    expect(estimateButton()).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getByRole('button', { name: t('reports.range24m') }))

    await user.click(estimateButton())
    expect(budgetsButton()).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('button', { name: t('reports.range24m') })).toBeNull()
    await waitFor(() => expect(api.reports.cashFlow).toHaveBeenLastCalledWith(12, 'monthly', true, undefined))
  })

  it('keeps 24 months when leaving the tab and coming back with budgets on', async () => {
    const { user } = await openCashFlow()
    await user.click(budgetsButton())
    await user.click(await screen.findByRole('button', { name: t('reports.range24m') }))
    await waitFor(() => expect(fork.cashFlowWithBudgets).toHaveBeenLastCalledWith(24, 'monthly', undefined))

    await user.click(screen.getByRole('button', { name: t('reports.netWorth') }))
    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
    expect(screen.getByRole('button', { name: t('reports.range24m') })).toBeInTheDocument()
    await waitFor(() => expect(fork.cashFlowWithBudgets).toHaveBeenLastCalledWith(24, 'monthly', undefined))
  })
})
