import type { RecurringTransaction } from '@/types'

/** Fork F-005: the recurring lifecycle routes' shapes. Amounts arrive as decimal strings. */
export type OccurrenceState = 'recorded' | 'edited' | 'skipped' | 'removed' | 'scheduled' | 'overdue' | 'past'

export interface Occurrence {
  recurring_id: string
  nominal_date: string
  date: string
  amount: string
  destination_amount: string | null
  state: OccurrenceState
  transaction_id: string | null
}

export interface OverdueOccurrence {
  recurring_id: string
  description: string
  account_id: string | null
  destination_account_id: string | null
  type: 'debit' | 'credit'
  amount: string
  currency: string
  nominal_date: string
  date: string
  auto_generate: boolean
}

export interface RecordedOccurrence {
  recurring_id: string
  nominal_date: string
  transaction_id: string
  date: string
  amount: string
  status: string
  state: string
}

export interface LinkCandidate {
  id: string
  date: string
  description: string
  amount: string
  currency: string
  status: string
}

export interface RowImpact {
  transaction_id: string
  date: string
  amount: string
  status: string
  transfer_pair_id: string | null
}

/** What an action does to the transactions the series already wrote. */
export interface Impact {
  history: number
  follow: RowImpact[]
  removed: RowImpact[]
  edited: RowImpact[]
  skipped_kept: string[]
  skipped_dropped: string[]
}

export interface ChangeResult {
  schedule: RecurringTransaction | null
  impact: Impact
  preview: boolean
}

export type EndResult = ChangeResult

export interface TrashResult {
  deleted: string[]
  impact: Impact
  preview: boolean
}

export interface RestoreResult {
  restored: string[]
  skipped: string[]
  next_occurrence: string | null
  preview: boolean
}

export interface PurgeResult {
  deleted: string[]
  kept_transactions: number
  preview: boolean
}

export type RecurringStatus = 'active' | 'inactive' | 'deleted' | 'all'

/** Fork fields on a recurring row (F-005). */
export interface LifecycleFields {
  predecessor_id?: string | null
  deleted_at?: string | null
}
