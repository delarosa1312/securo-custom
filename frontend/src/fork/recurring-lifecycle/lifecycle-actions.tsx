import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { recurring as recurringApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import type { RecurringTransaction } from '@/types'

import { ChangeFromDialog } from './change-from-dialog'
import { planEdit, type EditPlan, type LifecycleRecurring } from './edit-scope'
import { useRefreshAfterLifecycle } from './format'
import type { RowAction } from './list-parts'
import { LinkOccurrenceDialog, OccurrencesDialog, RecordOccurrenceDialog, type OccurrenceTarget } from './occurrence-dialogs'
import { SeriesActionDialog, type SeriesAction } from './series-dialogs'

/**
 * Fork F-005: what the Recurring page's row menu opens. The page keeps its own edit form and price
 * history; everything else (occurrences, pause, end, delete, restore, from-when) lives here.
 */
export function useLifecycleActions({
  canWrite,
  onEdit,
  onPriceHistory,
  onEditDone,
}: {
  canWrite: boolean
  onEdit: (rt: LifecycleRecurring) => void
  onPriceHistory: (rt: LifecycleRecurring) => void
  onEditDone: () => void
}) {
  const { t } = useTranslation()
  const refresh = useRefreshAfterLifecycle()
  const [occurrencesOf, setOccurrencesOf] = useState<LifecycleRecurring | null>(null)
  const [recording, setRecording] = useState<{ target: OccurrenceTarget; edit: boolean } | null>(null)
  const [linking, setLinking] = useState<OccurrenceTarget | null>(null)
  const [series, setSeries] = useState<{ rt: LifecycleRecurring; action: SeriesAction } | null>(null)
  const [scope, setScope] = useState<{ rt: LifecycleRecurring; plan: EditPlan } | null>(null)
  const setActive = useMutation({
    mutationFn: ({ rt, active }: { rt: LifecycleRecurring; active: boolean }) =>
      recurringApi.update(rt.id, { is_active: active }),
    onSuccess: (_, { active }) => {
      refresh()
      toast.success(t(`fork.recurringLifecycle.${active ? 'resumed' : 'paused'}`))
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })

  const handle = (rt: LifecycleRecurring, action: RowAction) => {
    if (action === 'edit') onEdit(rt)
    else if (action === 'priceHistory') onPriceHistory(rt)
    else if (action === 'occurrences') setOccurrencesOf(rt)
    else if (action === 'pause' || action === 'resume') setActive.mutate({ rt, active: action === 'resume' })
    else setSeries({ rt, action })
  }

  /** Q16: true when the edit waits for "Apply the change from when?" instead of saving at once. */
  const askScope = (rt: LifecycleRecurring, data: Partial<RecurringTransaction>): boolean => {
    const plan = planEdit(rt, data)
    if (!plan.askScope) return false
    setScope({ rt, plan })
    return true
  }

  const dialogs = (
    <>
      <OccurrencesDialog
        schedule={occurrencesOf}
        canWrite={canWrite}
        onClose={() => setOccurrencesOf(null)}
        onRecord={(target, edit) => setRecording({ target, edit })}
        onLink={setLinking}
      />
      <RecordOccurrenceDialog target={recording?.target ?? null} edit={recording?.edit ?? false} onClose={() => setRecording(null)} />
      <LinkOccurrenceDialog target={linking} onClose={() => setLinking(null)} />
      <SeriesActionDialog schedule={series?.rt ?? null} action={series?.action ?? null} onClose={() => setSeries(null)} />
      <ChangeFromDialog
        schedule={scope?.rt ?? null}
        plan={scope?.plan ?? null}
        onClose={() => setScope(null)}
        onDone={() => {
          setScope(null)
          onEditDone()
        }}
      />
    </>
  )
  return { handle, askScope, dialogs, busy: setActive.isPending }
}
