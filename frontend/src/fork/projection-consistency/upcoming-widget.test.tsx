// F-006 (#8): Upcoming asks from the workspace's today, the day the server projects from.
import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { UpcomingWidget, type DashboardScope } from '@/components/dashboard-widgets'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  dashboard: { projectedTransactions: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'Pacific/Kiritimati' }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (v: string) => v, privacyMode: false, MASK: '••••' }) }))

const SCOPE: DashboardScope = { month: '2026-11', activeAccountIds: null, activeWalletIds: null, noAccounts: false, currency: 'USD' }

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  // 12:00 UTC on the 20th is already the 21st at UTC+14.
  vi.setSystemTime(new Date('2026-11-20T12:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('UpcomingWidget window', () => {
  it('starts on the workspace day, not the browser day', async () => {
    api.dashboard.projectedTransactions.mockResolvedValue([])
    renderWithProviders(<UpcomingWidget scope={SCOPE} />)
    expect(await screen.findByText(t('widgets.upcoming.none', { days: 30 }))).toBeInTheDocument()
    expect(api.dashboard.projectedTransactions).toHaveBeenCalledWith({ from: '2026-11-21', to: '2026-12-21' })
  })
})
