// Fork F-004: the recurring form as a transfer between two of the user's accounts. Synthetic data.
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RecurringForm } from '@/pages/recurring'
import { currencies } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { RecurringTransaction } from '@/types'
import { TransferRoute } from './transfer-route'
import { isProjectedTransferLeg } from './transfer'

vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-GB',
  useDisplayLocale: () => 'en-US',
  useDisplayLocaleSettled: () => true,
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn() },
}))

const accounts = [
  { id: 'acc-checking', name: 'Checking', currency: 'BRL', is_closed: false },
  { id: 'acc-savings', name: 'Savings', currency: 'BRL', is_closed: false },
  { id: 'acc-broker', name: 'Broker', currency: 'USD', is_closed: false },
  { id: 'acc-old', name: 'Old wallet', currency: 'BRL', is_closed: true },
]

function savingsPlan(overrides: Partial<RecurringTransaction> = {}): RecurringTransaction {
  return {
    id: 'rec-plan', user_id: 'user-1', account_id: 'acc-checking', category_id: null,
    description: 'Savings plan', amount: 200, currency: 'BRL', type: 'debit', frequency: 'monthly',
    weekend_adjustment: 'none', day_of_month: 10, start_date: '2027-01-10', end_date: null,
    is_active: true, auto_generate: true, next_occurrence: '2027-01-10', amount_primary: null,
    fx_rate_used: null, destination_account_id: 'acc-savings', destination_amount: null,
    ...overrides,
  }
}

function renderForm(seed: RecurringTransaction | null) {
  const onSave = vi.fn()
  const result = renderWithProviders(
    <RecurringForm
      recurring={seed}
      categories={[]}
      categoryGroups={[]}
      currentCategory={undefined}
      accounts={accounts}
      onSave={onSave}
      onCancel={vi.fn()}
      loading={false}
    />,
  )
  return { ...result, onSave }
}

const typeSelect = () => screen.getByDisplayValue(t('recurring.expense')) as HTMLSelectElement
const fromSelect = () => screen.getByLabelText(t('fork.recurringTransfers.fromAccount')) as HTMLSelectElement
const toSelect = () => screen.getByLabelText(t('fork.recurringTransfers.toAccount')) as HTMLSelectElement
const options = (select: HTMLSelectElement) =>
  within(select).getAllByRole('option', { hidden: true }).filter((o) => !(o as HTMLOptionElement).hidden).map((o) => o.textContent)

beforeEach(() => {
  vi.mocked(currencies.list).mockResolvedValue([])
})

describe('RecurringForm as a transfer', () => {
  it('replaces the category with the two accounts and saves a transfer', async () => {
    const { user, onSave } = renderForm(null)
    await user.type(screen.getByLabelText(t('recurring.description')), 'Savings plan')
    await user.type(screen.getByLabelText(t('recurring.amount')), '200.00')
    await user.selectOptions(typeSelect(), 'transfer')

    expect(screen.queryByText(t('recurring.category'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('categoryLines.start'))).not.toBeInTheDocument()
    await user.selectOptions(fromSelect(), 'acc-checking')
    // Never the source itself, never a closed account.
    expect(options(toSelect())).toEqual(['Broker', 'Savings'])
    await user.selectOptions(toSelect(), 'acc-savings')
    expect(screen.queryByLabelText(/Amount received/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave.mock.calls[0][0]).toMatchObject({
      amount: 200, currency: 'BRL', type: 'debit', category_id: null,
      account_id: 'acc-checking', destination_account_id: 'acc-savings', destination_amount: null,
    })
  })

  it('asks for the amount received between currencies', async () => {
    const { user, onSave } = renderForm(null)
    await user.type(screen.getByLabelText(t('recurring.description')), 'Broker top-up')
    await user.type(screen.getByLabelText(t('recurring.amount')), '200.00')
    await user.selectOptions(typeSelect(), 'transfer')
    await user.selectOptions(fromSelect(), 'acc-checking')
    await user.selectOptions(toSelect(), 'acc-broker')

    const received = screen.getByLabelText(t('fork.recurringTransfers.amountReceived', { currency: 'USD' }))
    expect(received).toBeRequired()
    await user.type(received, '36.00')
    await user.click(screen.getByRole('button', { name: t('common.save') }))
    expect(onSave.mock.calls[0][0]).toMatchObject({
      currency: 'BRL', destination_account_id: 'acc-broker', destination_amount: 36,
    })
  })

  it('opens an existing transfer as a transfer and turns it back into an expense', async () => {
    const { user, onSave } = renderForm(savingsPlan())
    expect(screen.getByDisplayValue(t('fork.recurringTransfers.typeTransfer'))).toBeInTheDocument()
    expect(fromSelect()).toHaveValue('acc-checking')
    expect(toSelect()).toHaveValue('acc-savings')

    await user.selectOptions(screen.getByDisplayValue(t('fork.recurringTransfers.typeTransfer')), 'debit')
    await user.click(screen.getByRole('button', { name: t('common.save') }))
    expect(onSave.mock.calls[0][0]).toMatchObject({ type: 'debit', destination_account_id: null })
  })

  it('clears the destination when it becomes the source', async () => {
    const { user, onSave } = renderForm(savingsPlan())
    await user.selectOptions(fromSelect(), 'acc-savings')
    expect(toSelect()).toHaveValue('')
    await user.click(screen.getByRole('button', { name: t('common.save') }))
    expect(onSave).not.toHaveBeenCalled()
  })
})

describe('RecurringForm with a closed account', () => {
  it('keeps a closed destination so the transfer can still be stopped', async () => {
    const { user, onSave } = renderForm(savingsPlan({ destination_account_id: 'acc-gone', destination_amount: '36.00' }))
    expect(toSelect()).toHaveValue('acc-gone')
    expect(within(toSelect()).getByRole('option', { name: t('fork.recurringTransfers.unavailableAccount') })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('common.save') }))
    expect(onSave.mock.calls[0][0]).toMatchObject({ destination_account_id: 'acc-gone', destination_amount: 36 })
  })
})

describe('isProjectedTransferLeg', () => {
  it('tells a transfer leg from an upstream transfer-like row', () => {
    expect(isProjectedTransferLeg({ category_treat_as_transfer: true, category_id: null })).toBe(true)
    expect(isProjectedTransferLeg({ category_treat_as_transfer: true, category_id: 'cat-card-payment' })).toBe(false)
    expect(isProjectedTransferLeg({ category_treat_as_transfer: false, category_id: null })).toBe(false)
  })
})

describe('TransferRoute', () => {
  it('names both accounts', () => {
    renderWithProviders(<TransferRoute schedule={savingsPlan()} accounts={accounts} />)
    expect(screen.getByLabelText(t('fork.recurringTransfers.route', { from: 'Checking', to: 'Savings' }))).toBeInTheDocument()
  })
})
