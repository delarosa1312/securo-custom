import { describe, expect, it } from 'vitest'

import { cashFlowBar } from '@/lib/cash-flow-bars'

import { budgetCashFlowBar, cashFlowTooltip } from './bars'

describe('budgetCashFlowBar', () => {
  it('ends the column at inflow minus scheduled outflow minus budgeted spending', () => {
    const bar = budgetCashFlowBar(500, 100, 50)
    expect(bar.netFlow).toBe(350)
    expect(bar.outflow).toEqual([-300, -200])
    expect(bar.budget_outflow).toEqual([-200, -150])
    expect(bar.inflow).toEqual([-150, 350])
    expect(bar.topSeries).toBe('inflow')
  })

  it('puts the rounded top on the budget segment when nothing comes in', () => {
    expect(budgetCashFlowBar(0, 40, 11).topSeries).toBe('budget_outflow')
    expect(budgetCashFlowBar(0, 40, 0).topSeries).toBe('outflow')
    expect(budgetCashFlowBar(0, 0, 0).topSeries).toBeNull()
  })

  it('draws exactly the upstream column when there is no budgeted spending', () => {
    const upstream = cashFlowBar(120, 80)
    const bar = budgetCashFlowBar(120, 80, 0)
    expect(bar.outflow).toEqual(upstream.outflow)
    expect(bar.inflow).toEqual(upstream.inflow)
    expect(bar.netFlow).toBe(upstream.netFlow)
  })

  it('treats missing values as zero', () => {
    expect(budgetCashFlowBar(undefined, null, Number.NaN).netFlow).toBe(0)
  })
})

describe('cashFlowTooltip', () => {
  const point = { inflowAmount: 200, outflowAmount: 30, budgetAmount: 11 }

  it('lists the budget row and nets it when the mode is on', () => {
    const { bar, rows } = cashFlowTooltip(point, true)
    expect(rows.map((r) => [r.key, r.value])).toEqual([['inflow', 200], ['outflow', -30], ['budget_outflow', -11]])
    expect(bar.netFlow).toBe(159)
  })

  it('is the upstream tooltip when the mode is off', () => {
    const { bar, rows } = cashFlowTooltip(point, false)
    expect(rows.map((r) => r.key)).toEqual(['inflow', 'outflow'])
    expect(bar.netFlow).toBe(170)
  })
})
