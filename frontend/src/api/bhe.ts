import { request } from './client'
import type { BheResponse, ManualBhePayload } from '../types'

export function emitBhe(payload: ManualBhePayload): Promise<BheResponse> {
  return request<BheResponse>('/bhe/emit', { method: 'POST', body: JSON.stringify(payload) })
}

export function confirmFinalBhe(emissionId: number): Promise<BheResponse> {
  return request<BheResponse>(`/bhe/emissions/${emissionId}/confirm-final`, {
    method: 'POST', body: JSON.stringify({ confirm: true }),
  })
}
