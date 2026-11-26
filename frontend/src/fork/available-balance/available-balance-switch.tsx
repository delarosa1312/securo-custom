import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

/** Fork F-001: the account dialog's "Include in available balance" setting. */
export function AvailableBalanceSwitch({
  checked,
  onCheckedChange,
}: {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  const { t } = useTranslation()
  const labelId = useId()
  const hintId = useId()
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-border p-3">
      <div className="space-y-1">
        <Label id={labelId}>{t('fork.availableBalance.setting')}</Label>
        <p id={hintId} className="text-xs text-muted-foreground">{t('fork.availableBalance.settingHint')}</p>
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onCheckedChange}
        aria-labelledby={labelId}
        className="mt-0.5"
      />
    </div>
  )
}
