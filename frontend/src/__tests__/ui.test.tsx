import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import App from '../App'
import { defaultGlobalDetail } from '../App'
import { clientsApi } from '../api/clients'
import { batchesApi } from '../api/batches'
import { historyApi } from '../api/history'
import { emitBhe, confirmFinalBhe } from '../api/bhe'

vi.mock('../api/clients', () => ({ clientsApi: { list: vi.fn().mockResolvedValue([]), create: vi.fn(), update: vi.fn(), deactivate: vi.fn() } }))
vi.mock('../api/batches', () => ({ batchesApi: { list: vi.fn().mockResolvedValue([]), get: vi.fn(), create: vi.fn(), update: vi.fn(), createManual: vi.fn(), addManualItem: vi.fn(), updateManualItem: vi.fn(), cloneEditable: vi.fn(), applyManualCommon: vi.fn(), applyGlobalDetail: vi.fn(), addItem: vi.fn(), updateItem: vi.fn(), removeItem: vi.fn(), prepare: vi.fn(), emit: vi.fn(), retryFailed: vi.fn() } }))
vi.mock('../api/history', () => ({ historyApi: { list: vi.fn().mockResolvedValue([]), get: vi.fn() } }))
vi.mock('../api/bhe', () => ({ emitBhe: vi.fn().mockResolvedValue({ success: true, folio: 600, fecha_emision: '2026-09-11' }), confirmFinalBhe: vi.fn().mockResolvedValue({ success: true, folio: 600, fecha_emision: '2026-09-11' }) }))

beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); vi.mocked(batchesApi.list).mockResolvedValue([]) })
afterEach(() => vi.useRealTimers())

const fillBhe = async () => {
  await waitFor(() => expect(screen.getByRole('button', { name: /Agregar boleta/ })).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '999900011' } })
  fireEvent.change(screen.getByLabelText(/Direcci/), { target: { value: 'DIRECCION PRUEBA 002' } })
  fireEvent.change(screen.getByLabelText('Comuna'), { target: { value: 'Santiago' } })
  fireEvent.change(screen.getByLabelText('Detalle'), { target: { value: 'Servicio' } })
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '100000' } })
}

it('renderiza los datos comunes y mantiene opcionales ocultos en la entrada rápida', async () => {
  window.history.pushState({}, '', '/new-bhe')
  render(<App />)
  expect(screen.getByText(/EMISI/)).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Datos comunes del lote' })).toBeTruthy()
  expect(screen.queryByLabelText(/Nombre/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  expect(screen.getByLabelText(/Nombre/)).toBeTruthy()
  expect(screen.getAllByLabelText('Región').length).toBeGreaterThan(0)
})

it('agrega boletas temporales sin emitir ni crear clientes', async () => {
  const item: any = { id: 1, client_id: null, razon_social: '', rut_receptor: '999900011', direccion: 'DIRECCION PRUEBA 002', region: 13, comuna: 'Santiago', detalle: [{ descripcion: 'Servicio' }], monto: 100000, fecha_emision: '2026-11-05', retention_mode_override: 'issuer_handles_ppm', status: 'pending', folio: null }
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce({ id: 91, period: '2026-11', source: 'manual', status: 'ready', fecha_emision: '2026-11-05', provider: 'sii', items: [item] } as any)
  window.history.pushState({}, '', '/new-bhe')
  render(<App />)
  await fillBhe()
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  expect(await screen.findByText('Sin nombre')).toBeTruthy()
  expect(screen.getByText('99.990.001-1')).toBeTruthy()
  expect(screen.getByText('Pendiente')).toBeTruthy()
  const table = screen.getByRole('table')
  expect(within(table).getByRole('columnheader', { name: 'Receptor' })).toBeTruthy()
  expect(within(table).queryByRole('columnheader', { name: 'Folio' })).toBeNull()
  expect(within(table).getByRole('columnheader', { name: 'Acciones' })).toBeTruthy()
  expect(screen.getByLabelText('RUT receptor')).toHaveValue('')
  expect(JSON.parse(localStorage.getItem('appboletas.manualDraft')!).common.comuna).toBe('Santiago')
  expect(document.querySelector('.manual-form-actions')).toBeTruthy()
  expect(emitBhe).not.toHaveBeenCalled()
  expect(clientsApi.create).not.toHaveBeenCalled()
})

it('allows managing multiple temporary items and retains final folio in the same list', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const item: any = { id: 1, batch_id: 91, client_id: null, razon_social: 'Receptor Test 039', rut_receptor: '999900011', direccion: 'DIRECCION PRUEBA 002', region: 13, comuna: 'Santiago', detalle: 'Servicio', monto: 55000, fecha_emision: '2026-11-05', retention_mode_override: 'issuer_handles_ppm', status: 'pending', folio: null }
  const ready: any = { id: 91, period: '2026-11', source: 'manual', status: 'ready', fecha_emision: '2026-11-05', provider: 'sii', items: [item] }
  const ready2: any = { ...ready, items: [item, { ...item, id: 2, rut_receptor: '999900119', monto: 80000 }] }
  const done: any = { ...ready, status: 'completed', items: [{ ...item, status: 'success', folio: 1006, bhe_emission_id: 500 }] }
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce(ready)
  vi.mocked(batchesApi.addManualItem).mockResolvedValueOnce(ready2)
  vi.mocked(batchesApi.updateManualItem).mockResolvedValueOnce(ready2)
  vi.mocked(batchesApi.emit).mockResolvedValueOnce({} as any)
  vi.mocked(batchesApi.get).mockResolvedValueOnce(done)
  render(<App />)
  await fillBhe()
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  await screen.findByText('Pendiente')
  await fillBhe()
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '80000' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  expect(await screen.findByText(/2 boletas preparadas/)).toBeTruthy()
  fireEvent.click(screen.getAllByRole('button', { name: 'Editar' })[0])
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '55000' } })
  fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }))
  fireEvent.click(screen.getAllByRole('button', { name: 'Eliminar' })[1])
  expect(await screen.findByText(/1 boletas preparadas/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Procesar todas' }))
  expect(await screen.findByText(/Una vez emitidas en el SII/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Emitir 1 boletas' }))
  expect(await screen.findByText('1006')).toBeTruthy()
  expect(screen.getByText('Emitida')).toBeTruthy()
  expect(batchesApi.createManual).toHaveBeenCalledTimes(1)
  expect(batchesApi.addManualItem).toHaveBeenCalledTimes(1)
  expect(clientsApi.create).not.toHaveBeenCalled()
  expect(emitBhe).not.toHaveBeenCalled()
})

it('restores the latest manual batch and its folio when opening Nueva boleta again', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const completed: any = { id: 93, period: '2026-11', source: 'manual', status: 'completed', fecha_emision: '2026-11-05', provider: 'sii', items: [{ id: 3, client_id: null, razon_social: '', sii_resolved_recipient_name: 'cliente prueba uno', rut_receptor: '999900011', direccion: 'DIRECCION PRUEBA 002', region: 13, comuna: 'Santiago', detalle: 'Servicio', monto: 55000, fecha_emision: '2026-11-05', retention_mode_override: 'issuer_handles_ppm', status: 'success', folio: 1007 }] }
  vi.mocked(batchesApi.list).mockResolvedValueOnce([completed])
  vi.mocked(batchesApi.get).mockResolvedValueOnce(completed)
  render(<App />)
  expect(await screen.findByText('1007')).toBeTruthy()
  expect(screen.getByText('Emitida')).toBeTruthy()
  const receiver = screen.getByText('cliente prueba uno')
  expect(receiver).toHaveClass('recipient-name')
  expect(completed.items[0].sii_resolved_recipient_name).toBe('cliente prueba uno')
  expect(screen.getByText('99.990.001-1')).toBeTruthy()
  const table = screen.getByRole('table')
  expect(within(table).getByRole('columnheader', { name: 'Folio' })).toBeTruthy()
  expect(within(table).queryByRole('columnheader', { name: 'Acciones' })).toBeNull()
  expect(screen.getByRole('button', { name: '+ Nuevo lote' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Reintentar fallidos' })).toBeNull()
  expect(screen.getByRole('heading', { name: 'Boletas preparadas' })).toBeTruthy()
})

it('retries only backend-approved failed manual items and keeps successful folios', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const success: any = { id: 301, client_id: null, razon_social: 'Receptor Test 035', rut_receptor: '999900011', detalle: 'Servicio', monto: 45000, status: 'success', folio: 1006, retryable: false }
  const failed: any = { id: 302, client_id: null, razon_social: 'Receptor Test 036', rut_receptor: '999900119', detalle: 'Servicio', monto: 80000, status: 'failed', error_message: 'Exception at stage preview_reached', folio: null, final_click_occurred: false, retryable: true }
  const unknown: any = { id: 303, client_id: null, razon_social: 'Receptor Test 037', rut_receptor: '999900011', detalle: 'Servicio', monto: 60000, status: 'emission_status_unknown', folio: null, final_click_occurred: true, retryable: false }
  const before: any = { id: 96, source: 'manual', status: 'completed_with_errors', fecha_emision: '2026-11-05', items: [success, failed, unknown] }
  const after: any = { ...before, items: [success, { ...failed, status: 'success', folio: 1008 }, unknown] }
  vi.mocked(batchesApi.list).mockResolvedValueOnce([before])
  vi.mocked(batchesApi.get).mockResolvedValueOnce(before).mockResolvedValueOnce(after)
  vi.mocked(batchesApi.retryFailed).mockResolvedValueOnce({} as any)
  render(<App />)
  expect(await screen.findByText(/Revisión manual requerida para/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Reintentar fallidos' }))
  expect(await screen.findByText(/Los items con estado incierto/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Reintentar 1 boleta' }))
  expect(await screen.findByText('1008')).toBeTruthy()
  expect(screen.getByText('1006')).toBeTruthy()
  expect(batchesApi.retryFailed).toHaveBeenCalledTimes(1)
  expect(batchesApi.removeItem).not.toHaveBeenCalled()
})

it('starts an empty new manual lot while preserving the previous failed batch', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const failed: any = { id: 401, client_id: null, razon_social: 'RECEPTOR PRUEBA 06', rut_receptor: '999900011', detalle: 'Servicio', monto: 45000, status: 'failed', folio: null, final_click_occurred: true, retryable: false }
  const previous: any = { id: 97, source: 'manual', status: 'completed_with_errors', fecha_emision: '2026-11-05', items: [failed] }
  vi.mocked(batchesApi.list).mockResolvedValueOnce([previous])
  vi.mocked(batchesApi.get).mockResolvedValueOnce(previous)
  render(<App />)
  await screen.findByText(/Revisión manual requerida para/)
  fireEvent.click(screen.getByRole('button', { name: '+ Nuevo lote' }))
  expect(await screen.findByText(/Este lote contiene/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Nuevo lote' }))
  expect(await screen.findByText('Agrega una boleta para comenzar.')).toBeTruthy()
  expect(screen.getByLabelText('RUT receptor')).toBeTruthy()
  expect(batchesApi.removeItem).not.toHaveBeenCalled()
  expect(batchesApi.retryFailed).not.toHaveBeenCalled()
})

it('clones safe failures into an editable lot and applies the common date explicitly', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const failed: any = { id: 54, client_id: null, rut_receptor: '999900011', razon_social: '', direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt', detalle: 'Old detail', monto: 100, fecha_emision: '2026-07-31', retention_mode_override: 'issuer_handles_ppm', status: 'failed', folio: null, retryable: true, final_click_occurred: false }
  const oldBatch: any = { id: 9, source: 'manual', status: 'completed_with_errors', fecha_emision: '2026-07-31', items: Array.from({ length: 14 }, (_, index) => ({ ...failed, id: 54 + index })) }
  const cloned: any = { id: 10, source: 'manual', source_batch_id: 9, status: 'ready', fecha_emision: '2026-07-31', items: oldBatch.items.map((item: any, index: number) => ({ ...item, id: 68 + index, status: 'pending', retryable: false, error_message: null })) }
  const applied: any = { ...cloned, fecha_emision: '2026-10-01', items: cloned.items.map((item: any) => ({ ...item, fecha_emision: '2026-10-01' })) }
  vi.mocked(batchesApi.list).mockResolvedValueOnce([oldBatch])
  vi.mocked(batchesApi.get).mockResolvedValueOnce(oldBatch)
  vi.mocked(batchesApi.cloneEditable).mockResolvedValueOnce(cloned)
  vi.mocked(batchesApi.applyManualCommon).mockResolvedValueOnce(applied)
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Editar lote' }))
  expect(screen.getByText(/El lote original permanecerá sin cambios/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Editar 14 boletas' }))
  expect(await screen.findByText(/14 boletas preparadas/)).toBeTruthy()
  expect(screen.getAllByRole('button', { name: 'Editar' })).toHaveLength(14)
  expect(screen.queryByRole('columnheader', { name: 'Folio' })).toBeNull()
  fireEvent.change(screen.getByLabelText(/Fecha de emisi/), { target: { value: '2026-10-01' } })
  fireEvent.click(screen.getByRole('button', { name: /Aplicar datos comunes a 14 pendientes/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Aplicar a 14 pendientes' }))
  await waitFor(() => expect(batchesApi.applyManualCommon).toHaveBeenCalledWith(10, expect.objectContaining({ fecha_emision: '2026-10-01', region: 10 })))
  expect(screen.getAllByText('2026-10-01')).toHaveLength(14)
  expect(screen.getByRole('button', { name: 'Procesar todas' })).toBeTruthy()
  expect(batchesApi.retryFailed).not.toHaveBeenCalled()
  expect(clientsApi.create).not.toHaveBeenCalled()
})

it('limits common and customized date inputs to the local 90-day window', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 6, 12))
  window.history.pushState({}, '', '/new-bhe')
  render(<App />)
  await fillBhe()
  const commonDate = screen.getByLabelText('Fecha de emisión') as HTMLInputElement
  expect(commonDate.min).toBe('2026-08-08')
  expect(commonDate.max).toBe('2026-11-06')
  fireEvent.change(commonDate, { target: { value: '2026-08-07' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  expect(await screen.findByText(/más de 90 días de antigüedad/)).toBeTruthy()
  expect(batchesApi.createManual).not.toHaveBeenCalled()

  fireEvent.change(commonDate, { target: { value: '2026-08-08' } })
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  const itemDate = screen.getAllByLabelText('Fecha de emisión')[1] as HTMLInputElement
  expect(itemDate.min).toBe('2026-08-08')
  expect(itemDate.max).toBe('2026-11-06')
  fireEvent.change(itemDate, { target: { value: '2026-08-07' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  expect(batchesApi.createManual).not.toHaveBeenCalled()
})

it('blocks a stored batch with one old date before calling emit or bulk update', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 6, 12))
  window.history.pushState({}, '', '/new-bhe')
  const item: any = { id: 1, client_id: null, rut_receptor: '999900011', razon_social: '', direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt', detalle: 'Servicio', monto: 100, fecha_emision: '2026-11-06', retention_mode_override: 'issuer_handles_ppm', status: 'pending', folio: null }
  const ready: any = { id: 10, source: 'manual', status: 'ready', fecha_emision: '2026-11-06', items: Array.from({ length: 14 }, (_, index) => ({ ...item, id: index + 1, fecha_emision: index === 13 ? '2026-08-07' : '2026-11-06' })) }
  vi.mocked(batchesApi.list).mockResolvedValueOnce([ready])
  vi.mocked(batchesApi.get).mockResolvedValueOnce(ready)
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Procesar todas' }))
  fireEvent.click(screen.getByRole('button', { name: 'Emitir 14 boletas' }))
  expect(await screen.findByText(/más de 90 días de antigüedad/)).toBeTruthy()
  expect(batchesApi.emit).not.toHaveBeenCalled()

  fireEvent.change(screen.getByLabelText('Dirección'), { target: { value: 'Puerto Montt' } })
  fireEvent.change(screen.getByLabelText('Comuna'), { target: { value: 'Puerto Montt' } })
  fireEvent.change(screen.getByLabelText('Detalle'), { target: { value: 'Servicio' } })
  fireEvent.change(screen.getByLabelText('Fecha de emisión'), { target: { value: '2026-08-07' } })
  fireEvent.click(screen.getByRole('button', { name: /Aplicar datos comunes a 14 pendientes/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Aplicar a 14 pendientes' }))
  expect(batchesApi.applyManualCommon).not.toHaveBeenCalled()
})

it('prepara la boleta sin navegar al SII', async () => {
  const item: any = { id: 1, client_id: null, razon_social: 'RECEPTOR PRUEBA 06', rut_receptor: '999900011', direccion: 'DIRECCION PRUEBA 002', region: 13, comuna: 'Santiago', detalle: [{ descripcion: 'Servicio' }], monto: 100000, fecha_emision: '2026-11-05', retention_mode_override: 'issuer_handles_ppm', status: 'pending', folio: null }
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce({ id: 92, period: '2026-11', source: 'manual', status: 'ready', fecha_emision: '2026-11-05', provider: 'sii', items: [item] } as any)
  window.history.pushState({}, '', '/new-bhe')
  render(<App />)
  await fillBhe()
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  expect(await screen.findByText('Boletas preparadas')).toBeTruthy()
  expect(emitBhe).not.toHaveBeenCalled()
})

it('preserves a partially entered manual form after section navigation and remount', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const { unmount } = render(<App />)
  fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '999900011' } })
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Borrador temporal' } })
  fireEvent.click(screen.getByRole('button', { name: 'Historial' }))
  unmount()
  window.history.pushState({}, '', '/new-bhe')
  render(<App />)
  expect(await screen.findByDisplayValue('999900011')).toBeTruthy()
  expect(screen.getByDisplayValue('Borrador temporal')).toBeTruthy()
})

it('copies common values as snapshots and supports per-item overrides', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const item: any = { id: 1, client_id: null, razon_social: '', rut_receptor: '999900011', direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt', detalle: 'SERVICIOS ECOGRAFIA', monto: 80000, fecha_emision: '2026-11-05', retention_mode_override: 'receiver_retains_ppm', status: 'pending', folio: null }
  const one: any = { id: 98, source: 'manual', status: 'ready', fecha_emision: '2026-11-05', items: [item] }
  const two: any = { ...one, items: [item, { ...item, id: 2, rut_receptor: '999900119', direccion: 'Puerto Montt', detalle: 'SERVICIOS OCTUBRE', monto: 45000, fecha_emision: '2026-11-06' }] }
  const three: any = { ...two, items: [...two.items, { ...item, id: 3, direccion: 'Puerto Varas', comuna: 'Puerto Varas', detalle: 'SERVICIO PARTICULAR', fecha_emision: '2026-11-06', monto: 120000 }] }
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce(one)
  vi.mocked(batchesApi.addManualItem).mockResolvedValueOnce(two).mockResolvedValueOnce(three)
  render(<App />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Agregar boleta/ })).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('Dirección'), { target: { value: 'Puerto Montt' } })
  fireEvent.change(screen.getByLabelText('Región'), { target: { value: '10' } })
  fireEvent.change(screen.getByLabelText('Comuna'), { target: { value: 'Puerto Montt' } })
  fireEvent.change(screen.getByLabelText('Detalle'), { target: { value: 'SERVICIOS ECOGRAFIA' } })
  fireEvent.change(screen.getByLabelText('Fecha de emisión'), { target: { value: '2026-11-05' } })
  fireEvent.click(screen.getByLabelText('El receptor retiene el PPM'))
  const addQuick = async (rut: string, amount: string) => {
    fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: rut } })
    fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: amount } })
    fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
    await waitFor(() => expect(screen.getByLabelText('RUT receptor')).toHaveValue(''))
  }
  await addQuick('999900011', '80000')
  fireEvent.change(screen.getByLabelText('Detalle'), { target: { value: 'SERVICIOS OCTUBRE' } })
  fireEvent.change(screen.getByLabelText('Fecha de emisión'), { target: { value: '2026-11-06' } })
  await addQuick('999900119', '45000')
  fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '999900011' } })
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '120000' } })
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  fireEvent.change(screen.getAllByLabelText(/Direcci/)[1], { target: { value: 'Puerto Varas' } })
  fireEvent.change(screen.getAllByLabelText('Comuna')[1], { target: { value: 'Puerto Varas' } })
  fireEvent.change(screen.getAllByLabelText('Detalle')[1], { target: { value: 'SERVICIO PARTICULAR' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  await screen.findByText(/3 boletas preparadas/)
  const firstPayload = vi.mocked(batchesApi.createManual).mock.calls[0][0][0]
  const secondPayload = vi.mocked(batchesApi.addManualItem).mock.calls[0][1]
  const thirdPayload = vi.mocked(batchesApi.addManualItem).mock.calls[1][1]
  expect(firstPayload).toMatchObject({ direccion: 'Puerto Montt', comuna: 'Puerto Montt', detalle: [{ descripcion: 'SERVICIOS ECOGRAFIA' }], fecha_emision: '2026-11-05', retention_mode: 'receiver_retains_ppm' })
  expect(secondPayload).toMatchObject({ direccion: 'Puerto Montt', comuna: 'Puerto Montt', detalle: [{ descripcion: 'SERVICIOS OCTUBRE' }], fecha_emision: '2026-11-06', retention_mode: 'receiver_retains_ppm' })
  expect(thirdPayload).toMatchObject({ direccion: 'Puerto Varas', comuna: 'Puerto Varas', detalle: [{ descripcion: 'SERVICIO PARTICULAR' }], fecha_emision: '2026-11-06' })
  expect(firstPayload.razon_social).toBeUndefined()
  expect(firstPayload.giro).toBeUndefined()
  expect(firstPayload.email).toBeUndefined()
  expect(clientsApi.create).not.toHaveBeenCalled()
})

it('uses common snapshot values for quick RUT and amount entry when an old draft says customized', async () => {
  window.history.pushState({}, '', '/new-bhe')
  localStorage.setItem('appboletas.manualDraft', JSON.stringify({
    form: { rut_receptor: '', monto: '' },
    common: { retention_mode: 'issuer_handles_ppm', direccion: 'Puerto Montt', region: '10', comuna: 'Puerto Montt', detalle: 'servicios veterinarios', fecha_emision: '2026-10-01' },
    customized: true,
    customExpanded: false,
  }))
  const item: any = { id: 1, client_id: null, razon_social: '', rut_receptor: '999900011', direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt', detalle: [{ descripcion: 'servicios veterinarios' }], monto: 100, fecha_emision: '2026-10-01', retention_mode_override: 'issuer_handles_ppm', status: 'pending', folio: null }
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce({ id: 100, source: 'manual', status: 'ready', fecha_emision: '2026-10-01', items: [item] } as any)
  render(<App />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Agregar boleta/ })).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '999900011' } })
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '100' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  await screen.findByText('Pendiente')
  expect(screen.queryByText('Completa dirección.')).toBeNull()
  const payload = vi.mocked(batchesApi.createManual).mock.calls[0][0][0]
  expect(payload).toMatchObject({
    rut_receptor: '999900011', direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt',
    detalle: [{ descripcion: 'servicios veterinarios', cantidad: 1, precio_unitario: 100 }],
    fecha_emision: '2026-10-01', retention_mode: 'issuer_handles_ppm',
  })
  expect(payload.razon_social).toBeUndefined()
  expect(payload.giro).toBeUndefined()
  expect(payload.email).toBeUndefined()
  expect(clientsApi.create).not.toHaveBeenCalled()
})

it('inherits all common values when personalization is opened without editing fields', async () => {
  window.history.pushState({}, '', '/new-bhe')
  localStorage.setItem('appboletas.manualDraft', JSON.stringify({
    form: {}, common: { retention_mode: 'issuer_handles_ppm', direccion: 'Puerto Montt', region: '10', comuna: 'Puerto Montt', detalle: 'Servicio', fecha_emision: '2026-10-01' },
    overrides: {}, customExpanded: false,
  }))
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce({ id: 101, source: 'manual', status: 'ready', fecha_emision: '2026-10-01', items: [{ id: 1, client_id: null, rut_receptor: '999900011', direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt', detalle: 'Servicio', monto: 100, fecha_emision: '2026-10-01', status: 'pending' }] } as any)
  render(<App />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Agregar boleta/ })).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '999900011' } })
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '100' } })
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  await screen.findByText('Pendiente')
  expect(vi.mocked(batchesApi.createManual).mock.calls[0][0][0]).toMatchObject({ direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt', fecha_emision: '2026-10-01', retention_mode: 'issuer_handles_ppm' })
})

it('applies a date-only override and blocks an explicitly cleared required override', async () => {
  window.history.pushState({}, '', '/new-bhe')
  localStorage.setItem('appboletas.manualDraft', JSON.stringify({
    form: {}, common: { retention_mode: 'issuer_handles_ppm', direccion: 'Puerto Montt', region: '10', comuna: 'Puerto Montt', detalle: 'Servicio', fecha_emision: '2026-10-01' },
    overrides: {}, customExpanded: false,
  }))
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce({ id: 102, source: 'manual', status: 'ready', fecha_emision: '2026-10-01', items: [{ id: 1, client_id: null, rut_receptor: '999900011', direccion: 'Puerto Montt', region: 10, comuna: 'Puerto Montt', detalle: 'Servicio', monto: 100, fecha_emision: '2026-10-02', status: 'pending' }] } as any)
  render(<App />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Agregar boleta/ })).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '999900011' } })
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '100' } })
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  fireEvent.change(screen.getAllByLabelText(/Fecha de emisi/)[1], { target: { value: '2026-10-02' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  await screen.findByText('Pendiente')
  expect(vi.mocked(batchesApi.createManual).mock.calls[0][0][0]).toMatchObject({ direccion: 'Puerto Montt', comuna: 'Puerto Montt', detalle: [{ descripcion: 'Servicio' }], fecha_emision: '2026-10-02' })

})

it('blocks an explicitly cleared required override instead of inheriting common address', async () => {
  window.history.pushState({}, '', '/new-bhe')
  localStorage.setItem('appboletas.manualDraft', JSON.stringify({
    form: {}, common: { retention_mode: 'issuer_handles_ppm', direccion: 'Puerto Montt', region: '10', comuna: 'Puerto Montt', detalle: 'Servicio', fecha_emision: '2026-10-01' },
    overrides: {}, customExpanded: false,
  }))
  render(<App />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Agregar boleta/ })).not.toBeDisabled())
  fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '999900011' } })
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '100' } })
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  fireEvent.change(screen.getAllByLabelText(/Direcci/)[1], { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  expect((screen.getAllByLabelText(/Direcci/)[1] as HTMLInputElement).checkValidity()).toBe(false)
  expect(batchesApi.createManual).not.toHaveBeenCalled()
})

it('keeps the backend manual queue across section remount without creating another batch', async () => {
  window.history.pushState({}, '', '/new-bhe')
  const first: any = { id: 1, client_id: null, razon_social: 'Receptor Test 039', rut_receptor: '999900011', direccion: 'DIRECCION PRUEBA 002', region: 13, comuna: 'Santiago', detalle: [{ descripcion: 'Servicio' }], monto: 100000, fecha_emision: '2026-11-05', retention_mode_override: 'issuer_handles_ppm', status: 'pending', folio: null }
  const second = { ...first, id: 2, razon_social: 'Receptor Test 038', rut_receptor: '999900119', monto: 80000 }
  const one: any = { id: 95, period: '2026-11', source: 'manual', status: 'ready', fecha_emision: '2026-11-05', provider: 'sii', items: [first] }
  const two: any = { ...one, items: [first, second] }
  vi.mocked(batchesApi.createManual).mockResolvedValueOnce(one)
  vi.mocked(batchesApi.addManualItem).mockResolvedValueOnce(two)
  vi.mocked(batchesApi.list).mockResolvedValue([])
  vi.mocked(batchesApi.get).mockResolvedValue(two)
  const { unmount } = render(<App />)
  await fillBhe()
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  await screen.findByText('Pendiente')
  await fillBhe()
  fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '80000' } })
  fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
  fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Receptor Test 038' } })
  fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
  await screen.findByText('Receptor Test 038')
  vi.mocked(batchesApi.list).mockResolvedValue([two])
  fireEvent.click(screen.getByRole('button', { name: 'Historial' }))
  unmount()
  window.history.pushState({}, '', '/new-bhe')
  render(<App />)
  expect(await screen.findByText('Receptor Test 039')).toBeTruthy()
  expect((await screen.findAllByText('Receptor Test 038')).length).toBeGreaterThan(0)
  expect(batchesApi.createManual).toHaveBeenCalledTimes(1)
  expect(two.items.every((item: any) => item.client_id === null)).toBe(true)
})

describe('pantallas principales', () => {
  it('crea el batch mensual directamente para SII', async () => {
    vi.mocked(batchesApi.create).mockResolvedValueOnce({ id: 8, period: '2026-09', status: 'draft', fecha_emision: '2026-09-11', provider: 'sii', items: [] } as any)
    window.history.pushState({}, '', '/')
    render(<App />)
    expect(screen.queryByLabelText(/Proveedor de emisi/)).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: /Crear batch/ }))
    expect(vi.mocked(batchesApi.create)).toHaveBeenCalledWith(expect.any(String), expect.any(String), expect.any(String), expect.any(String))
  })

  it('abre y cierra la navegacion responsive', () => {
    render(<App />)
    const menu = screen.getByRole('button', { name: /Abrir men/ })
    fireEvent.click(menu)
    expect(menu).toHaveAttribute('aria-expanded', 'true')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(menu).toHaveAttribute('aria-expanded', 'false')
  })

  it('renderiza Historial y filtros', async () => {
    window.history.pushState({}, '', '/history')
    render(<App />)
    expect(await screen.findByText('Historial BHE')).toBeTruthy()
    expect(screen.getByText('Todos los estados')).toBeTruthy()
  })

  it('mantiene el historial mobile compacto y permite expandir metadata', async () => {
    vi.mocked(historyApi.list).mockResolvedValueOnce([{ id: 41, razon_social: 'CLIENTE PRUEBA UNO', rut_receptor: '99990023-2', monto_total: 80000, status: 'success', provider: 'sii', folio: 1004, fecha_emision_solicitada: '2026-09-24', created_at: '2026-09-24T12:00:00Z' } as any])
    window.history.pushState({}, '', '/history')
    render(<App />)
    const summary = await screen.findByRole('button', { name: /CLIENTE PRUEBA UNO.*Emitida/ })
    expect(summary).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(summary)
    expect(summary).toHaveAttribute('aria-expanded', 'true')
    expect(within(document.querySelector('.history-mobile-list') as HTMLElement).getByText('RUT receptor')).toBeTruthy()
  })

  it('muestra nombre de fantasía y tooltip accesible en historial desktop y mobile', async () => {
    vi.mocked(historyApi.list).mockResolvedValueOnce([{
      id: 42,
      client_id: 7,
      nombre_fantasia: 'EMPRESA DEMO VISUAL',
      razon_social: 'RECEPTOR TEST 031',
      rut_receptor: '99990011-9',
      direccion: 'DIRECCION PRUEBA 002',
      region: 13,
      comuna: 'Santiago',
      detalle: [{ descripcion: 'Servicio ficticio' }],
      monto_total: 80000,
      status: 'success',
      provider: 'sii',
      folio: 1005,
      fecha_emision_solicitada: '2026-09-24',
      created_at: '2026-09-24T12:00:00Z',
    } as any])
    window.history.pushState({}, '', '/history')
    render(<App />)

    expect(await screen.findAllByText('EMPRESA DEMO VISUAL')).toHaveLength(2)
    const infoButtons = await screen.findAllByRole('button', { name: /Información de EMPRESA DEMO VISUAL/ })
    expect(infoButtons).toHaveLength(2)
    fireEvent.focus(infoButtons[0])
    expect(await screen.findByRole('tooltip')).toHaveTextContent('RECEPTOR TEST 031')
    expect(screen.getByRole('tooltip')).toHaveTextContent('DIRECCION PRUEBA 002')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Santiago')
    expect(screen.getByText('1005')).toBeTruthy()
    expect(screen.getAllByText('Emitida')).toHaveLength(2)
  })

  it('usa razón social y mantiene folio cuando falta nombre de fantasía', async () => {
    vi.mocked(historyApi.list).mockResolvedValueOnce([{
      id: 43,
      nombre_fantasia: null,
      razon_social: 'RECEPTOR TEST 009',
      rut_receptor: '99990013-5',
      monto_total: 50000,
      status: 'failed',
      provider: 'sii',
      folio: null,
      fecha_emision_solicitada: '2026-09-24',
      created_at: '2026-09-24T12:00:00Z',
    } as any])
    window.history.pushState({}, '', '/history')
    render(<App />)

    expect(await screen.findAllByText('RECEPTOR TEST 009')).toHaveLength(2)
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Fallida')).toHaveLength(2)
  })

  it('renderiza Nueva boleta y revision local', async () => {
    window.history.pushState({}, '', '/new-bhe')
    render(<App />)
    expect((await screen.findAllByText('Nueva boleta')).length).toBeGreaterThan(0)
    expect(screen.getByText('Boletas preparadas')).toBeTruthy()
  })

  it('genera detalle global en espanol', () => { expect(defaultGlobalDetail('2026-08')).toContain('AGOSTO 2026') })

  it('renderiza clientes y busqueda', async () => {
    window.history.pushState({}, '', '/clients')
    render(<App />)
    expect(await screen.findByText('Clientes frecuentes')).toBeTruthy()
    expect(screen.getByPlaceholderText('Buscar por nombre o RUT')).toBeTruthy()
  })

  it('renderiza cliente nuevo y formulario', async () => {
    vi.mocked(clientsApi.list).mockResolvedValueOnce([{ id: 1, rut: '999900011', razon_social: 'Receptor Test 005', direccion: 'DIRECCION PRUEBA 002', region: 13, comuna: 'Santiago', activo: true } as any])
    window.history.pushState({}, '', '/clients')
    render(<App />)
    expect((await screen.findAllByText('Receptor Test 005')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: /nuevo cliente/i }))
    expect(screen.getByRole('button', { name: 'Guardar cliente' })).toBeVisible()
  })
})
