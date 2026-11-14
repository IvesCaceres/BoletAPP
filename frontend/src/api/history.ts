import { request } from './client'
import type { HistoryDetail, HistorySummary } from '../types'
export const historyApi = {
  list: (params: URLSearchParams) => request<HistorySummary[]>(`/bhe/history?${params.toString()}`),
  get: (id: number) => request<HistoryDetail>(`/bhe/history/${id}`)
}
