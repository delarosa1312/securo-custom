import { HelpCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import '@/fork/i18n'

/** The "Include budgets" pill, styled like the Cash Flow tab's "Include estimate". */
export function BudgetForecastToggle({ active, onToggle }: { active: boolean; onToggle: () => void }) {
  const { t } = useTranslation()
  return (
    <div
      data-testid="cash-flow-budget-toggle"
      className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${
        active ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border bg-card text-muted-foreground'
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex items-center gap-2 hover:text-foreground transition-colors"
        aria-pressed={active}
      >
        <span
          className={`relative inline-flex h-3.5 w-6 shrink-0 items-center rounded-full transition-colors ${
            active ? 'bg-primary' : 'bg-muted'
          }`}
        >
          <span
            className={`inline-block h-2.5 w-2.5 transform rounded-full bg-white transition-transform ${
              active ? 'translate-x-3 rtl:-translate-x-3' : 'translate-x-0.5 rtl:-translate-x-0.5'
            }`}
          />
        </span>
        {t('fork.budgetForecast.toggle')}
      </button>
      <span
        title={t('fork.budgetForecast.toggleHelp')}
        aria-label={t('fork.budgetForecast.toggleHelp')}
        className="inline-flex cursor-help"
      >
        <HelpCircle className="h-3.5 w-3.5 opacity-60" />
      </span>
    </div>
  )
}
