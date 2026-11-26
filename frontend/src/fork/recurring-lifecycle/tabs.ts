import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { recurringLifecycle } from './api'
import type { LifecycleRecurring } from './edit-scope'

export type StatusTab = 'active' | 'inactive' | 'deleted'

export function tabOf(rt: LifecycleRecurring): StatusTab {
  if (rt.deleted_at) return 'deleted'
  return rt.is_active ? 'active' : 'inactive'
}

/**
 * Fork F-005 (Q15): the page's own list (active and paused) plus the deleted schedules, split into
 * Active, Inactive and Deleted.
 */
export function useRecurringTabs(live: LifecycleRecurring[] | undefined) {
  const [tab, setTab] = useState<StatusTab>('active')
  const deleted = useQuery({
    queryKey: ['recurring', 'lifecycle', 'deleted'],
    queryFn: () => recurringLifecycle.list('deleted') as Promise<LifecycleRecurring[]>,
  })
  const all = useMemo(() => [...(live ?? []), ...(deleted.data ?? [])], [live, deleted.data])
  const counts = useMemo(() => {
    const out: Record<StatusTab, number> = { active: 0, inactive: 0, deleted: 0 }
    for (const rt of all) out[tabOf(rt)] += 1
    return out
  }, [all])
  const rows = useMemo(() => all.filter((rt) => tabOf(rt) === tab), [all, tab])
  return { all, rows, counts, tab, setTab }
}
