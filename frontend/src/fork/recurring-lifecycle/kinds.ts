// Fork F-005: the proposal kinds of the recurring lifecycle tools. Kept apart from proposal.ts so
// lib/agent-proposals.ts can import them without importing the API client.
export const RECURRING_LIFECYCLE_KINDS = [
  'skip_occurrence',
  'unskip_occurrence',
  'record_occurrence',
  'link_occurrence',
  'change_recurring_from',
  'correct_recurring_series',
  'end_recurring',
  'delete_recurring',
  'restore_recurring',
] as const

export type RecurringLifecycleKind = (typeof RECURRING_LIFECYCLE_KINDS)[number]

export function isRecurringLifecycleKind(kind: unknown): kind is RecurringLifecycleKind {
  return (RECURRING_LIFECYCLE_KINDS as readonly unknown[]).includes(kind)
}
