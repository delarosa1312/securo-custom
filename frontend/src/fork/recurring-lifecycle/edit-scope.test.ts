// Fork F-005: which edits ask "Apply the change from when?" (Q16). Synthetic data.
import { describe, expect, it } from 'vitest'

import { planEdit, type LifecycleRecurring } from './edit-scope'

const rent = {
  id: 'rec-rent', description: 'Rent', amount: 1500, currency: 'BRL', type: 'debit', frequency: 'monthly',
  weekend_adjustment: 'none', day_of_month: 10, start_date: '2026-01-10', end_date: null, is_active: true,
  auto_generate: true, next_occurrence: '2026-12-10', account_id: 'acc-1', category_id: null,
  destination_account_id: null, destination_amount: null, deleted_at: null,
} as unknown as LifecycleRecurring

describe('planEdit', () => {
  it('asks the scope for a new amount and offers the from-date option', () => {
    const plan = planEdit(rent, { ...rent, amount: 1600 })
    expect(plan).toMatchObject({ askScope: true, canSplit: true, values: { amount: 1600 }, series: {} })
  })

  it('saves a pause, an end date or the auto-generate flag directly', () => {
    expect(planEdit(rent, { ...rent, is_active: false }).askScope).toBe(false)
    expect(planEdit(rent, { ...rent, end_date: '2027-06-30' }).askScope).toBe(false)
    expect(planEdit(rent, { ...rent, auto_generate: false }).askScope).toBe(false)
  })

  it('keeps series fields apart from the values a split carries', () => {
    const plan = planEdit(rent, { ...rent, amount: 1600, end_date: '2027-06-30' })
    expect(plan.values).toEqual({ amount: 1600 })
    expect(plan.series).toEqual({ end_date: '2027-06-30' })
  })

  it('offers only the whole series when the start moves or nothing is left to split on', () => {
    expect(planEdit(rent, { ...rent, amount: 1600, start_date: '2026-02-10' }).canSplit).toBe(false)
    const fresh = { ...rent, next_occurrence: rent.start_date } as LifecycleRecurring
    expect(planEdit(fresh, { ...fresh, amount: 1600 }).canSplit).toBe(false)
  })

  it('treats equal numbers and empty values as unchanged', () => {
    expect(planEdit(rent, { ...rent, amount: '1500.00' as unknown as number, category_id: '' }).askScope).toBe(false)
  })

  it('never asks for a deleted schedule', () => {
    const deleted = { ...rent, deleted_at: '2026-11-01T10:00:00Z' } as LifecycleRecurring
    expect(planEdit(deleted, { ...deleted, amount: 1600 }).askScope).toBe(false)
  })
})
