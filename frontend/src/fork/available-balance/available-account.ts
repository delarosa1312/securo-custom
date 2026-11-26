import type { Account } from '@/types'

// Fork F-001. The account types the "Include in available balance" setting applies to.
export const AVAILABLE_ACCOUNT_TYPES: readonly string[] = ['checking', 'savings', 'wallet']

export function hasAvailableSetting(type: string): boolean {
  return AVAILABLE_ACCOUNT_TYPES.includes(type)
}

/** False only for an account someone switched out of available money. */
export function isIncludedInAvailable(account: Pick<Account, 'include_in_available'>): boolean {
  return account.include_in_available !== false
}

/** A checking, savings or wallet account that is open and switched off. */
export function isLeftOutOfAvailable(account: Pick<Account, 'type' | 'is_closed' | 'include_in_available'>): boolean {
  return hasAvailableSetting(account.type) && !account.is_closed && !isIncludedInAvailable(account)
}
