// Fork F-004: the proposal kinds of the recurring transfer tools. Kept apart from proposal.ts so
// lib/agent-proposals.ts can import them without importing the API client.
export const RECURRING_TRANSFER_KINDS = [
  'create_recurring_transfer',
  'update_recurring_transfer',
  'convert_transfer_series',
] as const

export type RecurringTransferKind = (typeof RECURRING_TRANSFER_KINDS)[number]

export function isRecurringTransferKind(kind: unknown): kind is RecurringTransferKind {
  return (RECURRING_TRANSFER_KINDS as readonly unknown[]).includes(kind)
}
