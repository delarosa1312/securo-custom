import i18n from 'i18next'

import en from '@/fork/locales/en.json'
import ptBR from '@/fork/locales/pt-BR.json'

// Fork strings live under `fork.*` (D-004). The `reports.*` labels are the keys the
// upstream Reports page looks up for a summary figure or a series, so they also
// join that namespace. Deep merge; other languages fall back to English.
let registered = false

export function registerForkStrings() {
  if (registered) return
  registered = true
  for (const [lang, bundle] of [['en', en], ['pt-BR', ptBR]] as const) {
    i18n.addResourceBundle(lang, 'translation', { fork: bundle, reports: bundle.reports }, true, false)
  }
}

registerForkStrings()
