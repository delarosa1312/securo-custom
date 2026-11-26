import { describe, expect, it } from 'vitest'

import i18n from '@/lib/i18n'
import en from './en.json'
import ptBR from './pt-BR.json'

function flatten(obj: Record<string, unknown>, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out.set(key, v)
    else if (v !== null && typeof v === 'object' && !Array.isArray(v))
      for (const [inner, value] of flatten(v as Record<string, unknown>, key)) out.set(inner, value)
  }
  return out
}

const placeholders = (value: string) => [...(value.match(/\{\{(\w+)\}\}/g) ?? [])].sort()

describe('fork locale bundles (D-004)', () => {
  const enKeys = flatten(en)
  const ptKeys = flatten(ptBR)

  it('have identical keys in en and pt-BR', () => {
    expect([...ptKeys.keys()].sort()).toEqual([...enKeys.keys()].sort())
  })

  it('have the same placeholders and no empty strings', () => {
    for (const [key, value] of enKeys) {
      expect(value.trim(), key).not.toBe('')
      expect(ptKeys.get(key)?.trim(), key).not.toBe('')
      expect(placeholders(ptKeys.get(key) ?? ''), key).toEqual(placeholders(value))
    }
  })

  it('are merged into the translation namespace under fork', () => {
    const [firstKey, firstValue] = [...enKeys][0]
    expect(i18n.getResource('en', 'translation', `fork.${firstKey}`)).toBe(firstValue)
    expect(i18n.getResource('pt-BR', 'translation', `fork.${firstKey}`)).toBe(ptKeys.get(firstKey))
    // Upstream keys are still there next to ours.
    expect(Object.keys(i18n.getResourceBundle('en', 'translation')).length).toBeGreaterThan(1)
  })
})
