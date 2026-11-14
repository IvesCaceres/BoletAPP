import { request } from './client'
import type { Client, ClientPayload } from '../types'
export const clientsApi = {
  list: (includeInactive = false) => request<Client[]>(`/clients?include_inactive=${includeInactive}`),
  create: (payload: ClientPayload) => request<Client>('/clients', { method: 'POST', body: JSON.stringify(payload) }),
  update: (id: number, payload: Partial<ClientPayload> & { activo?: boolean }) => request<Client>(`/clients/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deactivate: (id: number) => request<Client>(`/clients/${id}`, { method: 'DELETE' })
}
