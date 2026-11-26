// Fork F-005: the series dialogs, the from-when dialog and the overdue list. Synthetic data.
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'

const lifecycle = vi.hoisted(() => ({
  trash: vi.fn(), end: vi.fn(), restore: vi.fn(), purge: vi.fn(), changeFrom: vi.fn(), overdue: vi.fn(),
  skip: vi.fn(),
}))
const update = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ recurringLifecycle: lifecycle }))
vi.mock('@/lib/api', () => ({ recurring: { update }, default: {}, api: {} }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (value: string) => value }) }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))

import { ChangeFromDialog } from './change-from-dialog'
import type { LifecycleRecurring } from './edit-scope'
import { OverdueSection, UpcomingOverdueLine } from './overdue-section'
import { SeriesActionDialog } from './series-dialogs'
import type { Impact } from './types'

const rent = {
  id: 'rec-rent', description: 'Rent', amount: 1500, currency: 'USD', type: 'debit', frequency: 'monthly',
  weekend_adjustment: 'none', day_of_month: 10, start_date: '2026-01-10', end_date: null, is_active: true,
  auto_generate: true, next_occurrence: '2026-12-10', account_id: 'acc-1', category_id: null,
  destination_account_id: null, destination_amount: null, deleted_at: null,
} as unknown as LifecycleRecurring

const row = (id: string, date: string) => ({ transaction_id: id, date, amount: '1500.00', status: 'posted', transfer_pair_id: null })
const impact = (over: Partial<Impact> = {}): Impact => ({
  history: 0, follow: [], removed: [], edited: [], skipped_kept: [], skipped_dropped: [], ...over,
})

beforeEach(() => vi.clearAllMocks())

describe('Delete (to Deleted)', () => {
  it('ticks the scheduled rows by default and counts what stays', async () => {
    lifecycle.trash.mockResolvedValue({ deleted: [], impact: impact({ history: 3, removed: [row('tx-9', '2026-12-10')] }), preview: true })
    const { user } = renderWithProviders(<SeriesActionDialog schedule={rent} action="delete" onClose={() => {}} />)
    const box = await screen.findByTestId('delete-scheduled')
    expect(box).toBeChecked()
    expect(screen.getByText(/Its 2 recorded transactions stay/)).toBeInTheDocument()
    expect(screen.getByText(/Also delete the 1 scheduled transaction after today \(Dec 10, \$1,500\.00\)/)).toBeInTheDocument()
    await user.click(box)
    expect(screen.getByText(/Its 3 recorded transactions stay/)).toBeInTheDocument()
    await user.click(screen.getByTestId('series-confirm'))
    await waitFor(() => expect(lifecycle.trash).toHaveBeenLastCalledWith('rec-rent', { delete_scheduled: false, preview: false }))
  })

  it('shows no checkbox when nothing is scheduled', async () => {
    lifecycle.trash.mockResolvedValue({ deleted: [], impact: impact({ history: 1 }), preview: true })
    renderWithProviders(<SeriesActionDialog schedule={rent} action="delete" onClose={() => {}} />)
    expect(await screen.findByText(/Its 1 recorded transaction stays/)).toBeInTheDocument()
    expect(screen.queryByTestId('delete-scheduled')).not.toBeInTheDocument()
  })
})

describe('Restore and Delete permanently', () => {
  it('says which occurrences are marked skipped and the next one', async () => {
    lifecycle.restore.mockResolvedValue({ restored: [], skipped: ['2026-10-10', '2026-11-10'], next_occurrence: '2026-12-10', preview: true })
    const deleted = { ...rent, deleted_at: '2026-09-20T12:00:00Z' } as LifecycleRecurring
    const { user } = renderWithProviders(<SeriesActionDialog schedule={deleted} action="restore" onClose={() => {}} />)
    expect(await screen.findByText(/The 2 occurrences between then and today \(Oct 10, Nov 10\) are marked as skipped/)).toBeInTheDocument()
    expect(screen.getByText(/The next one is Dec 10, 2026/)).toBeInTheDocument()
    await user.click(screen.getByTestId('series-confirm'))
    await waitFor(() => expect(lifecycle.restore).toHaveBeenLastCalledWith('rec-rent', false))
  })

  it('counts the transactions a permanent delete keeps', async () => {
    lifecycle.purge.mockResolvedValue({ deleted: [], kept_transactions: 4, preview: true })
    renderWithProviders(<SeriesActionDialog schedule={rent} action="purge" onClose={() => {}} />)
    expect(await screen.findByText(/Its 4 recorded transactions stay in your history/)).toBeInTheDocument()
  })
})

describe('Apply the change from when?', () => {
  it('previews from the next occurrence and moves a new end date to the part that goes on', async () => {
    lifecycle.changeFrom.mockImplementation(async (_id: string, body: { preview: boolean }) => ({
      schedule: body.preview ? null : { id: 'rec-next' },
      impact: impact({ follow: [row('tx-5', '2026-12-10')] }),
      preview: body.preview,
    }))
    const plan = { values: { amount: 1600 }, series: { end_date: '2027-12-31' }, askScope: true, canSplit: true }
    const onDone = vi.fn()
    const { user } = renderWithProviders(
      <ChangeFromDialog schedule={rent} plan={plan as never} onClose={() => {}} onDone={onDone} />,
    )
    expect(await screen.findByText(/1 scheduled transaction \(Dec 10\) changes to the new values/)).toBeInTheDocument()
    expect(lifecycle.changeFrom).toHaveBeenCalledWith('rec-rent', { from_date: '2026-12-10', changes: { amount: 1600 }, preview: true })
    await user.click(screen.getByTestId('change-apply'))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(update).toHaveBeenCalledWith('rec-next', { end_date: '2027-12-31' })
  })

  it('corrects the whole series with every field when chosen', async () => {
    lifecycle.changeFrom.mockResolvedValue({ schedule: null, impact: impact(), preview: true })
    const plan = { values: { amount: 1600 }, series: { end_date: '2027-12-31' }, askScope: true, canSplit: true }
    const { user } = renderWithProviders(
      <ChangeFromDialog schedule={rent} plan={plan as never} onClose={() => {}} onDone={() => {}} />,
    )
    await user.click(screen.getByRole('radio', { name: new RegExp(t('fork.recurringLifecycle.changeFrom.whole')) }))
    await waitFor(() => expect(lifecycle.changeFrom).toHaveBeenLastCalledWith('rec-rent', {
      from_date: null, changes: { amount: 1600, end_date: '2027-12-31' }, preview: true,
    }))
    expect(await screen.findByText(/Transactions already recorded stay as they are/)).toBeInTheDocument()
  })
})

describe('overdue occurrences', () => {
  const overdue = [
    { recurring_id: 'rec-rent', description: 'Rent', account_id: 'acc-1', destination_account_id: null, type: 'debit',
      amount: '1500.00', currency: 'USD', nominal_date: '2026-11-10', date: '2026-11-10', auto_generate: false },
    { recurring_id: 'rec-save', description: 'Savings plan', account_id: 'acc-2', destination_account_id: 'acc-3', type: 'debit',
      amount: '200.00', currency: 'USD', nominal_date: '2026-11-15', date: '2026-11-15', auto_generate: false },
  ]

  it('lists them with Record, Link (not for a transfer) and Skip', async () => {
    lifecycle.overdue.mockResolvedValue(overdue)
    lifecycle.skip.mockResolvedValue(undefined)
    const { user } = renderWithProviders(<OverdueSection schedules={[rent]} canWrite />)
    const rows = await screen.findAllByTestId('overdue-row')
    expect(screen.getByText(/2 overdue occurrences/)).toBeInTheDocument()
    expect(within(rows[0]).getByRole('button', { name: 'Link a transaction' })).toBeInTheDocument()
    expect(within(rows[1]).queryByRole('button', { name: 'Link a transaction' })).not.toBeInTheDocument()
    await user.click(within(rows[0]).getByRole('button', { name: 'Skip' }))
    await waitFor(() => expect(lifecycle.skip).toHaveBeenCalledWith('rec-rent', '2026-11-10'))
  })

  it('offers no action in a read-only workspace', async () => {
    lifecycle.overdue.mockResolvedValue(overdue)
    renderWithProviders(<OverdueSection schedules={[rent]} canWrite={false} />)
    await screen.findAllByTestId('overdue-row')
    expect(screen.queryByRole('button', { name: 'Skip' })).not.toBeInTheDocument()
  })

  it('counts in Upcoming only the accounts in view, a transfer by either account', async () => {
    lifecycle.overdue.mockResolvedValue(overdue)
    const { unmount } = renderWithProviders(<UpcomingOverdueLine activeAccountIds={['acc-3']} />)
    expect(await screen.findByText(/1 overdue occurrence not recorded/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review' })).toHaveAttribute('href', '/recurring#overdue')
    unmount()
    renderWithProviders(<UpcomingOverdueLine activeAccountIds={null} />)
    expect(await screen.findByText(/2 overdue occurrences not recorded/)).toBeInTheDocument()
  })
})
