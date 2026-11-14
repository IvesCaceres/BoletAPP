import { request } from './client'
import type { Batch, BatchItem, BatchResult, ManualBatchItemPayload, Preview, RetentionMode } from '../types'
export const batchesApi = {
  createManual: (items: ManualBatchItemPayload[]) => request<Batch>('/bhe/batches/manual', { method: 'POST', body: JSON.stringify({ items }) }),
  addManualItem: (batchId: number, item: ManualBatchItemPayload) => request<Batch>(`/bhe/batches/${batchId}/manual-items`, { method: 'POST', body: JSON.stringify(item) }),
  updateManualItem: (batchId: number, itemId: number, item: ManualBatchItemPayload) => request<Batch>(`/bhe/batches/${batchId}/manual-items/${itemId}`, { method: 'PATCH', body: JSON.stringify(item) }),
  cloneEditable: (batchId: number) => request<Batch>(`/bhe/batches/${batchId}/clone-editable`, { method: 'POST' }),
  applyManualCommon: (batchId: number, common: { retention_mode: RetentionMode; direccion: string; region: number; comuna: string; detalle: string; fecha_emision: string }) => request<Batch>(`/bhe/batches/${batchId}/apply-manual-common`, { method: 'POST', body: JSON.stringify(common) }),
  list: () => request<Batch[]>('/bhe/batches'),
  get: (id: number) => request<Batch>(`/bhe/batches/${id}`),
  create: (period: string, fecha_emision: string, detalle_global?: string | null, retention_mode: RetentionMode = 'issuer_handles_ppm') => request<Batch>('/bhe/batches', { method: 'POST', body: JSON.stringify({ period, fecha_emision, detalle_global, retention_mode }) }),
  update: (id: number, payload: { fecha_emision?: string; detalle_global?: string | null; retention_mode?: RetentionMode | null }) => request<Batch>(`/bhe/batches/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  addItem: (batchId: number, client_id: number, monto: number, detalle: string, retention_mode_override?: RetentionMode | null) => request<BatchItem>(`/bhe/batches/${batchId}/items`, { method: 'POST', body: JSON.stringify({ client_id, monto, detalle, retention_mode_override }) }),
  updateItem: (batchId: number, itemId: number, monto: number, detalle: string, retention_mode_override?: RetentionMode | null) => request<BatchItem>(`/bhe/batches/${batchId}/items/${itemId}`, { method: 'PATCH', body: JSON.stringify({ monto, detalle, retention_mode_override }) }),
  removeItem: (batchId: number, itemId: number) => request<void>(`/bhe/batches/${batchId}/items/${itemId}`, { method: 'DELETE' }),
  applyGlobalDetail: (id: number) => request<Batch>(`/bhe/batches/${id}/apply-detail`, { method: 'POST' }),
  prepare: (id: number) => request<Preview>(`/bhe/batches/${id}/prepare`, { method: 'POST' }),
  emit: (id: number) => request<BatchResult>(`/bhe/batches/${id}/emit`, { method: 'POST', body: JSON.stringify({ confirm: true }) }),
  retryFailed: (id: number) => request<BatchResult>(`/bhe/batches/${id}/retry-failed`, { method: 'POST', body: JSON.stringify({ confirm: true }) }),
  retryItem: (batchId: number, itemId: number) => request<BatchResult>(`/bhe/batches/${batchId}/items/${itemId}/retry`, { method: 'POST', body: JSON.stringify({ confirm: true }) })
}
