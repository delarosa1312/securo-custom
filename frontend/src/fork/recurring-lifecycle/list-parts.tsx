import { useTranslation } from 'react-i18next'
import { MoreHorizontal } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatCurrency } from '@/lib/format'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'

import type { LifecycleRecurring } from './edit-scope'
import { showDate } from './format'
import type { StatusTab } from './tabs'

export function StatusTabs({
  tab,
  counts,
  onChange,
}: {
  tab: StatusTab
  counts: Record<StatusTab, number>
  onChange: (tab: StatusTab) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="px-4 sm:px-5 pt-3">
      <Tabs value={tab} onValueChange={(value) => onChange(value as StatusTab)}>
        <TabsList aria-label={t('recurring.status')}>
          {(['active', 'inactive', 'deleted'] as const).map((key) => (
            <TabsTrigger key={key} value={key} data-testid={`recurring-tab-${key}`}>
              {t(`fork.recurringLifecycle.tabs.${key}`, { count: counts[key] })}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </div>
  )
}

/** "since Mar 10: 1,600.00 (was 1,500.00)" under a schedule that continues an earlier part. */
export function SeriesSince({ rt, all }: { rt: LifecycleRecurring; all: LifecycleRecurring[] }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const previous = rt.predecessor_id ? all.find((other) => other.id === rt.predecessor_id) : undefined
  if (!previous || Number(previous.amount) === Number(rt.amount)) return null
  return (
    <span className="block text-xs font-normal text-muted-foreground" data-testid="series-since">
      {t('fork.recurringLifecycle.since', {
        date: showDate(rt.start_date, dateLocale),
        amount: mask(formatCurrency(Number(rt.amount), rt.currency, locale)),
        previous: mask(formatCurrency(Number(previous.amount), previous.currency, locale)),
      })}
    </span>
  )
}

export type RowAction =
  | 'edit' | 'occurrences' | 'priceHistory' | 'pause' | 'resume' | 'end' | 'delete' | 'restore' | 'purge'

/** The row menu (Q14): every action of one schedule. Read-only workspaces only read. */
export function RowMenu({
  rt,
  canWrite,
  onAction,
}: {
  rt: LifecycleRecurring
  canWrite: boolean
  onAction: (action: RowAction) => void
}) {
  const { t } = useTranslation()
  const deleted = Boolean(rt.deleted_at)
  const item = (action: RowAction, destructive = false) => (
    <DropdownMenuItem
      key={action}
      onSelect={() => onAction(action)}
      variant={destructive ? 'destructive' : 'default'}
      data-testid={`recurring-menu-${action}`}
    >
      {t(`fork.recurringLifecycle.menu.${action}`)}
    </DropdownMenuItem>
  )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
          aria-label={t('fork.recurringLifecycle.menu.open', { description: rt.description })}
          title={t('fork.recurringLifecycle.menu.open', { description: rt.description })}
          data-testid="recurring-row-menu"
        >
          <MoreHorizontal size={14} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {canWrite && !deleted && item('edit')}
        {item('occurrences')}
        {item('priceHistory')}
        {canWrite && !deleted && (
          <>
            <DropdownMenuSeparator />
            {item(rt.is_active ? 'pause' : 'resume')}
            {item('end')}
            {item('delete', true)}
          </>
        )}
        {canWrite && deleted && (
          <>
            <DropdownMenuSeparator />
            {item('restore')}
            {item('purge', true)}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
