import api from '@/lib/api'
import type { RecurringTransaction } from '@/types'

import type {
  ChangeResult,
  EndResult,
  LinkCandidate,
  Occurrence,
  OverdueOccurrence,
  PurgeResult,
  RecordedOccurrence,
  RecurringStatus,
  RestoreResult,
  TrashResult,
} from './types'

const base = '/recurring-transactions'

/** Fork F-005: one occurrence at a time and the whole series' lifecycle. Every write has a preview. */
export const recurringLifecycle = {
  list: async (status: RecurringStatus): Promise<RecurringTransaction[]> => {
    const { data } = await api.get(base, { params: { status } })
    return data
  },
  occurrences: async (id: string, from?: string, to?: string): Promise<Occurrence[]> => {
    const { data } = await api.get(`${base}/${id}/occurrences`, { params: { from, to } })
    return data
  },
  overdue: async (): Promise<OverdueOccurrence[]> => {
    const { data } = await api.get(`${base}/overdue`)
    return data
  },
  skip: async (id: string, nominalDate: string): Promise<void> => {
    await api.post(`${base}/${id}/occurrences/${nominalDate}/skip`)
  },
  unskip: async (id: string, nominalDate: string): Promise<void> => {
    await api.delete(`${base}/${id}/occurrences/${nominalDate}/skip`)
  },
  record: async (
    id: string,
    nominalDate: string,
    body: { amount?: string; date?: string; destination_amount?: string },
  ): Promise<RecordedOccurrence> => {
    const { data } = await api.post(`${base}/${id}/occurrences/${nominalDate}/record`, body)
    return data
  },
  linkCandidates: async (id: string, nominalDate: string): Promise<LinkCandidate[]> => {
    const { data } = await api.get(`${base}/${id}/occurrences/${nominalDate}/link-candidates`)
    return data
  },
  link: async (id: string, nominalDate: string, transactionId: string): Promise<RecordedOccurrence> => {
    const { data } = await api.post(`${base}/${id}/occurrences/${nominalDate}/link`, {
      transaction_id: transactionId,
    })
    return data
  },
  /** from_date null corrects the whole series; a date splits it there. */
  changeFrom: async (
    id: string,
    body: { from_date: string | null; changes: Partial<RecurringTransaction>; preview: boolean },
  ): Promise<ChangeResult> => {
    const { data } = await api.post(`${base}/${id}/change-from`, body)
    return data
  },
  end: async (
    id: string,
    body: { last_date: string; delete_scheduled: boolean; preview: boolean },
  ): Promise<EndResult> => {
    const { data } = await api.post(`${base}/${id}/end`, body)
    return data
  },
  trash: async (id: string, body: { delete_scheduled: boolean; preview: boolean }): Promise<TrashResult> => {
    const { data } = await api.post(`${base}/${id}/trash`, body)
    return data
  },
  restore: async (id: string, preview: boolean): Promise<RestoreResult> => {
    const { data } = await api.post(`${base}/${id}/restore`, { preview })
    return data
  },
  purge: async (id: string, preview: boolean): Promise<PurgeResult> => {
    const { data } = await api.post(`${base}/${id}/purge`, { preview })
    return data
  },
}
