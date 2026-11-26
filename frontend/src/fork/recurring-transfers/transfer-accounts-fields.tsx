import { useTranslation } from 'react-i18next'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { getAccountName } from '@/lib/account-utils'
import { formatAmountInput } from '@/lib/format'
import { destinationOptions, needsReceivedAmount, type TransferAccount } from './transfer'

/** Fork F-004: the recurring form's accounts when the schedule is a transfer. */
export function TransferAccountsFields({
  accounts,
  sourceId,
  onSourceChange,
  destinationId,
  onDestinationChange,
  receivedAmount,
  onReceivedAmountChange,
  receivedInvalid,
  displayLocale,
  selectClass,
}: {
  accounts: TransferAccount[]
  sourceId: string
  onSourceChange: (id: string) => void
  destinationId: string
  onDestinationChange: (id: string) => void
  receivedAmount: string
  onReceivedAmountChange: (value: string) => void
  receivedInvalid: boolean
  displayLocale: string
  selectClass: string
}) {
  const { t } = useTranslation()
  const source = accounts.find((account) => account.id === sourceId)
  const destination = accounts.find((account) => account.id === destinationId)
  const options = destinationOptions(accounts, sourceId)
  // A closed account is not in the list; the schedule keeps it, so it can still be deactivated or end-dated.
  const sourceMissing = !!sourceId && !source
  const destinationMissing =
    !!destinationId && destinationId !== sourceId && !options.some((account) => account.id === destinationId)
  return (
    <>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="recurring-transfer-from">{t('fork.recurringTransfers.fromAccount')}</Label>
          <select
            id="recurring-transfer-from"
            className={selectClass}
            value={sourceId}
            onChange={(e) => onSourceChange(e.target.value)}
            required
          >
            {!sourceId && <option value="" disabled hidden>{t('fork.recurringTransfers.selectAccount')}</option>}
            {sourceMissing && <option value={sourceId}>{t('fork.recurringTransfers.unavailableAccount')}</option>}
            {accounts.filter((account) => !account.is_closed || account.id === sourceId).map((account) => (
              <option key={account.id} value={account.id}>{getAccountName(account)}</option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="recurring-transfer-to">{t('fork.recurringTransfers.toAccount')}</Label>
          <select
            id="recurring-transfer-to"
            className={selectClass}
            value={destinationId && destinationId !== sourceId ? destinationId : ''}
            onChange={(e) => onDestinationChange(e.target.value)}
            required
          >
            <option value="" disabled hidden>{t('fork.recurringTransfers.selectAccount')}</option>
            {destinationMissing && (
              <option value={destinationId}>
                {destination ? getAccountName(destination) : t('fork.recurringTransfers.unavailableAccount')}
              </option>
            )}
            {options.map((account) => (
              <option key={account.id} value={account.id}>{getAccountName(account)}</option>
            ))}
          </select>
        </div>
      </div>
      {needsReceivedAmount(source, destination) && destination && (
        <div className="space-y-2">
          <Label htmlFor="recurring-transfer-received">
            {t('fork.recurringTransfers.amountReceived', { currency: destination.currency })}
          </Label>
          <Input
            id="recurring-transfer-received"
            type="text"
            inputMode="decimal"
            value={receivedAmount}
            onChange={(e) => onReceivedAmountChange(e.target.value)}
            aria-invalid={receivedInvalid}
            aria-describedby="recurring-transfer-received-hint"
            required
          />
          <p id="recurring-transfer-received-hint" className="text-xs text-muted-foreground">
            {receivedInvalid
              ? t('common.invalidAmount', { example: formatAmountInput(1234.56, displayLocale, 2, 2) })
              : t('fork.recurringTransfers.amountReceivedHint', { name: getAccountName(destination) })}
          </p>
        </div>
      )}
    </>
  )
}
