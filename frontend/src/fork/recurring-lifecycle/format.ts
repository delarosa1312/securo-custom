import { useQueryClient } from '@tanstack/react-query'

import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency, ltrAmount } from '@/lib/format'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'

import type { RowImpact } from './types'

/** A date as the page shows it ("Dec 10" or "Dec 10, 2026"). */
export function showDate(iso: string, dateLocale: string, withYear = false): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale, {
    day: '2-digit',
    month: 'short',
    ...(withYear ? { year: 'numeric' } : {}),
  })
}

export function showDates(dates: string[], dateLocale: string, max = 4): string {
  const shown = dates.slice(0, max).map((d) => showDate(d, dateLocale))
  return dates.length > max ? `${shown.join(', ')}, ...` : shown.join(', ')
}

/** A signed amount: expenses and money leaving show a minus sign. */
export function signedAmount(amount: number | string, currency: string, type: string, locale: string, transfer = false) {
  const sign = transfer ? '' : type === 'credit' ? '+' : '−'
  return ltrAmount(`${sign}${formatCurrency(Number(amount), currency, locale)}`)
}

/** Everything a lifecycle action can change: schedules, rows, projections and the overdue list. */
export function useRefreshAfterLifecycle() {
  const queryClient = useQueryClient()
  return () => {
    invalidateFinancialQueries(queryClient)
    queryClient.invalidateQueries({ queryKey: ['recurring'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }
}

/** "Dec 25, -120.00" for each row, as the confirmation dialogs list them. */
export function useRowItems(currency: string) {
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  return (rows: RowImpact[], max = 4) => {
    const shown = rows
      .slice(0, max)
      .map((row) => `${showDate(row.date, dateLocale)}, ${mask(formatCurrency(Number(row.amount), currency, locale))}`)
    return rows.length > max ? `${shown.join('; ')}; ...` : shown.join('; ')
  }
}
