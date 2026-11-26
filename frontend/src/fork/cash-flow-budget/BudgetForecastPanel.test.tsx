import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'

import { renderWithProviders, t } from '@/test/utils'

import { BudgetForecastPanel } from './BudgetForecastPanel'
import type { BudgetForecastExplanation } from './types'

const category = {
  category_id: 'c1', name: 'Restaurants', color: '#F59E0B', icon: null, group_id: 'g1', group_name: 'Food',
  budget: '250.00', posted: '100.00', scheduled: '40.00', remaining: '110.00', forecast: '110.00',
}

function explanation(overrides: Partial<BudgetForecastExplanation> = {}): BudgetForecastExplanation {
  return {
    placement: 'subtracted',
    rule: 'even_daily',
    total: '360.00',
    months: [
      {
        month: '2026-11', spread_from: '2026-11-21', spread_to: '2026-11-30', days: 10,
        budget: '250.00', posted: '100.00', scheduled: '40.00', remaining: '110.00', forecast: '110.00',
        categories: [category],
      },
      {
        month: '2026-12', spread_from: '2026-12-01', spread_to: '2026-12-31', days: 31,
        budget: '250.00', posted: '0.00', scheduled: '0.00', remaining: '250.00', forecast: '250.00',
        categories: [{ ...category, posted: '0.00', scheduled: '0.00', remaining: '250.00', forecast: '250.00' }],
      },
    ],
    ...overrides,
  }
}

function renderPanel(data = explanation()) {
  return renderWithProviders(
    <BudgetForecastPanel explanation={data} currency="USD" locale="en-US" dateLocale="en-US" />,
  )
}

describe('BudgetForecastPanel', () => {
  it('opens the current month with every figure and the spreading rule', () => {
    renderPanel()
    const panel = screen.getByTestId('cash-flow-budget-panel')
    expect(panel).toHaveTextContent(t('fork.budgetForecast.panelTitle'))
    expect(panel).toHaveTextContent(t('fork.budgetForecast.introSubtracted'))
    expect(panel).toHaveTextContent(t('fork.budgetForecast.spread', { from: 'Nov 21', to: 'Nov 30' }))
    const row = screen.getByText('Restaurants').closest('tr') as HTMLElement
    expect(within(row).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'RestaurantsFood', '$250.00', '$100.00', '$40.00', '$110.00', '$110.00',
    ])
    // December stays closed until asked for.
    expect(screen.getAllByRole('table')).toHaveLength(1)
  })

  it('opens another month on click', async () => {
    const { user } = renderPanel()
    await user.click(screen.getByRole('button', { name: /december 2026/i }))
    expect(screen.getAllByRole('table')).toHaveLength(2)
  })

  it('explains why a filtered view does not subtract it', () => {
    renderPanel(explanation({ placement: 'unallocated' }))
    expect(screen.getByTestId('cash-flow-budget-panel')).toHaveTextContent(t('fork.budgetForecast.introUnallocated'))
  })

  it('says when the month has no days left', () => {
    const data = explanation()
    data.months[0] = { ...data.months[0], days: 0, spread_from: null, spread_to: null, forecast: '0.00' }
    renderPanel(data)
    expect(screen.getByTestId('cash-flow-budget-panel')).toHaveTextContent(t('fork.budgetForecast.noDaysLeft'))
  })

  it('points to the Budgets page when there are no budgets', () => {
    renderPanel(explanation({ total: '0.00', months: [{ ...explanation().months[0], categories: [] }] }))
    expect(screen.getByTestId('cash-flow-budget-panel')).toHaveTextContent(t('fork.budgetForecast.empty'))
  })
})
