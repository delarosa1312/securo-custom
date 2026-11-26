// F-006 (#8, Q13): an open invoice in the calendar reads "Invoice <number> (expected)" in the user's language.
import { afterEach, describe, expect, it } from 'vitest'

import { calendarItemDescription } from '@/fork/projection-consistency/calendar-item-description'
import { i18n } from '@/test/utils'
import type { TransactionCalendarItem } from '@/types'

function item(extra: Partial<TransactionCalendarItem>): TransactionCalendarItem {
  return {
    kind: 'projected', id: null, recurring_id: null, date: '2026-11-27', description: 'Invoice NF-7 (expected)',
    amount: 400, amount_primary: 400, currency: 'BRL', type: 'credit', account_id: null, account_name: null,
    category_id: null, category_name: null, category_icon: null, category_color: null, status: null,
    source: 'invoice', transfer_pair_id: null, is_transfer: false, is_ignored: false, exclude_from_pnl: false,
    invoice_id: 'synthetic', invoice_number: 'NF-7', ...extra,
  }
}

afterEach(async () => {
  await i18n.changeLanguage('en')
})

describe('calendarItemDescription', () => {
  it('names an open invoice by its number in English and pt-BR', async () => {
    expect(calendarItemDescription(item({}), i18n.t)).toBe('Invoice NF-7 (expected)')
    expect(calendarItemDescription(item({ invoice_number: null }), i18n.t)).toBe('Invoice (expected)')
    await i18n.changeLanguage('pt-BR')
    expect(calendarItemDescription(item({}), i18n.t)).toBe('Cobrança NF-7 (prevista)')
    expect(calendarItemDescription(item({ invoice_number: null }), i18n.t)).toBe('Cobrança (prevista)')
  })

  it('keeps every other row as the server sent it', () => {
    expect(calendarItemDescription(item({ source: 'recurring', description: 'Rent' }), i18n.t)).toBe('Rent')
  })
})
