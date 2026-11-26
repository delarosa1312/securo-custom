import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { extractApiError } from '@/lib/api-errors'
import { formatAmountInput, parseAmountInput } from '@/lib/format'
import { cn } from '@/lib/utils'
import { isTransferSchedule } from '@/fork/recurring-transfers/transfer'

import { recurringLifecycle } from './api'
import type { LifecycleRecurring } from './edit-scope'
import { showDate, signedAmount, useRefreshAfterLifecycle } from './format'
import type { Occurrence, OccurrenceState } from './types'

/** One occurrence to record or link: its schedule's values and its nominal date. */
export interface OccurrenceTarget {
  schedule: Pick<LifecycleRecurring, 'id' | 'description' | 'currency' | 'type' | 'destination_account_id' | 'destination_amount'>
  nominalDate: string
  date: string
  amount: string | number
  destinationAmount?: string | number | null
}

const STATE_STYLE: Record<OccurrenceState, string> = {
  recorded: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  edited: 'bg-violet-50 text-violet-700 border-violet-100',
  skipped: 'bg-muted text-muted-foreground border-border line-through',
  removed: 'bg-muted text-muted-foreground border-border',
  scheduled: 'bg-sky-50 text-sky-700 border-sky-100',
  overdue: 'bg-amber-50 text-amber-700 border-amber-200',
  past: 'bg-muted text-muted-foreground border-border',
}

function transactionLink(occurrence: Occurrence): string {
  return `/transactions?from=${occurrence.date}&to=${occurrence.date}&highlight=${occurrence.transaction_id}`
}

/** Fork F-005 (Q14): a schedule's occurrences, recent and to come, with what can be done to each. */
export function OccurrencesDialog({
  schedule,
  canWrite,
  onClose,
  onRecord,
  onLink,
}: {
  schedule: LifecycleRecurring | null
  canWrite: boolean
  onClose: () => void
  onRecord: (target: OccurrenceTarget, edit: boolean) => void
  onLink: (target: OccurrenceTarget) => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const refresh = useRefreshAfterLifecycle()
  const { data, isPending, isError } = useQuery({
    queryKey: ['recurring', 'lifecycle', 'occurrences', schedule?.id],
    queryFn: () => recurringLifecycle.occurrences(schedule!.id),
    enabled: Boolean(schedule),
  })
  const skip = useMutation({
    mutationFn: ({ nominal, undo }: { nominal: string; undo: boolean }) =>
      undo ? recurringLifecycle.unskip(schedule!.id, nominal) : recurringLifecycle.skip(schedule!.id, nominal),
    onSuccess: (_, { undo }) => {
      refresh()
      toast.success(t(`fork.recurringLifecycle.occurrences.${undo ? 'unskipped' : 'skipped'}`))
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })
  const editable = canWrite && schedule != null && !schedule.deleted_at
  const target = (occurrence: Occurrence): OccurrenceTarget => ({
    schedule: schedule!,
    nominalDate: occurrence.nominal_date,
    date: occurrence.date,
    amount: occurrence.amount,
    destinationAmount: occurrence.destination_amount,
  })
  const transfer = isTransferSchedule(schedule)

  return (
    <Dialog open={Boolean(schedule)} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-h-[90vh] flex flex-col overflow-hidden sm:max-w-2xl" data-testid="occurrences-dialog">
        <DialogHeader className="shrink-0">
          <DialogTitle>{t('fork.recurringLifecycle.occurrences.title', { description: schedule?.description ?? '' })}</DialogTitle>
          {editable && <DialogDescription>{t('fork.recurringLifecycle.occurrences.help')}</DialogDescription>}
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto">
          {isError ? (
            <p className="text-sm text-muted-foreground py-4">{t('common.error')}</p>
          ) : isPending ? (
            <p className="text-sm text-muted-foreground py-4">{t('common.loading')}</p>
          ) : !data?.length ? (
            <p className="text-sm text-muted-foreground py-4">{t('fork.recurringLifecycle.occurrences.empty')}</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted-foreground">
                  <th className="py-2 text-start font-medium">{t('fork.recurringLifecycle.occurrences.date')}</th>
                  <th className="py-2 text-start font-medium">{t('fork.recurringLifecycle.occurrences.amount')}</th>
                  <th className="py-2 text-start font-medium">{t('fork.recurringLifecycle.occurrences.state')}</th>
                  <th className="py-2"><span className="sr-only">{t('recurring.actions')}</span></th>
                </tr>
              </thead>
              <tbody>
                {data.map((occurrence) => (
                  <tr key={occurrence.nominal_date} className="border-b border-border last:border-0" data-testid="occurrence-row">
                    <td className="py-2 tabular-nums">
                      {showDate(occurrence.date, dateLocale, true)}
                      {occurrence.date !== occurrence.nominal_date && (
                        <span className="ms-1 text-xs text-muted-foreground">({showDate(occurrence.nominal_date, dateLocale)})</span>
                      )}
                    </td>
                    <td className="py-2 tabular-nums">
                      {mask(signedAmount(occurrence.amount, schedule!.currency, schedule!.type, locale, transfer))}
                    </td>
                    <td className="py-2">
                      <span className={cn('text-[11px] font-semibold px-2 py-0.5 rounded-full border', STATE_STYLE[occurrence.state])}>
                        {t(`fork.recurringLifecycle.occurrences.states.${occurrence.state}`)}
                      </span>
                    </td>
                    <td className="py-2 text-end">
                      <div className="flex flex-wrap justify-end gap-1">
                        {editable && (occurrence.state === 'scheduled' || occurrence.state === 'overdue') && (
                          <>
                            <Button size="sm" variant="outline" className="h-7" onClick={() => onRecord(target(occurrence), occurrence.state === 'scheduled')}>
                              {t(`fork.recurringLifecycle.occurrences.${occurrence.state === 'scheduled' ? 'edit' : 'record'}`)}
                            </Button>
                            {occurrence.state === 'overdue' && !transfer && (
                              <Button size="sm" variant="outline" className="h-7" onClick={() => onLink(target(occurrence))}>
                                {t('fork.recurringLifecycle.occurrences.link')}
                              </Button>
                            )}
                            <Button
                              size="sm" variant="ghost" className="h-7"
                              disabled={skip.isPending}
                              onClick={() => skip.mutate({ nominal: occurrence.nominal_date, undo: false })}
                            >
                              {t('fork.recurringLifecycle.occurrences.skip')}
                            </Button>
                          </>
                        )}
                        {editable && occurrence.state === 'skipped' && (
                          <Button
                            size="sm" variant="ghost" className="h-7"
                            disabled={skip.isPending}
                            onClick={() => skip.mutate({ nominal: occurrence.nominal_date, undo: true })}
                          >
                            {t('fork.recurringLifecycle.occurrences.undoSkip')}
                          </Button>
                        )}
                        {(occurrence.state === 'recorded' || occurrence.state === 'edited') && occurrence.transaction_id && (
                          <Button size="sm" variant="ghost" className="h-7" asChild>
                            <Link to={transactionLink(occurrence)}>{t('fork.recurringLifecycle.occurrences.openTransaction')}</Link>
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** Record one occurrence now: as scheduled (an overdue bill) or with its own amount and date (Edit). */
export function RecordOccurrenceDialog({
  target,
  edit,
  onClose,
}: {
  target: OccurrenceTarget | null
  edit: boolean
  onClose: () => void
}) {
  return (
    <Dialog open={Boolean(target)} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-md" data-testid="record-occurrence-dialog">
        {target && <RecordForm key={`${target.schedule.id}-${target.nominalDate}`} target={target} edit={edit} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  )
}

function RecordForm({ target, edit, onClose }: { target: OccurrenceTarget; edit: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const refresh = useRefreshAfterLifecycle()
  const [amount, setAmount] = useState(formatAmountInput(Number(target.amount), locale, 2, 2))
  const [date, setDate] = useState(target.date)
  const crossCurrency = target.schedule.destination_amount != null
  const [received, setReceived] = useState(
    target.destinationAmount != null ? formatAmountInput(Number(target.destinationAmount), locale, 2, 2) : '',
  )
  const parsed = parseAmountInput(amount, locale)
  const parsedReceived = parseAmountInput(received, locale)
  const record = useMutation({
    mutationFn: () => {
      const body: { amount?: string; date?: string; destination_amount?: string } = {}
      if (parsed != null && parsed !== Number(target.amount)) body.amount = parsed.toFixed(2)
      if (date && date !== target.date) body.date = date
      if (crossCurrency && parsedReceived != null && parsedReceived !== Number(target.destinationAmount)) {
        body.destination_amount = parsedReceived.toFixed(2)
      }
      return recurringLifecycle.record(target.schedule.id, target.nominalDate, body)
    },
    onSuccess: () => {
      refresh()
      toast.success(t('fork.recurringLifecycle.record.done'))
      onClose()
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })
  const title = t(`fork.recurringLifecycle.record.${edit ? 'titleEdit' : 'titleRecord'}`, {
    date: showDate(target.nominalDate, dateLocale, true),
  })
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (parsed == null || parsed <= 0) return
        record.mutate()
      }}
      className="space-y-4"
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{target.schedule.description}. {t('fork.recurringLifecycle.record.help')}</DialogDescription>
      </DialogHeader>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="occurrence-amount">{t('fork.recurringLifecycle.record.amount')}</Label>
          <Input id="occurrence-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        </div>
        <div className="space-y-2">
          <Label>{t('fork.recurringLifecycle.record.date')}</Label>
          <DatePickerInput value={date} onChange={setDate} className="w-full justify-start" />
        </div>
      </div>
      {crossCurrency && (
        <div className="space-y-2">
          <Label htmlFor="occurrence-received">{t('fork.recurringLifecycle.record.received')}</Label>
          <Input id="occurrence-received" inputMode="decimal" value={received} onChange={(e) => setReceived(e.target.value)} />
        </div>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={record.isPending || parsed == null || parsed <= 0}>
          {record.isPending ? t('common.loading') : t('fork.recurringLifecycle.record.save')}
        </Button>
      </DialogFooter>
    </form>
  )
}

/** Link an overdue occurrence to a transaction already typed by hand. */
export function LinkOccurrenceDialog({ target, onClose }: { target: OccurrenceTarget | null; onClose: () => void }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const refresh = useRefreshAfterLifecycle()
  const [chosen, setChosen] = useState<string | null>(null)
  const { data, isPending, isError } = useQuery({
    queryKey: ['recurring', 'lifecycle', 'link-candidates', target?.schedule.id, target?.nominalDate],
    queryFn: () => recurringLifecycle.linkCandidates(target!.schedule.id, target!.nominalDate),
    enabled: Boolean(target),
  })
  const link = useMutation({
    mutationFn: (transactionId: string) => recurringLifecycle.link(target!.schedule.id, target!.nominalDate, transactionId),
    onSuccess: () => {
      refresh()
      toast.success(t('fork.recurringLifecycle.link.done'))
      setChosen(null)
      onClose()
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })
  return (
    <Dialog open={Boolean(target)} onOpenChange={(open) => { if (!open) { setChosen(null); onClose() } }}>
      <DialogContent className="sm:max-w-lg" data-testid="link-occurrence-dialog">
        <DialogHeader>
          <DialogTitle>
            {target && t('fork.recurringLifecycle.link.title', { date: showDate(target.nominalDate, dateLocale, true) })}
          </DialogTitle>
          <DialogDescription>{target?.schedule.description}. {t('fork.recurringLifecycle.link.help')}</DialogDescription>
        </DialogHeader>
        {isError ? (
          <p className="text-sm text-muted-foreground">{t('common.error')}</p>
        ) : isPending ? (
          <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
        ) : !data?.length ? (
          <p className="text-sm text-muted-foreground">{t('fork.recurringLifecycle.link.empty')}</p>
        ) : (
          <ul className="border border-border rounded-lg divide-y divide-border max-h-72 overflow-y-auto" role="radiogroup">
            {data.map((candidate) => (
              <li key={candidate.id}>
                <label className="flex items-center gap-3 px-3 py-2 text-sm cursor-pointer hover:bg-muted">
                  <input
                    type="radio"
                    name="link-candidate"
                    checked={chosen === candidate.id}
                    onChange={() => setChosen(candidate.id)}
                  />
                  <span className="w-20 tabular-nums text-muted-foreground">{showDate(candidate.date, dateLocale)}</span>
                  <span className="min-w-0 flex-1 truncate">{candidate.description}</span>
                  <span className="tabular-nums font-medium">
                    {mask(signedAmount(candidate.amount, candidate.currency, target?.schedule.type ?? 'debit', locale))}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => { setChosen(null); onClose() }}>{t('common.cancel')}</Button>
          <Button disabled={!chosen || link.isPending} onClick={() => chosen && link.mutate(chosen)}>
            {link.isPending ? t('common.loading') : t('fork.recurringLifecycle.link.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
