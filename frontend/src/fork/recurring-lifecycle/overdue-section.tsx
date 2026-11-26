import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { extractApiError } from '@/lib/api-errors'
import { isTransferSchedule } from '@/fork/recurring-transfers/transfer'

import { recurringLifecycle } from './api'
import type { LifecycleRecurring } from './edit-scope'
import { showDate, signedAmount, useRefreshAfterLifecycle } from './format'
import { LinkOccurrenceDialog, RecordOccurrenceDialog, type OccurrenceTarget } from './occurrence-dialogs'
import type { OverdueOccurrence } from './types'

export const OVERDUE_ANCHOR = 'overdue'

function useOverdue() {
  return useQuery({
    queryKey: ['recurring', 'lifecycle', 'overdue'],
    queryFn: recurringLifecycle.overdue,
  })
}

/**
 * Fork F-005 (Q12, Q18): occurrences dated today or earlier that are not rows. They count nowhere
 * until recorded, linked to a typed transaction or skipped.
 */
export function OverdueSection({ schedules, canWrite }: { schedules: LifecycleRecurring[]; canWrite: boolean }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const refresh = useRefreshAfterLifecycle()
  const location = useLocation()
  const ref = useRef<HTMLDivElement>(null)
  const { data } = useOverdue()
  const [recording, setRecording] = useState<OccurrenceTarget | null>(null)
  const [linking, setLinking] = useState<OccurrenceTarget | null>(null)
  const skip = useMutation({
    mutationFn: (row: OverdueOccurrence) => recurringLifecycle.skip(row.recurring_id, row.nominal_date),
    onSuccess: () => {
      refresh()
      toast.success(t('fork.recurringLifecycle.occurrences.skipped'))
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })
  const rows = data ?? []
  const count = rows.length

  // The Upcoming line links here.
  useEffect(() => {
    if (count && location.hash === `#${OVERDUE_ANCHOR}`) ref.current?.scrollIntoView({ block: 'start' })
  }, [count, location.hash])

  if (!count) return null
  const target = (row: OverdueOccurrence): OccurrenceTarget => {
    const schedule = schedules.find((rt) => rt.id === row.recurring_id)
    return {
      schedule: schedule ?? {
        id: row.recurring_id,
        description: row.description,
        currency: row.currency,
        type: row.type,
        destination_account_id: row.destination_account_id,
        destination_amount: null,
      },
      nominalDate: row.nominal_date,
      date: row.date,
      amount: row.amount,
      destinationAmount: schedule?.destination_amount ?? null,
    }
  }

  return (
    <div
      ref={ref}
      id={OVERDUE_ANCHOR}
      className="mx-4 sm:mx-5 mt-3 rounded-lg border border-amber-200 bg-amber-50/70 dark:border-amber-900/60 dark:bg-amber-950/30"
      data-testid="overdue-section"
    >
      <p className="flex items-start gap-2 px-3 pt-3 pb-2 text-sm font-medium text-amber-800 dark:text-amber-300">
        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
        {t('fork.recurringLifecycle.overdue.title', { count })}
      </p>
      <ul className="divide-y divide-amber-200/70 dark:divide-amber-900/50">
        {rows.map((row) => {
          const transfer = isTransferSchedule(row)
          return (
            <li key={`${row.recurring_id}-${row.nominal_date}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2" data-testid="overdue-row">
              <span className="w-24 text-xs tabular-nums text-muted-foreground">{showDate(row.date, dateLocale, true)}</span>
              <span className="min-w-0 flex-1 truncate text-sm">{row.description}</span>
              <span className="text-sm font-semibold tabular-nums">
                {mask(signedAmount(row.amount, row.currency, row.type, locale, transfer))}
              </span>
              {canWrite && (
                <span className="flex gap-1">
                  <Button size="sm" variant="outline" className="h-7" onClick={() => setRecording(target(row))}>
                    {t('fork.recurringLifecycle.overdue.record')}
                  </Button>
                  {!transfer && (
                    <Button size="sm" variant="outline" className="h-7" onClick={() => setLinking(target(row))}>
                      {t('fork.recurringLifecycle.overdue.link')}
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" className="h-7" disabled={skip.isPending} onClick={() => skip.mutate(row)}>
                    {t('fork.recurringLifecycle.overdue.skip')}
                  </Button>
                </span>
              )}
            </li>
          )
        })}
      </ul>
      <RecordOccurrenceDialog target={recording} edit={false} onClose={() => setRecording(null)} />
      <LinkOccurrenceDialog target={linking} onClose={() => setLinking(null)} />
    </div>
  )
}

/** Q18: one line in the Upcoming widget, within the dashboard's active accounts. */
export function UpcomingOverdueLine({ activeAccountIds }: { activeAccountIds: string[] | null | undefined }) {
  const { t } = useTranslation()
  const { data } = useOverdue()
  const rows = (data ?? []).filter(
    (row) =>
      !activeAccountIds ||
      (row.account_id != null && activeAccountIds.includes(row.account_id)) ||
      (row.destination_account_id != null && activeAccountIds.includes(row.destination_account_id)),
  )
  if (!rows.length) return null
  return (
    <p className="mb-3 flex items-center gap-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300" data-testid="upcoming-overdue">
      <AlertTriangle size={14} className="shrink-0" />
      <span className="min-w-0 flex-1">{t('fork.recurringLifecycle.overdue.upcoming', { count: rows.length })}</span>
      <Link to={`/recurring#${OVERDUE_ANCHOR}`} className="font-medium underline-offset-2 hover:underline">
        {t('fork.recurringLifecycle.overdue.review')}
      </Link>
    </p>
  )
}
