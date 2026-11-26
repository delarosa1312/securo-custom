import { describe, expect, it } from 'vitest'
import { hasAvailableSetting, isIncludedInAvailable, isLeftOutOfAvailable } from './available-account'
import forkEn from '@/fork/locales/en.json'
import forkPtBR from '@/fork/locales/pt-BR.json'

describe('available-balance predicates', () => {
  it('applies the setting to checking, savings and wallet only', () => {
    expect(['checking', 'savings', 'wallet'].every(hasAvailableSetting)).toBe(true)
    expect(['credit_card', 'loan', 'investment'].some(hasAvailableSetting)).toBe(false)
  })

  it('counts an account as included unless it is switched off', () => {
    expect(isIncludedInAvailable({ include_in_available: true })).toBe(true)
    expect(isIncludedInAvailable({ include_in_available: undefined })).toBe(true)
    expect(isIncludedInAvailable({ include_in_available: false })).toBe(false)
  })

  it('marks only open liquid accounts that are switched off', () => {
    expect(isLeftOutOfAvailable({ type: 'savings', is_closed: false, include_in_available: false })).toBe(true)
    expect(isLeftOutOfAvailable({ type: 'savings', is_closed: true, include_in_available: false })).toBe(false)
    expect(isLeftOutOfAvailable({ type: 'credit_card', is_closed: false, include_in_available: false })).toBe(false)
    expect(isLeftOutOfAvailable({ type: 'checking', is_closed: false, include_in_available: true })).toBe(false)
  })
})

describe('fork locale bundles', () => {
  const keys = (obj: Record<string, unknown>, prefix = ''): string[] =>
    Object.entries(obj).flatMap(([k, v]) =>
      v !== null && typeof v === 'object' ? keys(v as Record<string, unknown>, `${prefix}${k}.`) : [`${prefix}${k}`])

  it('has the same available-balance keys in English and Brazilian Portuguese', () => {
    expect(keys(forkPtBR.availableBalance).sort()).toEqual(keys(forkEn.availableBalance).sort())
  })
})
