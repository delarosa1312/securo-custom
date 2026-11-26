import api from '@/lib/api'

import type { CashFlowBudgetResponse } from './types'

/** The Cash Flow report with remaining budgets counted as spending (F-002). */
export async function cashFlowWithBudgets(
  months: number,
  interval: string,
  accountIds?: string[],
): Promise<CashFlowBudgetResponse> {
  const filtered = !!accountIds && accountIds.length > 0
  const { data } = await api.get('/reports/cash-flow', {
    params: { months, interval, budget_forecast: true, ...(filtered ? { account_ids: accountIds } : {}) },
    ...(filtered ? { paramsSerializer: { indexes: null as null } } : {}),
  })
  return data
}
