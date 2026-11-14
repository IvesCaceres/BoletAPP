export class ApiError extends Error { constructor(public status: number, message: string) { super(message) } }
export function userFacingError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return 'Ya existe un cliente con este RUT.'
    if (error.status === 422) return error.message.replace(/^Error 422\.?\s*/, '') || 'Revisa los campos ingresados.'
    if (error.status >= 500) return 'No fue posible guardar el cliente por un error interno.'
    return error.message
  }
  if (error instanceof TypeError) return 'No fue posible conectar con el servidor.'
  if (error instanceof Error) return error.message
  return 'No fue posible completar la operación.'
}
// Packaged builds always use the integrated backend's origin, including dynamic ports.
const base = (import.meta.env.DEV ? (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8001') : '').replace(/\/$/, '')
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, { headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) }, ...init })
  if (!response.ok) { let message = `Error ${response.status}`; try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; else if (Array.isArray(body.detail)) message = body.detail.map((item: { msg?: string }) => item.msg || 'Dato inválido').join('. ') } catch { /* empty */ } throw new ApiError(response.status, message) }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
