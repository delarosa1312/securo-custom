// Fork F-001: the dashboard's Available balance and the card widget's after-paying
// figure leave out an account switched off; net worth and the cash row keep it.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import DashboardPage from '@/pages/dashboard'
import { CreditCardsWidget, type DashboardScope } from '@/components/dashboard-widgets'
import { TooltipProvider } from '@/components/ui/tooltip'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, DashboardSummary } from '@/types'

const api = vi.hoisted(() => ({
  dashboard: {
    summary: vi.fn(),
    spendingByCategory: vi.fn(),
    balanceHistory: vi.fn(),
    balanceHistoryMonths: vi.fn(),
    projectedTransactions: vi.fn(),
  },
  transactions: { list: vi.fn(), calendar: vi.fn(), update: vi.fn(), get: vi.fn(), flaggedSummary: vi.fn() },
  budgets: { comparison: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn(), listIncludingHidden: vi.fn() },
  accounts: { list: vi.fn() },
  assets: { list: vi.fn() },
  currencies: { list: vi.fn() },
  mortgagePayments: { get: vi.fn() },
  goals: { summary: vi.fn() },
  groups: { list: vi.fn() },
  payees: { list: vi.fn() },
  fiscal: { taxIdKinds: vi.fn() },
  rules: { create: vi.fn(), list: vi.fn() },
  settings: { attachments: vi.fn() },
  info: { get: vi.fn() },
  plugins: { installed: vi.fn() },
  reports: { plugin: vi.fn() },
  invoices: { summary: vi.fn() },
  invoiceSchedules: { summary: vi.fn() },
  uiLayouts: {
    get: async () => ({ dashboard: null, reports: null }),
    save: async () => {},
    reset: async () => {},
  },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
const collectionFilter = vi.hoisted(() => ({ activeAccountIds: null as string[] | null, activeWalletIds: null as string[] | null }))
vi.mock('@/contexts/collection-filter-context', () => ({ useCollectionFilter: () => collectionFilter }))

function account(overrides: Partial<Account>): Account {
  return {
    id: 'acc', user_id: 'user-1', connection_id: null, external_id: null, name: 'Account', display_name: null,
    masked_number: null, institution_name: null, institution_logo_url: null, type: 'checking', balance: 0,
    current_balance: 0, previous_balance: null, balance_primary: null, currency: 'USD', credit_limit: null,
    available_credit: null, statement_close_day: null, payment_due_day: null, next_close_date: null,
    next_due_date: null, minimum_payment: null, card_brand: null, card_level: null, shared_balance_group: null,
    is_closed: false, closed_at: null,
    ...overrides,
  }
}

// The worked example of issue #4.
const accounts = [
  account({ id: 'checking', name: 'Current account', current_balance: 500 }),
  account({ id: 'wallet', name: 'Cash', type: 'wallet', current_balance: 100 }),
  account({ id: 'savings', name: 'Reserve', type: 'savings', current_balance: 1000, include_in_available: false }),
  account({ id: 'card', name: 'Card', type: 'credit_card', current_balance: -300 }),
]

const summary: DashboardSummary = {
  total_balance: { USD: 1300 },
  total_balance_primary: 1300,
  projected_balance: { USD: 1300 },
  projected_balance_primary: 1300,
  balance_date: '2026-09-12',
  monthly_income: 0,
  monthly_expenses: 0,
  monthly_income_primary: 0,
  monthly_expenses_primary: 0,
  accounts_count: accounts.length,
  pending_categorization: 0,
  pending_categorization_amount: 0,
  assets_value: {},
  assets_value_primary: 0,
  cash_balance: { USD: 1600 },
  cash_balance_primary: 1600,
  primary_currency: 'USD',
  pending_shares_net: 0,
}

const usd = (value: number) => formatCurrency(value, 'USD', 'en-US')

beforeEach(() => {
  vi.clearAllMocks()
  collectionFilter.activeAccountIds = null
  collectionFilter.activeWalletIds = null
  api.dashboard.summary.mockResolvedValue(summary)
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.balanceHistory.mockResolvedValue({
    current: [], opening_balance: 0, baseline: [], comparison: null, comparison_month: null, comparison_opening_balance: null,
  })
  api.dashboard.balanceHistoryMonths.mockResolvedValue([])
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.budgets.comparison.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.categoryGroups.listIncludingHidden.mockResolvedValue([])
  api.accounts.list.mockResolvedValue(accounts)
  api.assets.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.goals.summary.mockResolvedValue([])
  api.groups.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
  api.rules.list.mockResolvedValue([])
  api.settings.attachments.mockResolvedValue({ enabled: false })
  api.info.get.mockRejectedValue(new Error('no info'))
})

function renderDashboard() {
  return renderWithProviders(
    <TooltipProvider delayDuration={0}>
      <DashboardPage />
    </TooltipProvider>,
  )
}

describe('Available balance with an account switched off', () => {
  it('leaves the reserve out of the headline and its chips, and nothing else', async () => {
    const { user } = renderDashboard()
    await screen.findByText(usd(1300))
    const heading = screen.getByText(t('dashboard.availableBalance'))
    expect(heading.nextElementSibling).toHaveTextContent(usd(600))
    expect(screen.getByText('Current account').closest('a')).toBeInTheDocument()
    expect(screen.getByText('Cash').closest('a')).toBeInTheDocument()
    expect(screen.queryByText('Reserve')).not.toBeInTheDocument()

    // Net worth breakdown: the cash row still holds the reserve.
    await user.hover(screen.getByRole('button', { name: 'i' }))
    const tooltip = await screen.findByRole('tooltip')
    expect(within(tooltip).getByText(t('dashboard.cashBalance')).parentElement!).toHaveTextContent(usd(1600))
  })

  it('counts every account while the setting is untouched, as upstream', async () => {
    api.accounts.list.mockResolvedValue(accounts.map((a) => ({ ...a, include_in_available: undefined })))
    renderDashboard()
    await screen.findByText(usd(1300))
    expect(screen.getByText(t('dashboard.availableBalance')).nextElementSibling).toHaveTextContent(usd(1600))
  })

  it('follows the active collection', async () => {
    collectionFilter.activeAccountIds = ['checking', 'savings']
    renderDashboard()
    await screen.findByText(usd(1300))
    expect(screen.getByText(t('dashboard.availableBalance')).nextElementSibling).toHaveTextContent(usd(500))
  })

  it('reads 0 when every liquid account is off', async () => {
    api.accounts.list.mockResolvedValue(accounts.map((a) => (a.type === 'credit_card' ? a : { ...a, include_in_available: false })))
    renderDashboard()
    await screen.findByText(usd(1300))
    expect(screen.getByText(t('dashboard.availableBalance')).nextElementSibling).toHaveTextContent(usd(0))
  })
})

describe('Card widget after paying', () => {
  const scope: DashboardScope = { month: '2026-03', activeAccountIds: null, activeWalletIds: null, noAccounts: false, currency: 'USD' }

  it('leaves the reserve out of what is left after paying the cards', async () => {
    renderWithProviders(<CreditCardsWidget scope={scope} />)
    // Checking and savings, as upstream; the reserve is off: 500 - 300.
    expect(await screen.findByText(t('widgets.cards.afterPaying', { amount: usd(200) }))).toBeInTheDocument()
  })

  it('counts the reserve again once it is switched back on', async () => {
    api.accounts.list.mockResolvedValue(accounts.map((a) => ({ ...a, include_in_available: true })))
    renderWithProviders(<CreditCardsWidget scope={scope} />)
    expect(await screen.findByText(t('widgets.cards.afterPaying', { amount: usd(1200) }))).toBeInTheDocument()
  })
})
