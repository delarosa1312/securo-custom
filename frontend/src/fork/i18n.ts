import i18n from 'i18next'

import en from '@/fork/locales/en.json'
import ptBR from '@/fork/locales/pt-BR.json'

// lib/i18n.ts puts these files under `fork.*` (D-004). The `reports` labels are the keys the upstream Reports
// page looks up for a summary figure or a series (`reports.<key>`), so they also join that namespace.
let registered = false

export function registerForkReportLabels() {
  if (registered) return
  registered = true
  for (const [lang, bundle] of [['en', en], ['pt-BR', ptBR]] as const) {
    i18n.addResourceBundle(lang, 'translation', { reports: bundle.reports }, true, false)
  }
}

registerForkReportLabels()
