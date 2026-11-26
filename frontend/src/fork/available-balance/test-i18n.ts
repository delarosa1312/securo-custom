// Until the fork bundle merge in lib/i18n.ts (issue #1) is on this branch, tests load it here.
// After that merge this is a harmless re-add of the same keys.
import { i18n } from '@/test/utils'
import forkEn from '@/fork/locales/en.json'
import forkPtBR from '@/fork/locales/pt-BR.json'

i18n.addResourceBundle('en', 'translation', { fork: forkEn }, true, true)
i18n.addResourceBundle('pt-BR', 'translation', { fork: forkPtBR }, true, true)
