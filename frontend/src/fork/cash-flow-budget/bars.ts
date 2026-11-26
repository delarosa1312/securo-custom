import { cashFlowBar, cashFlowTopSeries, type CashFlowBar } from '@/lib/cash-flow-bars'

export type BudgetCashFlowBar = CashFlowBar & {
  budgetAmount: number
  budget_outflow: [number, number]
  topSeries: 'inflow' | 'outflow' | 'budget_outflow' | null
}

const amount = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : 0)

/**
 * The upstream column (outflow below inflow, ending at net flow) with the
 * budget-derived spending as the upper part of the outflow, so the column
 * still ends at inflow - scheduled outflow - budgeted spending.
 */
export function budgetCashFlowBar(inflow: unknown, outflow: unknown, budget: unknown): BudgetCashFlowBar {
  const inflowAmount = amount(inflow)
  const outflowAmount = amount(outflow)
  const budgetAmount = amount(budget)
  const spent = outflowAmount + budgetAmount
  const netFlow = inflowAmount - spent
  const topSeries = inflowAmount !== 0
    ? 'inflow'
    : budgetAmount !== 0 ? 'budget_outflow' : cashFlowTopSeries({ inflowAmount, outflowAmount })
  return {
    inflowAmount,
    outflowAmount,
    budgetAmount,
    netFlow,
    outflow: [-2 * spent, -2 * spent + outflowAmount],
    budget_outflow: [-2 * spent + outflowAmount, -spent],
    inflow: [-spent, netFlow],
    topSeries,
  }
}

/** Tooltip rows and net flow for one period; the budget row only when the mode is on. */
export function cashFlowTooltip(point: Record<string, unknown>, withBudget: boolean) {
  const bar = withBudget
    ? budgetCashFlowBar(point.inflowAmount, point.outflowAmount, point.budgetAmount)
    : cashFlowBar(point.inflowAmount, point.outflowAmount)
  const rows = [
    { key: 'inflow', name: 'inflow', color: 'var(--chart-income)', value: bar.inflowAmount },
    { key: 'outflow', name: 'outflow', color: 'var(--chart-expense)', value: -bar.outflowAmount },
  ]
  if (withBudget) {
    rows.push({ key: 'budget_outflow', name: 'budget_outflow', color: BUDGET_COLOR, value: -amount(point.budgetAmount) })
  }
  return { bar, rows }
}

export const BUDGET_COLOR = '#F59E0B'
