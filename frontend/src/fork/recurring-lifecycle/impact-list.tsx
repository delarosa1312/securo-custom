import { useTranslation } from 'react-i18next'

import { useDateLocale } from '@/hooks/use-display-locale'

import { showDates, useRowItems } from './format'
import type { Impact, RowImpact } from './types'

/** What a change does to the rows the series already wrote (Q16: listed before anything is written). */
export function ImpactList({ impact, currency }: { impact: Impact; currency: string }) {
  const { t } = useTranslation()
  const dateLocale = useDateLocale()
  const items = useRowItems(currency)
  const dates = (rows: RowImpact[]) => showDates(rows.map((row) => row.date), dateLocale)
  const lines: string[] = []
  if (impact.follow.length) {
    lines.push(t('fork.recurringLifecycle.changeFrom.follow', { count: impact.follow.length, dates: dates(impact.follow) }))
  }
  if (impact.removed.length) {
    lines.push(t('fork.recurringLifecycle.changeFrom.removed', { count: impact.removed.length, dates: dates(impact.removed) }))
  }
  if (impact.edited.length) {
    lines.push(t('fork.recurringLifecycle.changeFrom.edited', { count: impact.edited.length, items: items(impact.edited) }))
  }
  if (impact.skipped_dropped.length) {
    lines.push(t('fork.recurringLifecycle.changeFrom.skippedDropped', { dates: showDates(impact.skipped_dropped, dateLocale) }))
  }
  if (!lines.length) lines.push(t('fork.recurringLifecycle.changeFrom.nothing'))
  lines.push(t('fork.recurringLifecycle.changeFrom.history'))
  return (
    <ul className="list-disc ps-5 space-y-1 text-xs text-foreground">
      {lines.map((line) => <li key={line}>{line}</li>)}
    </ul>
  )
}
