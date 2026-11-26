import type { TFunction } from 'i18next'

import { api } from '@/lib/api'
import type { ProposalData } from '@/lib/agent-proposals'

import type { RecurringLifecycleKind } from './kinds'

// Fork F-005: the in-app cards for the recurring lifecycle proposal tools.

export { isRecurringLifecycleKind } from './kinds'

type Fmt = (amount: unknown, currency: unknown) => string
type Fields = Record<string, unknown>

export function recurringLifecycleTitle(kind: RecurringLifecycleKind, t: TFunction): string {
  return t(`fork.recurringLifecycle.proposal.${kind}`)
}

function changesText(changes: Fields, target: Fields): string {
  return Object.entries(changes)
    .map(([key, value]) => key in target
      ? `${key}: ${String(target[key] ?? '∅')} → ${String(value ?? '∅')}`
      : `${key}: ${String(value ?? '∅')}`)
    .join(', ') || '?'
}

export function recurringLifecycleSummary(
  kind: RecurringLifecycleKind,
  data: ProposalData,
  t: TFunction,
  fmt: Fmt,
): string {
  const d = data as Fields
  const target = (data.target || {}) as Fields
  const proposed = (data.proposed || {}) as Fields
  const occurrence = (target.occurrence || {}) as Fields
  const description = String(target.description ?? '?')
  const key = 'fork.recurringLifecycle.proposal'
  switch (kind) {
    case 'skip_occurrence':
    case 'unskip_occurrence':
      return t(`${key}.${kind === 'skip_occurrence' ? 'skip' : 'unskip'}`, {
        description, date: String(occurrence.nominal_date ?? '?'),
      })
    case 'record_occurrence':
      return t(`${key}.record`, {
        description,
        date: String(occurrence.nominal_date ?? '?'),
        amount: fmt(proposed.amount, target.currency),
        on: String(proposed.date ?? '?'),
      })
    case 'link_occurrence':
      return t(`${key}.link`, {
        description,
        date: String(occurrence.nominal_date ?? '?'),
        transaction: String(proposed.description ?? '?'),
        on: String(proposed.date ?? '?'),
      })
    case 'change_recurring_from':
      return t(`${key}.changeFrom`, {
        description, date: String(d.from_date ?? '?'), changes: changesText((data.changes || {}) as Fields, target),
      })
    case 'correct_recurring_series':
      return t(`${key}.correct`, { description, changes: changesText((data.changes || {}) as Fields, target) })
    case 'end_recurring':
      return t(`${key}.end`, { description, date: String(proposed.last_date ?? '?') })
        + (proposed.delete_scheduled ? t(`${key}.endDelete`) : '')
    case 'delete_recurring': {
      if (d.mode === 'permanent') return t(`${key}.purge`, { description })
      const body = (d.apply_body || {}) as Fields
      return t(`${key}.trash`, { description }) + (body.delete_scheduled ? t(`${key}.trashDelete`) : '')
    }
    case 'restore_recurring':
      return t(`${key}.restore`, { description })
  }
}

// The tool names the route it previewed; only the lifecycle routes of one schedule are followed.
const ROUTE = /^(POST|DELETE) \/api(\/recurring-transactions\/[0-9a-f-]{36}\/[a-z0-9/-]+)$/

export async function applyRecurringLifecycle(data: ProposalData): Promise<string> {
  const d = data as Fields
  const match = ROUTE.exec(String(d.apply_endpoint ?? ''))
  if (!match) throw new Error('This proposal has no apply route')
  const [, method, url] = match
  await api.request({ method, url, data: method === 'POST' ? (d.apply_body ?? {}) : undefined })
  return String(((data.target || {}) as Fields).id ?? '')
}
