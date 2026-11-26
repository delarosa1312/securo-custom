import type { ReportResponse } from '@/types'

// Exact decimals arrive as strings ("110.00").
export interface BudgetForecastCategory {
  category_id: string
  name: string
  color: string | null
  icon: string | null
  group_id: string | null
  group_name: string | null
  budget: string
  posted: string
  scheduled: string
  remaining: string
  forecast: string
}

export interface BudgetForecastMonth {
  month: string
  spread_from: string | null
  spread_to: string | null
  days: number
  budget: string
  posted: string
  scheduled: string
  remaining: string
  forecast: string
  categories: BudgetForecastCategory[]
}

export interface BudgetForecastExplanation {
  placement: 'subtracted' | 'unallocated'
  rule: 'even_daily'
  total: string
  months: BudgetForecastMonth[]
}

export type CashFlowBudgetResponse = ReportResponse & { budget_forecast?: BudgetForecastExplanation }

export const BUDGET_OUTFLOW_KEY = 'budget_outflow'
export const BUDGET_RANGE_KEY = '24m'
export const BUDGET_RANGE_OPTION: { key: string; months: number } = { key: BUDGET_RANGE_KEY, months: 24 }
