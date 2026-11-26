import { ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getAccountName } from '@/lib/account-utils'
import type { TransferAccount, TransferFields } from './transfer'

/** Fork F-004: "Checking → Savings" under a recurring transfer's description. */
export function TransferRoute({
  schedule,
  accounts,
}: {
  schedule: TransferFields & { account_id: string | null }
  accounts: TransferAccount[]
}) {
  const { t } = useTranslation()
  const name = (id: string | null | undefined) => {
    const account = accounts.find((candidate) => candidate.id === id)
    return account ? getAccountName(account) : '?'
  }
  return (
    <span
      className="flex items-center gap-1 text-xs font-normal text-muted-foreground"
      aria-label={t('fork.recurringTransfers.route', {
        from: name(schedule.account_id),
        to: name(schedule.destination_account_id),
      })}
    >
      <span>{name(schedule.account_id)}</span>
      <ArrowRight size={11} aria-hidden />
      <span>{name(schedule.destination_account_id)}</span>
    </span>
  )
}
