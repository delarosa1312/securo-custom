import i18n from 'i18next'

import en from '@/fork/locales/en.json'
import ptBR from '@/fork/locales/pt-BR.json'

// Fork strings join the app's `translation` namespace (D-004). Other languages
// fall back to English. Deep merge, so `reports.*` keys sit next to upstream's.
let registered = false

export function registerForkStrings() {
  if (registered) return
  registered = true
  i18n.addResourceBundle('en', 'translation', en, true, false)
  i18n.addResourceBundle('pt-BR', 'translation', ptBR, true, false)
}

registerForkStrings()
