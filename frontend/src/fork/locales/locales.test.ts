import { describe, expect, it } from 'vitest'

import en from './en.json'
import ptBR from './pt-BR.json'

function leaves(value: unknown, prefix = ''): Record<string, string> {
  if (typeof value === 'string') return { [prefix]: value }
  return Object.entries(value as Record<string, unknown>).reduce(
    (acc, [key, child]) => ({ ...acc, ...leaves(child, prefix ? `${prefix}.${key}` : key) }),
    {} as Record<string, string>,
  )
}

const placeholders = (text: string) => [...text.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort()

describe('fork locale bundles', () => {
  const english = leaves(en)
  const portuguese = leaves(ptBR)

  it('have the same keys in English and pt-BR', () => {
    expect(Object.keys(portuguese).sort()).toEqual(Object.keys(english).sort())
  })

  it('keep the same placeholders', () => {
    for (const key of Object.keys(english)) {
      expect(placeholders(portuguese[key]), key).toEqual(placeholders(english[key]))
    }
  })

  it('never use an em dash or en dash', () => {
    for (const text of [...Object.values(english), ...Object.values(portuguese)]) {
      expect(text).not.toMatch(/[–—]/)
    }
  })
})
