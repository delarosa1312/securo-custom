import type { RecurringTransaction } from '@/types'

import type { LifecycleFields } from './types'

export type LifecycleRecurring = RecurringTransaction & LifecycleFields

// Fork F-005: the values a change "from a date on" carries to the successor (the backend's CHANGEABLE).
const VALUE_FIELDS = [
  'description', 'amount', 'currency', 'type', 'account_id', 'destination_account_id', 'destination_amount',
  'category_id', 'frequency', 'day_of_month', 'weekend_adjustment', 'auto_generate',
] as const
// Fields that belong to the whole series: never split.
const SERIES_FIELDS = ['start_date', 'end_date', 'is_active'] as const

function same(a: unknown, b: unknown): boolean {
  if (a == null || a === '') return b == null || b === ''
  if (typeof a === 'number' || typeof b === 'number') return Number(a) === Number(b)
  return a === b
}

function linesChanged(original: RecurringTransaction, data: Partial<RecurringTransaction>): boolean {
  const sent = (data as { category_lines?: unknown }).category_lines
  if (sent === undefined) return false
  return JSON.stringify(sent) !== JSON.stringify(original.category_lines ?? null)
}

export interface EditPlan {
  /** Value fields that changed: what "from a date on" would carry. */
  values: Partial<RecurringTransaction>
  /** Series fields that changed (start, end, active). */
  series: Partial<RecurringTransaction>
  /** Ask "Apply the change from when?" before saving. */
  askScope: boolean
  /** The from-date option is offered (the series has a date after its start to split on). */
  canSplit: boolean
}

/** What an edit of an existing schedule changes, and whether the user chooses its scope (Q16). */
export function planEdit(original: LifecycleRecurring, data: Partial<RecurringTransaction>): EditPlan {
  const values: Record<string, unknown> = {}
  for (const key of VALUE_FIELDS) {
    if (key in data && !same(original[key], data[key])) values[key] = data[key]
  }
  if (linesChanged(original, data)) {
    values.category_lines = (data as { category_lines?: unknown }).category_lines
    values.category_id = data.category_id ?? null
  }
  const series: Record<string, unknown> = {}
  for (const key of SERIES_FIELDS) {
    if (key in data && !same(original[key], data[key])) series[key] = data[key]
  }
  const valueChanged = Object.keys(values).some((key) => key !== 'auto_generate')
  // A split needs a date after the start; a new start date belongs to the whole series.
  const canSplit = original.next_occurrence > original.start_date && !('start_date' in series)
  return {
    values: values as Partial<RecurringTransaction>,
    series: series as Partial<RecurringTransaction>,
    askScope: valueChanged && !original.deleted_at,
    canSplit,
  }
}
