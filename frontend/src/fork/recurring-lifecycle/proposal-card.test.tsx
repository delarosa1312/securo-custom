// Fork F-005: the in-app proposal cards for the recurring lifecycle tools. Synthetic data.
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import { isProposalData } from '@/lib/agent-proposals'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('@/lib/api', () => ({
  default: { request },
  api: { request },
  accounts: {},
  budgets: {},
  categories: {},
  categoryGroups: {},
  goals: {},
  recurring: {},
  rules: {},
  transactions: {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-GB' }))

import { toast } from 'sonner'
import { ProposalCard } from '@/components/agents/proposal-card'
import { RECURRING_LIFECYCLE_KINDS } from './kinds'

const ID = '0b6f3c4e-9a51-4c1f-8f0e-2d7f6a1b3c5d'
const target = { id: ID, description: 'Rent', amount: 1500, currency: 'BRL', occurrence: { nominal_date: '2027-01-10' } }

describe('recurring lifecycle proposals', () => {
  it('are recognised as proposals', () => {
    for (const kind of RECURRING_LIFECYCLE_KINDS) expect(isProposalData({ kind })).toBe(true)
  })

  it('skips through the route the tool previewed', async () => {
    request.mockResolvedValue({ data: {} })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-skip"
        data={{ kind: 'skip_occurrence', target, apply_endpoint: `POST /api/recurring-transactions/${ID}/occurrences/2027-01-10/skip` } as never}
      />,
    )
    expect(screen.getByText(t('fork.recurringLifecycle.proposal.skip_occurrence'))).toBeInTheDocument()
    expect(screen.getByText('Rent: skip 2027-01-10')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(request).toHaveBeenCalledWith({
      method: 'POST', url: `/recurring-transactions/${ID}/occurrences/2027-01-10/skip`, data: {},
    }))
  })

  it('sends the change from a date with its body', async () => {
    request.mockResolvedValue({ data: {} })
    const body = { from_date: '2027-03-01', changes: { amount: '1600.00' } }
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-change-from"
        data={{
          kind: 'change_recurring_from', target, changes: body.changes, from_date: body.from_date,
          apply_endpoint: `POST /api/recurring-transactions/${ID}/change-from`, apply_body: body,
        } as never}
      />,
    )
    expect(screen.getByText('Rent from 2027-03-01: amount: 1500 → 1600.00')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(request).toHaveBeenCalledWith({
      method: 'POST', url: `/recurring-transactions/${ID}/change-from`, data: body,
    }))
  })

  it('says a permanent delete is permanent', () => {
    renderWithProviders(
      <ProposalCard
        toolCallId="call-purge"
        data={{ kind: 'delete_recurring', mode: 'permanent', target, apply_endpoint: `POST /api/recurring-transactions/${ID}/purge` } as never}
      />,
    )
    expect(screen.getByText('Rent: delete permanently')).toBeInTheDocument()
  })

  it('follows no route outside one schedule', async () => {
    request.mockClear()
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-foreign"
        data={{ kind: 'restore_recurring', target, apply_endpoint: 'POST /api/admin/users' } as never}
      />,
    )
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(request).not.toHaveBeenCalled()
  })
})
