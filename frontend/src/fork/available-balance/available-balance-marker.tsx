import { useTranslation } from 'react-i18next'
import type { Account } from '@/types'
import { isLeftOutOfAvailable } from './available-account'

/** Fork F-001: " · Not in available balance" in an account row's subtitle. */
export function AvailableBalanceMarker({ account }: { account: Pick<Account, 'type' | 'is_closed' | 'include_in_available'> }) {
  const { t } = useTranslation()
  if (!isLeftOutOfAvailable(account)) return null
  return <> · <span>{t('fork.availableBalance.leftOut')}</span></>
}
