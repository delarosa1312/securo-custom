// Fork F-001: the in-app proposal card for propose_set_account_available.
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import { isProposalData } from '@/lib/agent-proposals'
import './test-i18n'

const { updateAccount } = vi.hoisted(() => ({ updateAccount: vi.fn() }))
vi.mock('@/lib/api', () => ({
  accounts: { update: updateAccount },
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

import { ProposalCard } from '@/components/agents/proposal-card'

const target = { id: 'acc-reserve', name: 'Reserve', type: 'savings', currency: 'BRL', current_balance: 1000, include_in_available: true }

describe('set_account_available proposal', () => {
  it('is recognised as a proposal', () => {
    expect(isProposalData({ kind: 'set_account_available' })).toBe(true)
  })

  it('describes leaving the account out and applies it through the account', async () => {
    updateAccount.mockResolvedValue({ id: 'acc-reserve' })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-set-available-off"
        data={{ kind: 'set_account_available', target, changes: { include_in_available: false } }}
      />,
    )
    expect(screen.getByText(t('fork.availableBalance.proposal.title'))).toBeInTheDocument()
    expect(screen.getByText(t('fork.availableBalance.proposal.leaveOut', { name: 'Reserve' }))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(updateAccount).toHaveBeenCalledWith('acc-reserve', { include_in_available: false }))
  })

  it('describes including the account again', async () => {
    updateAccount.mockResolvedValue({ id: 'acc-reserve' })
    const { user } = renderWithProviders(
      <ProposalCard
        toolCallId="call-set-available-on"
        data={{ kind: 'set_account_available', target: { ...target, include_in_available: false }, changes: { include_in_available: true } }}
      />,
    )
    expect(screen.getByText(t('fork.availableBalance.proposal.include', { name: 'Reserve' }))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Apply/ }))
    await waitFor(() => expect(updateAccount).toHaveBeenCalledWith('acc-reserve', { include_in_available: true }))
  })
})
