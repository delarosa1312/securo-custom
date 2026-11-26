// Fork F-004: a recurring transfer is a schedule with a destination account. Its amount leaves the source
// (account_id) and arrives in the destination; destination_amount is set only between currencies.

export const TRANSFER_TYPE = 'transfer'

export type ScheduleKind = 'debit' | 'credit' | typeof TRANSFER_TYPE

export interface TransferAccount {
  id: string
  name: string
  display_name?: string | null
  currency?: string
  is_closed?: boolean
}

export interface TransferFields {
  destination_account_id?: string | null
  destination_amount?: number | string | null
}

export function isTransferSchedule(schedule: TransferFields | null | undefined): boolean {
  return !!schedule?.destination_account_id
}

/** Open accounts other than the source: a transfer never goes to itself or to a closed account. */
export function destinationOptions<T extends TransferAccount>(accounts: T[], sourceId: string): T[] {
  return accounts.filter((account) => account.id !== sourceId && !account.is_closed)
}

/** Between currencies the amount received each time is fixed by the user (the only rate policy). */
export function needsReceivedAmount(source?: TransferAccount, destination?: TransferAccount): boolean {
  return !!source?.currency && !!destination?.currency && source.currency !== destination.currency
}

/** The fields Save sends for the schedule's kind; a schedule that stops being a transfer drops them. */
export function transferPayload(
  kind: ScheduleKind,
  wasTransfer: boolean,
  destinationId: string,
  receivedAmount: number | null,
): TransferFields & { type?: 'debit' } {
  if (kind === TRANSFER_TYPE) {
    return { type: 'debit', destination_account_id: destinationId, destination_amount: receivedAmount }
  }
  return wasTransfer ? { destination_account_id: null } : {}
}

/**
 * A projected row that is one leg of a recurring transfer. The server marks legs as transfers and gives them
 * no category; an upstream row marked as a transfer always has its transfer-like category.
 */
export function isProjectedTransferLeg(row: {
  category_treat_as_transfer?: boolean
  category_id?: string | null
  has_category_lines?: boolean
}): boolean {
  return !!row.category_treat_as_transfer && row.category_id == null && !row.has_category_lines
}
