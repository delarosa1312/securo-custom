import api from '@/lib/api'

/** Fork F-004: convert one previewed series of scheduled transfers into a recurring transfer. */
export async function applyTransferConversion(
  seriesId: string,
  body: { end_date?: string; open_ended?: boolean },
): Promise<{ schedule: { id: string }; linked_transfers: number }> {
  const { data } = await api.post(`/recurring-transactions/transfer-conversions/${seriesId}/apply`, body)
  return data
}
