// Fork F-004: the in-app proposal cards for the recurring transfer tools. Synthetic data.
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import { isProposalData } from '@/lib/agent-proposals'

const { createRecurring, updateRecurring, post } = vi.hoisted(() => ({
  createRecurring: vi.fn(), updateRecurring: vi.fn(), post: vi.fn(),
}))
vi.mock('@/lib/api', () => ({
  default: { post },
  accounts: {},
  budgets: {},
  categories: {},
  categoryGroups: {},
  goals: {},
  recurring: { create: createRecurring, update: updateRecurring },
  rules: {},
  transactions: {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-GB' }))

import { ProposalCard } from '@/components/agents/proposal-card'

const proposed = {
  description: 'Savings plan', amount: 200, currency: 'BRL',
  from_account_id: 'acc-checking', from_account_name: 'Checking',
  to_account_id: 'acc-savings', to_account_name: 'Savings',
  destination_amount: null, destination_currency: 'BRL', frequency: 'monthly',
  weekend_adjustment: 'next_monday', day_of_month: 10, start_date: '2027-01-10', end_date: null,
}

describe('recurring transfer proposals', () => {
  it('are recognised as proposals', () => {
    for (const kind of ['create_recurring_transfer', 'update_recurring_transfer', 'convert_transfer_series']) {
      expect(isProposalData({ kind })).toBe(true)
    }
  })

  it('creates the transfer schedule with both accounts', async () => {
    createRecurring.mockResolvedValue({ id: 'rec-plan' })
    const { user } = renderWithProviders(
      <ProposalCard toolCallId="call-create-transfer" data={{ kind: 'create_recurring_transfer', proposed }} />,
    )
    expect(screen.getByText(t('fork.recurringTransfers.proposal.create_recurring_transfer'))).toBeInTheDocument()
    expect(screen.getByText(/Savings plan: .* from Checking to Savings, Monthly/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(createRecurring).toHaveBeenCalledWith(expect.objectContaining({
      type: 'debit', account_id: 'acc-checking', destination_account_id: 'acc-savings',
      destination_amount: null, weekend_adjustment: 'next_monday', day_of_month: 10,
    })))
  })

  it('maps the update to the schedule fields', async () => {
    updateRecurring.mockResolvedValue({ id: 'rec-plan' })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-update-transfer"
        data={{
          kind: 'update_recurring_transfer',
          target: { id: 'rec-plan', description: 'Savings plan', amount: 200, to_account_id: 'acc-savings' },
          changes: { amount: 250, to_account_id: 'acc-broker', to_account_name: 'Broker', destination_amount: 45 },
        }}
      />,
    )
    expect(screen.getByText(/Savings plan: amount: 200 → 250/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(updateRecurring).toHaveBeenCalledWith('rec-plan', {
      amount: 250, destination_account_id: 'acc-broker', destination_amount: 45,
    }))
  })

  it('converts one series through its apply endpoint', async () => {
    post.mockResolvedValue({ data: { schedule: { id: 'rec-new' }, linked_transfers: 4 } })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-convert-series"
        data={{
          kind: 'convert_transfer_series',
          target: {
            series_id: 'series-1', description: 'Rent reserve', amount: '300.00', count: 4, frequency: 'monthly',
            source_account: { id: 'acc-checking', name: 'Checking', currency: 'BRL' },
            destination_account: { id: 'acc-savings', name: 'Savings', currency: 'BRL' },
          },
          proposed: { end_date: '2027-04-05', open_ended: false },
        }}
      />,
    )
    expect(screen.getByText(/4 scheduled transfers of .* from Checking to Savings \(Rent reserve, Monthly\) become one recurring transfer\. It ends on 2027-04-05\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/recurring-transactions/transfer-conversions/series-1/apply', { end_date: '2027-04-05' },
    ))
  })
})
