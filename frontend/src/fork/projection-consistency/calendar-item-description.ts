// F-006 (#8, Q13): the calendar lists an open invoice it counts as "Invoice <number> (expected)".
// The server sends that in English; the web app shows it in the user's language.
import type { TFunction } from 'i18next'

import type { TransactionCalendarItem } from '@/types'

export function calendarItemDescription(item: TransactionCalendarItem, t: TFunction): string {
  if (item.source !== 'invoice') return item.description
  return item.invoice_number
    ? t('fork.projections.invoiceExpected', { number: item.invoice_number })
    : t('fork.projections.invoiceExpectedNoNumber')
}
