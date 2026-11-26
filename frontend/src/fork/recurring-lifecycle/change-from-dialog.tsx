import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { recurring as recurringApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import type { RecurringTransaction } from '@/types'

import { recurringLifecycle } from './api'
import type { EditPlan, LifecycleRecurring } from './edit-scope'
import { showDate, useRefreshAfterLifecycle } from './format'
import { ImpactList } from './impact-list'

/** Fork F-005 (Q16): after Save on a changed schedule, from when the change applies. */
export function ChangeFromDialog({
  schedule,
  plan,
  onClose,
  onDone,
}: {
  schedule: LifecycleRecurring | null
  plan: EditPlan | null
  onClose: () => void
  onDone: () => void
}) {
  return (
    <Dialog open={Boolean(schedule && plan)} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-lg" data-testid="change-from-dialog">
        {schedule && plan && <ChangeFromForm key={schedule.id} schedule={schedule} plan={plan} onClose={onClose} onDone={onDone} />}
      </DialogContent>
    </Dialog>
  )
}

function ChangeFromForm({
  schedule,
  plan,
  onClose,
  onDone,
}: {
  schedule: LifecycleRecurring
  plan: EditPlan
  onClose: () => void
  onDone: () => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const refresh = useRefreshAfterLifecycle()
  const [mode, setMode] = useState<'from' | 'whole'>(plan.canSplit ? 'from' : 'whole')
  const [fromDate, setFromDate] = useState(schedule.next_occurrence)
  const split = mode === 'from'
  const body = {
    from_date: split ? fromDate : null,
    changes: (split ? plan.values : { ...plan.values, ...plan.series }) as Partial<RecurringTransaction>,
  }
  const preview = useQuery({
    queryKey: ['recurring-lifecycle-preview', 'change', schedule.id, body],
    queryFn: () => recurringLifecycle.changeFrom(schedule.id, { ...body, preview: true }),
    enabled: !split || fromDate > schedule.start_date,
    retry: false,
    gcTime: 0,
  })
  const apply = useMutation({
    mutationFn: async () => {
      const result = await recurringLifecycle.changeFrom(schedule.id, { ...body, preview: false })
      // A new end date or a pause belongs to the part that goes on.
      if (split && result.schedule && Object.keys(plan.series).length > 0) {
        await recurringApi.update(result.schedule.id, plan.series)
      }
      return result
    },
    onSuccess: () => {
      refresh()
      toast.success(t('fork.recurringLifecycle.changeFrom.done'))
      onDone()
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })
  const money = (value: unknown) => mask(formatCurrency(Number(value), schedule.currency, locale))
  const amountChanged = plan.values.amount !== undefined
  const impact = preview.data?.impact
  const nothingMoves = impact != null && !impact.follow.length && !impact.removed.length && !impact.edited.length

  return (
    <div className="space-y-4">
      <DialogHeader>
        <DialogTitle>{t('fork.recurringLifecycle.changeFrom.title')}</DialogTitle>
        <DialogDescription>
          {schedule.description}
          {amountChanged && `: ${money(schedule.amount)} → ${money(plan.values.amount)}`}
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-3" role="radiogroup">
        {plan.canSplit && (
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="radio" name="change-scope" className="mt-1" checked={split} onChange={() => setMode('from')} />
            <span className="text-sm">
              <span className="font-medium">{t('fork.recurringLifecycle.changeFrom.fromOption', { date: showDate(fromDate, dateLocale, true) })}</span>
              <span className="block text-xs text-muted-foreground">
                {amountChanged
                  ? t('fork.recurringLifecycle.changeFrom.fromHelpAmount', { amount: money(schedule.amount) })
                  : t('fork.recurringLifecycle.changeFrom.fromHelp')}
              </span>
              {split && (
                <span className="mt-2 flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{t('fork.recurringLifecycle.changeFrom.fromDate')}</span>
                  <DatePickerInput value={fromDate} onChange={setFromDate} className="h-8" />
                </span>
              )}
            </span>
          </label>
        )}
        <label className="flex items-start gap-2 cursor-pointer">
          <input type="radio" name="change-scope" className="mt-1" checked={!split} onChange={() => setMode('whole')} />
          <span className="text-sm">
            <span className="font-medium">{t('fork.recurringLifecycle.changeFrom.whole')}</span>
            <span className="block text-xs text-muted-foreground">
              {t('fork.recurringLifecycle.changeFrom.wholeHelp')}
              {nothingMoves && !split && ` ${t('fork.recurringLifecycle.changeFrom.wholeUnchanged')}`}
            </span>
          </span>
        </label>
      </div>
      <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm" data-testid="change-impact">
        <p className="font-medium mb-1">{t('fork.recurringLifecycle.changeFrom.impactTitle')}</p>
        {preview.isError ? (
          <p className="text-destructive text-xs">{extractApiError(preview.error, t('common.error'))}</p>
        ) : !impact ? (
          <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
        ) : (
          <ImpactList impact={impact} currency={schedule.currency} />
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
        <Button disabled={!impact || preview.isError || apply.isPending} onClick={() => apply.mutate()} data-testid="change-apply">
          {apply.isPending ? t('common.loading') : t('fork.recurringLifecycle.changeFrom.apply')}
        </Button>
      </DialogFooter>
    </div>
  )
}
