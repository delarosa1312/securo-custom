import { useState, type ReactNode } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { useDateLocale } from '@/hooks/use-display-locale'
import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { extractApiError } from '@/lib/api-errors'
import { todayInTimezone } from '@/lib/date-utils'

import { recurringLifecycle } from './api'
import type { LifecycleRecurring } from './edit-scope'
import { showDate, showDates, useRefreshAfterLifecycle, useRowItems } from './format'
import type { RowImpact } from './types'

export type SeriesAction = 'end' | 'delete' | 'purge' | 'restore'

/** Fork F-005: End series, Delete (to Deleted), Delete permanently and Restore, each previewed first. */
export function SeriesActionDialog({
  schedule,
  action,
  onClose,
}: {
  schedule: LifecycleRecurring | null
  action: SeriesAction | null
  onClose: () => void
}) {
  return (
    <Dialog open={Boolean(schedule && action)} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogContent className="sm:max-w-md" data-testid={action ? `series-${action}-dialog` : undefined}>
        {schedule && action === 'end' && <EndForm schedule={schedule} onClose={onClose} />}
        {schedule && action === 'delete' && <TrashForm schedule={schedule} onClose={onClose} />}
        {schedule && action === 'purge' && <PurgeForm schedule={schedule} onClose={onClose} />}
        {schedule && action === 'restore' && <RestoreForm schedule={schedule} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  )
}

function useApply(run: () => Promise<unknown>, done: string, onClose: () => void) {
  const { t } = useTranslation()
  const refresh = useRefreshAfterLifecycle()
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      refresh()
      toast.success(done)
      onClose()
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })
}

function Frame({
  title,
  confirm,
  destructive,
  ready,
  pending,
  error,
  onConfirm,
  onClose,
  children,
}: {
  title: string
  confirm: string
  destructive?: boolean
  ready: boolean
  pending: boolean
  error: unknown
  onConfirm: () => void
  onClose: () => void
  children: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-4">
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription className="sr-only">{title}</DialogDescription>
      </DialogHeader>
      <div className="space-y-3 text-sm">
        {error ? (
          <p className="text-destructive text-xs">{extractApiError(error, t('common.error'))}</p>
        ) : ready ? (
          children
        ) : (
          <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
        <Button
          variant={destructive ? 'destructive' : 'default'}
          disabled={!ready || pending || Boolean(error)}
          onClick={onConfirm}
          data-testid="series-confirm"
        >
          {pending ? t('common.loading') : confirm}
        </Button>
      </DialogFooter>
    </div>
  )
}

/** Q17: scheduled rows after the date go too unless unticked; the label names them. Shown only when there are some. */
function DeleteScheduled({
  labelKey,
  rows,
  currency,
  checked,
  onChange,
}: {
  labelKey: 'end' | 'trash'
  rows: RowImpact[]
  currency: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  const { t } = useTranslation()
  const items = useRowItems(currency)
  if (!rows.length) return null
  return (
    <label className="flex items-start gap-2 cursor-pointer">
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        data-testid="delete-scheduled"
      />
      <span>{t(`fork.recurringLifecycle.${labelKey}.deleteScheduled`, { count: rows.length, items: items(rows) })}</span>
    </label>
  )
}

function EndForm({ schedule, onClose }: { schedule: LifecycleRecurring; onClose: () => void }) {
  const { t } = useTranslation()
  const dateLocale = useDateLocale()
  const today = todayInTimezone(useEffectiveTimezone())
  const [lastDate, setLastDate] = useState(today < schedule.start_date ? schedule.start_date : today)
  const [deleteScheduled, setDeleteScheduled] = useState(true)
  const valid = lastDate >= schedule.start_date
  const preview = useQuery({
    queryKey: ['recurring-lifecycle-preview', 'end', schedule.id, lastDate],
    queryFn: () => recurringLifecycle.end(schedule.id, { last_date: lastDate, delete_scheduled: false, preview: true }),
    enabled: valid,
    retry: false,
    gcTime: 0,
  })
  const apply = useApply(
    () => recurringLifecycle.end(schedule.id, { last_date: lastDate, delete_scheduled: deleteScheduled, preview: false }),
    t('fork.recurringLifecycle.end.done'),
    onClose,
  )
  return (
    <Frame
      title={t('fork.recurringLifecycle.end.title', { description: schedule.description })}
      confirm={t('fork.recurringLifecycle.end.confirm')}
      ready={valid && Boolean(preview.data)}
      pending={apply.isPending}
      error={preview.error}
      onConfirm={() => apply.mutate()}
      onClose={onClose}
    >
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">{t('fork.recurringLifecycle.end.lastDate')}</span>
        <DatePickerInput value={lastDate} onChange={setLastDate} className="h-8" />
      </div>
      <p>{t('fork.recurringLifecycle.end.body', { date: showDate(lastDate, dateLocale, true) })}</p>
      {preview.data && (
        <DeleteScheduled
          labelKey="end"
          rows={preview.data.impact.removed}
          currency={schedule.currency}
          checked={deleteScheduled}
          onChange={setDeleteScheduled}
        />
      )}
    </Frame>
  )
}

function TrashForm({ schedule, onClose }: { schedule: LifecycleRecurring; onClose: () => void }) {
  const { t } = useTranslation()
  const [deleteScheduled, setDeleteScheduled] = useState(true)
  const preview = useQuery({
    queryKey: ['recurring-lifecycle-preview', 'trash', schedule.id],
    queryFn: () => recurringLifecycle.trash(schedule.id, { delete_scheduled: false, preview: true }),
    retry: false,
    gcTime: 0,
  })
  const apply = useApply(
    () => recurringLifecycle.trash(schedule.id, { delete_scheduled: deleteScheduled, preview: false }),
    t('fork.recurringLifecycle.trash.done'),
    onClose,
  )
  const impact = preview.data?.impact
  const removed = impact?.removed ?? []
  // history counts every row of the series; the ones deleted with it do not stay.
  const kept = (impact?.history ?? 0) - (deleteScheduled ? removed.length : 0)
  return (
    <Frame
      title={t('fork.recurringLifecycle.trash.title', { description: schedule.description })}
      confirm={t('fork.recurringLifecycle.trash.confirm')}
      destructive
      ready={Boolean(impact)}
      pending={apply.isPending}
      error={preview.error}
      onConfirm={() => apply.mutate()}
      onClose={onClose}
    >
      <p>{t('fork.recurringLifecycle.trash.body', { count: kept })}</p>
      <DeleteScheduled
        labelKey="trash"
        rows={removed}
        currency={schedule.currency}
        checked={deleteScheduled}
        onChange={setDeleteScheduled}
      />
    </Frame>
  )
}

function PurgeForm({ schedule, onClose }: { schedule: LifecycleRecurring; onClose: () => void }) {
  const { t } = useTranslation()
  const preview = useQuery({
    queryKey: ['recurring-lifecycle-preview', 'purge', schedule.id],
    queryFn: () => recurringLifecycle.purge(schedule.id, true),
    retry: false,
    gcTime: 0,
  })
  const apply = useApply(
    () => recurringLifecycle.purge(schedule.id, false),
    t('fork.recurringLifecycle.purge.done'),
    onClose,
  )
  return (
    <Frame
      title={t('fork.recurringLifecycle.purge.title', { description: schedule.description })}
      confirm={t('fork.recurringLifecycle.purge.confirm')}
      destructive
      ready={Boolean(preview.data)}
      pending={apply.isPending}
      error={preview.error}
      onConfirm={() => apply.mutate()}
      onClose={onClose}
    >
      <p>{t('fork.recurringLifecycle.purge.body', { count: preview.data?.kept_transactions ?? 0 })}</p>
    </Frame>
  )
}

function RestoreForm({ schedule, onClose }: { schedule: LifecycleRecurring; onClose: () => void }) {
  const { t } = useTranslation()
  const dateLocale = useDateLocale()
  const preview = useQuery({
    queryKey: ['recurring-lifecycle-preview', 'restore', schedule.id],
    queryFn: () => recurringLifecycle.restore(schedule.id, true),
    retry: false,
    gcTime: 0,
  })
  const apply = useApply(
    () => recurringLifecycle.restore(schedule.id, false),
    t('fork.recurringLifecycle.restore.done'),
    onClose,
  )
  const plan = preview.data
  return (
    <Frame
      title={t('fork.recurringLifecycle.restore.title', { description: schedule.description })}
      confirm={t('fork.recurringLifecycle.restore.confirm')}
      ready={Boolean(plan)}
      pending={apply.isPending}
      error={preview.error}
      onConfirm={() => apply.mutate()}
      onClose={onClose}
    >
      {schedule.deleted_at && (
        <p>{t('fork.recurringLifecycle.restore.deleted', { date: showDate(schedule.deleted_at.slice(0, 10), dateLocale, true) })}</p>
      )}
      {plan && plan.skipped.length > 0 && (
        <p>{t('fork.recurringLifecycle.restore.skipped', { count: plan.skipped.length, dates: showDates(plan.skipped, dateLocale) })}</p>
      )}
      {plan && (
        <p>
          {plan.next_occurrence
            ? t('fork.recurringLifecycle.restore.next', { date: showDate(plan.next_occurrence, dateLocale, true) })
            : t('fork.recurringLifecycle.restore.inactive')}
        </p>
      )}
    </Frame>
  )
}
