import type { TFunction } from 'i18next'
import { accounts } from '@/lib/api'
import type { ProposalData } from '@/lib/agent-proposals'

// Fork F-001: the in-app card for `propose_set_account_available`.

export const SET_ACCOUNT_AVAILABLE_KIND = 'set_account_available'

function includeFlag(value: unknown): boolean {
  return value !== false
}

export function setAccountAvailableSummary(data: ProposalData, t: TFunction): string {
  const target = (data.target || {}) as Record<string, unknown>
  const changes = (data.changes || {}) as Record<string, unknown>
  const name = String(target.name ?? '?')
  return includeFlag(changes.include_in_available)
    ? t('fork.availableBalance.proposal.include', { name })
    : t('fork.availableBalance.proposal.leaveOut', { name })
}

export async function applySetAccountAvailable(data: ProposalData): Promise<string> {
  const id = String((data.target as Record<string, unknown>).id)
  const changes = (data.changes || {}) as Record<string, unknown>
  await accounts.update(id, { include_in_available: includeFlag(changes.include_in_available) })
  return id
}
