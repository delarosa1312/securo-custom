import type { TFunction } from 'i18next'
import { recurring } from '@/lib/api'
import type { ProposalData } from '@/lib/agent-proposals'
import { applyTransferConversion } from './api'
import type { RecurringTransferKind } from './kinds'

// Fork F-004: the in-app cards for the recurring transfer proposal tools.

export { isRecurringTransferKind } from './kinds'

type Fmt = (amount: unknown, currency: unknown) => string
type Fields = Record<string, unknown>

export function recurringTransferTitle(kind: RecurringTransferKind, t: TFunction): string {
  return t(`fork.recurringTransfers.proposal.${kind}`)
}

function frequency(value: unknown, t: TFunction): string {
  return value ? t(`recurring.${String(value)}`) : '?'
}

function account(value: unknown): string {
  if (value && typeof value === 'object') {
    return String((value as Fields).name ?? '?')
  }
  return value ? String(value) : '?'
}

export function recurringTransferSummary(
  kind: RecurringTransferKind,
  data: ProposalData,
  t: TFunction,
  fmt: Fmt,
): string {
  const proposed = (data.proposed || {}) as Fields
  const target = (data.target || {}) as Fields
  if (kind === 'create_recurring_transfer') {
    const received = proposed.destination_amount != null
      ? t('fork.recurringTransfers.proposal.received', {
          amount: fmt(proposed.destination_amount, proposed.destination_currency),
        })
      : ''
    return t('fork.recurringTransfers.proposal.create', {
      description: String(proposed.description ?? '?'),
      amount: fmt(proposed.amount, proposed.currency),
      from: account(proposed.from_account_name),
      to: account(proposed.to_account_name),
      frequency: frequency(proposed.frequency, t),
    }) + received
  }
  if (kind === 'update_recurring_transfer') {
    const changes = (data.changes || {}) as Fields
    const lines = Object.entries(changes)
      .filter(([key]) => !key.endsWith('_account_id'))
      .map(([key, value]) => `${key}: ${String(target[key] ?? '∅')} → ${String(value ?? '∅')}`)
    return t('fork.recurringTransfers.proposal.update', {
      description: String(target.description ?? '?'),
      changes: lines.join(', ') || '?',
    })
  }
  const ending = proposed.open_ended
    ? t('fork.recurringTransfers.proposal.convertOpen')
    : t('fork.recurringTransfers.proposal.convertEnds', { date: String(proposed.end_date ?? '?') })
  return t('fork.recurringTransfers.proposal.convert', {
    count: Number(target.count ?? 0),
    amount: fmt(target.amount, (target.source_account as Fields | undefined)?.currency),
    from: account(target.source_account),
    to: account(target.destination_account),
    description: String(target.description ?? '?'),
    frequency: frequency(target.frequency, t),
  }) + ending
}

const UPDATE_FIELD: Record<string, string> = {
  from_account_id: 'account_id',
  to_account_id: 'destination_account_id',
}

export async function applyRecurringTransfer(kind: RecurringTransferKind, data: ProposalData): Promise<string> {
  const proposed = (data.proposed || {}) as Fields
  const target = (data.target || {}) as Fields
  if (kind === 'create_recurring_transfer') {
    const created = await recurring.create({
      description: String(proposed.description),
      amount: Number(proposed.amount),
      currency: proposed.currency as string,
      type: 'debit',
      frequency: proposed.frequency as never,
      weekend_adjustment: (proposed.weekend_adjustment as never) || 'none',
      day_of_month: (proposed.day_of_month as number | null) ?? null,
      start_date: proposed.start_date as string,
      end_date: (proposed.end_date as string | null) ?? null,
      account_id: proposed.from_account_id as string,
      destination_account_id: proposed.to_account_id as string,
      destination_amount: (proposed.destination_amount as number | null) ?? null,
    })
    return created.id
  }
  if (kind === 'update_recurring_transfer') {
    const id = String(target.id)
    const changes: Fields = {}
    for (const [key, value] of Object.entries((data.changes || {}) as Fields)) {
      if (key.endsWith('_account_name')) continue
      changes[UPDATE_FIELD[key] ?? key] = value
    }
    await recurring.update(id, changes)
    return id
  }
  const result = await applyTransferConversion(
    String(target.series_id),
    proposed.open_ended ? { open_ended: true } : { end_date: String(proposed.end_date) },
  )
  return result.schedule.id
}
