import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import type { Account } from '@/types'
import './test-i18n'

vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@/components/ui/date-picker-input', () => ({
  DatePickerInput: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <input aria-label="balance date" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}))

import { AccountDialog } from '@/pages/accounts'

function account(overrides: Partial<Account>): Account {
  return {
    id: 'acc-1',
    connection_id: null,
    name: 'Reserve',
    display_name: null,
    type: 'savings',
    balance: '1000.00' as unknown as number,
    currency: 'BRL',
    balance_date: '2026-08-01',
    credit_limit: null,
    statement_close_day: null,
    payment_due_day: null,
    is_closed: false,
    include_in_available: true,
    ...overrides,
  } as Account
}

function renderDialog(acc: Account | null) {
  const onSave = vi.fn()
  renderWithProviders(<AccountDialog open onClose={vi.fn()} account={acc} onSave={onSave} loading={false} />)
  return onSave
}

const setting = () => screen.queryByRole('switch', { name: t('fork.availableBalance.setting') })
const save = () => fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

describe('Include in available balance', () => {
  it('turns off and sends the setting', () => {
    const onSave = renderDialog(account({}))
    expect(screen.getByText(t('fork.availableBalance.settingHint'))).toBeInTheDocument()
    expect(setting()).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(setting()!)
    expect(setting()).toHaveAttribute('aria-checked', 'false')
    save()
    expect(onSave.mock.calls[0][0]).toMatchObject({ include_in_available: false })
  })

  it('opens off for an account that is left out, and turns back on', () => {
    const onSave = renderDialog(account({ include_in_available: false }))
    expect(setting()).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(setting()!)
    save()
    expect(onSave.mock.calls[0][0]).toMatchObject({ include_in_available: true })
  })

  it('leaves the setting out of an unrelated edit', () => {
    const onSave = renderDialog(account({ include_in_available: false }))
    fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Emergency fund' } })
    save()
    expect(onSave.mock.calls[0][0]).toMatchObject({ name: 'Emergency fund' })
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('include_in_available')
  })

  it.each(['checking', 'wallet'])('is offered on a %s account', (type) => {
    renderDialog(account({ type }))
    expect(setting()).toBeInTheDocument()
  })

  it.each(['credit_card', 'loan', 'investment'])('is not offered on a %s account', (type) => {
    renderDialog(account({ type }))
    expect(setting()).not.toBeInTheDocument()
  })

  it('is offered on a connected account', () => {
    renderDialog(account({ connection_id: 'conn-1' }))
    expect(setting()).toBeInTheDocument()
  })

  it('is not offered when adding an account', () => {
    renderDialog(null)
    expect(setting()).not.toBeInTheDocument()
  })

  it('shows in Brazilian Portuguese', async () => {
    const { i18n } = await import('@/test/utils')
    await i18n.changeLanguage('pt-BR')
    try {
      renderDialog(account({}))
      expect(screen.getByText('Incluir no saldo disponível')).toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('en')
    }
  })
})
