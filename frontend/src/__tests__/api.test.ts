import { describe, expect, it, vi, beforeEach } from 'vitest'
import { batchesApi } from '../api/batches'
import { clientsApi } from '../api/clients'
import { ApiError, userFacingError } from '../api/client'
import { historyApi } from '../api/history'
import { confirmFinalBhe, emitBhe } from '../api/bhe'
import type { ManualBhePayload } from '../types'

const response = (body: unknown, ok = true, status = 200) => ({ ok, status, json: async () => body })
beforeEach(() => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({}))) })

describe('API frontend', () => {
  it('lista clientes activos', async () => { await clientsApi.list(); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/clients?include_inactive=false'), expect.anything()) })
  it('crea el lote manual por POST en el endpoint registrado', async () => {
    const item: ManualBhePayload = { rut_receptor: '99990001-1', razon_social: 'RECEPTOR TEST 034', direccion: 'DIRECCION PRUEBA 002', region: 13, comuna: 'Santiago', detalle: [{ descripcion: 'Servicio', cantidad: 1, precio_unitario: 45000 }], fecha_emision: '2026-11-05', retention_mode: 'issuer_handles_ppm' }
    await batchesApi.createManual([item])
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/batches/manual'), expect.objectContaining({ method: 'POST', body: JSON.stringify({ items: [item] }) }))
  })
  it('crea y edita clientes con JSON', async () => { await clientsApi.create({ rut: '1-9', razon_social: 'A', nombre_fantasia: 'Empresa Demo 013 Spa', direccion: 'D', region: 13, comuna: 'C' }); await clientsApi.update(4, { razon_social: 'B' }); expect(fetch).toHaveBeenNthCalledWith(1, expect.stringContaining('/clients'), expect.objectContaining({ body: expect.stringContaining('nombre_fantasia') })); expect(fetch).toHaveBeenCalledTimes(2) })
  it('desactiva cliente por soft delete endpoint', async () => { await clientsApi.deactivate(4); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/clients/4'), expect.objectContaining({ method: 'DELETE' })) })
  it('agrega monto entero y detalle al batch', async () => { await batchesApi.addItem(2, 5, 350000, 'Servicios'); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/batches/2/items'), expect.objectContaining({ method: 'POST', body: JSON.stringify({ client_id: 5, monto: 350000, detalle: 'Servicios' }) })) })
  it('crea y actualiza batches sin seleccionar provider', async () => { await batchesApi.create('2026-09', '2026-09-11', 'Servicios', 'issuer_handles_ppm'); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/batches'), expect.objectContaining({ method: 'POST', body: expect.not.stringContaining('provider') })); await batchesApi.update(2, { detalle_global: 'Nuevo detalle' }); expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining('/bhe/batches/2'), expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ detalle_global: 'Nuevo detalle' }) })) })
  it('prepare usa endpoint separado y no emite', async () => { await batchesApi.prepare(2); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/batches/2/prepare'), expect.objectContaining({ method: 'POST' })); expect(fetch).not.toHaveBeenCalledWith(expect.stringContaining('/bhe/batches/2/emit'), expect.anything()) })
  it('emisión requiere body confirm true', async () => { await batchesApi.emit(2); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/batches/2/emit'), expect.objectContaining({ body: JSON.stringify({ confirm: true }) })) })
  it('retry solo usa retry-failed', async () => { await batchesApi.retryFailed(2); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/batches/2/retry-failed'), expect.anything()) })
  it('retry individual incluye el id del item y confirmación explícita', async () => { await batchesApi.retryItem(6, 11); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/batches/6/items/11/retry'), expect.objectContaining({ method: 'POST', body: JSON.stringify({ confirm: true }) })) })
  it('historial construye filtros', async () => { const p = new URLSearchParams({ status: 'failed', rut_receptor: '1-9' }); await historyApi.list(p); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('status=failed'), expect.anything()) })
  it('obtiene detalle individual', async () => { await historyApi.get(8); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/history/8'), expect.anything()) })
  it('emite BHE manual con cantidad 1 y precio unitario igual al monto', async () => { await emitBhe({ rut_receptor: '1-9', razon_social: 'A', direccion: 'D', region: 13, comuna: 'C', detalle: [{ descripcion: 'Servicio', cantidad: 1, precio_unitario: 250000 }], fecha_emision: '2026-09-11' }); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/emit'), expect.objectContaining({ method: 'POST', body: expect.stringContaining('"cantidad":1') })) })
  it('envía la modalidad semántica de retención', async () => { await emitBhe({ rut_receptor: '1-9', razon_social: 'A', direccion: 'D', region: 13, comuna: 'C', detalle: [{ descripcion: 'Servicio', cantidad: 1, precio_unitario: 250000 }], fecha_emision: '2026-09-11', retention_mode: 'receiver_retains_ppm' }); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/emit'), expect.objectContaining({ body: expect.stringContaining('receiver_retains_ppm') })) })
  it('confirma la emisiÃ³n SII solo por endpoint separado', async () => { await confirmFinalBhe(42); expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/bhe/emissions/42/confirm-final'), expect.objectContaining({ method: 'POST', body: JSON.stringify({ confirm: true }) })) })
  it('actualiza y aplica el detalle global del batch', async () => { await batchesApi.update(2, { detalle_global: 'Detalle común' }); await batchesApi.applyGlobalDetail(2); expect(fetch).toHaveBeenNthCalledWith(1, expect.stringContaining('/bhe/batches/2'), expect.objectContaining({ method: 'PATCH' })); expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining('/bhe/batches/2/apply-detail'), expect.objectContaining({ method: 'POST' })) })
  it('muestra mensajes útiles para 409, 422, 500 y red', () => { expect(userFacingError(new ApiError(409, 'Error 409'))).toBe('Ya existe un cliente con este RUT.'); expect(userFacingError(new ApiError(422, 'Error 422. campo inválido'))).toContain('campo inválido'); expect(userFacingError(new ApiError(500, 'Error 500'))).toContain('error interno'); expect(userFacingError(new TypeError('Failed to fetch'))).toContain('conectar') })
})
