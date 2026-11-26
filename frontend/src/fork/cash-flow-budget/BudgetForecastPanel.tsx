import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import '@/fork/i18n'
import { formatCurrency } from '@/lib/format'
import { monthLabel } from '@/lib/month-utils'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { cn } from '@/lib/utils'

import type { BudgetForecastExplanation, BudgetForecastMonth } from './types'

interface Props {
  explanation: BudgetForecastExplanation
  currency: string
  locale: string
  dateLocale: string
}

const COLUMNS = ['budget', 'posted', 'scheduled', 'remaining', 'forecast'] as const

/** Where the budget-derived spending of the Cash Flow forecast comes from, month by month. */
export function BudgetForecastPanel({ explanation, currency, locale, dateLocale }: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<Set<string>>(() => new Set(explanation.months.slice(0, 1).map((m) => m.month)))
  const months = explanation.months.filter((m) => m.categories.length > 0)
  const toggle = (month: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(month)) next.delete(month)
      else next.add(month)
      return next
    })

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm mb-5" data-testid="cash-flow-budget-panel">
      <div className="px-5 pt-5 pb-3">
        <p className="text-sm font-semibold text-foreground">{t('fork.budgetForecast.panelTitle')}</p>
        <p className="text-xs text-muted-foreground mt-1">
          {t(explanation.placement === 'subtracted'
            ? 'fork.budgetForecast.introSubtracted'
            : 'fork.budgetForecast.introUnallocated')}
        </p>
      </div>
      {months.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted-foreground">{t('fork.budgetForecast.empty')}</p>
      ) : (
        <div className="px-2 pb-3">
          {months.map((month) => (
            <MonthBlock
              key={month.month}
              month={month}
              expanded={open.has(month.month)}
              onToggle={() => toggle(month.month)}
              currency={currency}
              locale={locale}
              dateLocale={dateLocale}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function MonthBlock({
  month, expanded, onToggle, currency, locale, dateLocale,
}: {
  month: BudgetForecastMonth
  expanded: boolean
  onToggle: () => void
  currency: string
  locale: string
  dateLocale: string
}) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const money = (value: string) => mask(formatCurrency(Number(value), currency, locale))
  const day = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(dateLocale, { day: 'numeric', month: 'short' })
  const rule = month.days > 0 && month.spread_from && month.spread_to
    ? t('fork.budgetForecast.spread', { from: day(month.spread_from), to: day(month.spread_to) })
    : t('fork.budgetForecast.noDaysLeft')

  return (
    <div className="border-t border-border first:border-t-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-start hover:bg-muted/40 rounded-lg"
      >
        <ChevronRight className={cn('h-4 w-4 shrink-0 transition-transform', expanded && 'rotate-90 rtl:-rotate-90')} />
        <span className="text-sm font-medium capitalize">{monthLabel(month.month, dateLocale)}</span>
        <span className="ms-auto text-sm font-semibold tabular-nums">{money(month.forecast)}</span>
      </button>
      {expanded && (
        <div className="px-3 pb-3">
          <p className="text-xs text-muted-foreground mb-2">{rule}</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs tabular-nums">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="py-1 pe-3 text-start font-medium">{t('fork.budgetForecast.category')}</th>
                  {COLUMNS.map((column) => (
                    <th key={column} className="py-1 ps-3 text-end font-medium whitespace-nowrap">
                      {t(`fork.budgetForecast.${column}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {month.categories.map((category) => (
                  <tr key={category.category_id} className="border-t border-border/60">
                    <td className="py-1.5 pe-3">
                      <span className="flex items-center gap-2 min-w-0">
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ backgroundColor: category.color ?? 'var(--muted-foreground)' }}
                        />
                        <span className="truncate">{category.name}</span>
                        {category.group_name && (
                          <span className="truncate text-muted-foreground">{category.group_name}</span>
                        )}
                      </span>
                    </td>
                    {COLUMNS.map((column) => (
                      <td key={column} className="py-1.5 ps-3 text-end whitespace-nowrap">{money(category[column])}</td>
                    ))}
                  </tr>
                ))}
                <tr className="border-t border-border font-semibold">
                  <td className="py-1.5 pe-3">{t('fork.budgetForecast.total')}</td>
                  {COLUMNS.map((column) => (
                    <td key={column} className="py-1.5 ps-3 text-end whitespace-nowrap">{money(month[column])}</td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
