import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'

const testClock = vi.hoisted(() => {
  const NativeDate = Date
  const initialDate = new NativeDate('2026-09-15T12:00:00-03:00')
  const set = (value: Date) => {
    class ControlledDate extends NativeDate {
      constructor(...args: any[]) {
        super(args.length === 0 ? value.getTime() : args[0])
      }

      static now() {
        return value.getTime()
      }
    }
    vi.stubGlobal('Date', ControlledDate)
  }
  set(initialDate)
  return { NativeDate, initialDate, set }
})

afterAll(() => {
  vi.unstubAllGlobals()
})

const clients = vi.hoisted(() => [
  { id: 1, rut: '999900011', razon_social: 'EMPRESA DEMO SPA', nombre_fantasia: 'CLINICA PRUEBA', direccion: 'A', region: 10, comuna: 'Puerto Varas', activo: true, detalle_default: null },
  { id: 2, rut: '999900119', razon_social: 'Receptor Test 004', nombre_fantasia: null, direccion: 'B', region: 13, comuna: 'Santiago', activo: true, detalle_default: 'Detalle por defecto' },
])
const draft = (items: any[] = []) => ({ id: 7, period: '2026-09', status: 'draft', fecha_emision: '2026-09-30', detalle_global: 'SERVICIOS ECOGRAFÍA MES SEPTIEMBRE 2026', items })
const batchesApi = vi.hoisted(() => ({
  list: vi.fn().mockResolvedValue([]),
  get: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  applyGlobalDetail: vi.fn(),
  addItem: vi.fn().mockResolvedValue({}),
  updateItem: vi.fn(), removeItem: vi.fn(), prepare: vi.fn(), emit: vi.fn(), retryFailed: vi.fn(), retryItem: vi.fn(),
}))

vi.mock('../api/clients', () => ({ clientsApi: { list: vi.fn().mockResolvedValue(clients), create: vi.fn(), update: vi.fn(), deactivate: vi.fn() } }))
vi.mock('../api/batches', () => ({ batchesApi }))
vi.mock('../api/history', () => ({ historyApi: { list: vi.fn().mockResolvedValue([]), get: vi.fn() } }))

describe('detalle global mensual', () => {
  it('creates a new batch without changing a completed historical batch', async () => {
    const historical = { id: 1, period: '2026-09', status: 'completed_with_errors', provider: 'simplefactura', fecha_emision: '2026-09-30', detalle_global: 'Historical batch', items: [{ id: 21, razon_social: 'RECEPTOR TEST 018', rut_receptor: '1-9', detalle: 'Previous', monto: 100, status: 'failed' }] }
    const fresh = { id: 2, period: '2026-09', status: 'draft', provider: 'sii', fecha_emision: '2026-09-15', detalle_global: 'New batch', items: [] }
    batchesApi.list.mockResolvedValue([historical])
    batchesApi.get.mockResolvedValue(historical)
    batchesApi.create.mockResolvedValueOnce(fresh)
    render(<App />)
    await waitFor(() => expect(batchesApi.get).toHaveBeenCalledWith(1))
    expect(screen.getByDisplayValue('Historical batch')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /nuevo lote/i }))
    await waitFor(() => expect(batchesApi.create).toHaveBeenCalled())
    expect(batchesApi.create).toHaveBeenCalledWith(expect.any(String), expect.any(String), expect.any(String), 'issuer_handles_ppm')
    expect(screen.getByText('Clientes disponibles')).toBeTruthy()
    expect(screen.queryByText('RECEPTOR TEST 018')).toBeNull()
  })

  it('shows persisted folios in added clients immediately after emission without a result block', async () => {
    const ready = { id: 4, period: '2026-09', status: 'ready', provider: 'sii', fecha_emision: '2026-09-15', items: [] }
    const completed = { id: 4, period: '2026-09', status: 'completed_with_errors', provider: 'sii', fecha_emision: '2026-09-15', items: [{ id: 41, razon_social: 'RECEPTOR PRUEBA 05', rut_receptor: '1-9', detalle: 'S', monto: 100, status: 'success', bhe_emission_id: 90, folio: 1001, final_click_occurred: true }, { id: 42, razon_social: 'RECEPTOR PRUEBA 03', rut_receptor: '2-7', detalle: 'S', monto: 100, status: 'failed', bhe_emission_id: 91, error_message: 'SiiValidationError at stage preview_reached', final_click_occurred: false }] }
    batchesApi.list.mockResolvedValue([ready])
    batchesApi.get.mockResolvedValueOnce(ready).mockResolvedValueOnce(completed)
    batchesApi.emit.mockResolvedValueOnce({ batch_id: 4, status: 'processing', provider: 'sii', total: 2, success: 0, failed: 0, unknown: 0, pending: 2, processing: 0, results: [] })
    render(<App />)
    await waitFor(() => expect(batchesApi.get).toHaveBeenCalledWith(4))
    fireEvent.click(screen.getByRole('button', { name: /emitir 0 bhe en sii/i }))
    fireEvent.click(screen.getByRole('button', { name: /confirmar emisión de 0 bhe en sii/i }))
    await waitFor(() => expect(screen.getByText('1001')).toBeTruthy())
    expect(screen.getByRole('columnheader', { name: 'Folio' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Resultado' })).toBeNull()
    expect(screen.getByText('No se pudo validar la vista previa en SII.')).toBeTruthy()
  })

  it('offers a row retry and targets only that item', async () => {
    const failed = { id: 6, period: '2026-09', status: 'completed_with_errors', provider: 'sii', fecha_emision: '2026-09-15', items: [
      { id: 11, razon_social: 'Receptor Test 019', rut_receptor: '1-9', detalle: 'S', monto: 100, status: 'failed', final_click_occurred: false, bhe_emission_id: 18, retryable: true },
      { id: 12, razon_social: 'Receptor Test 020', rut_receptor: '2-7', detalle: 'S', monto: 100, status: 'failed', final_click_occurred: false, bhe_emission_id: 19, retryable: true },
    ] }
    batchesApi.list.mockResolvedValue([failed])
    batchesApi.get.mockResolvedValue(failed)
    let finishRetry!: (value: unknown) => void
    batchesApi.retryItem.mockReturnValueOnce(new Promise((resolve) => { finishRetry = resolve }))
    render(<App />)
    await waitFor(() => expect(batchesApi.get).toHaveBeenCalledWith(6))
    vi.useFakeTimers()
    const retryButtons = screen.getAllByRole('button', { name: 'Reintentar' })
    fireEvent.click(retryButtons[0])
    expect(screen.getByText(/únicamente esta boleta/i)).toBeTruthy()
    expect(batchesApi.retryItem).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /confirmar retry de 1 bhe en sii/i }))
    expect(batchesApi.retryItem).toHaveBeenCalledWith(6, 11)
    await act(async () => { vi.advanceTimersByTime(1500); await Promise.resolve() })
    expect(batchesApi.get).toHaveBeenCalledTimes(2)
    await act(async () => { finishRetry({}); await Promise.resolve() })
    vi.useRealTimers()
    expect(batchesApi.retryItem).toHaveBeenCalledTimes(1)
  })

  it('hides individual retry for success, unknown, final click, folio, and server-ineligible failures', async () => {
    const guarded = { id: 6, period: '2026-09', status: 'completed_with_errors', provider: 'sii', fecha_emision: '2026-09-15', items: [
      { id: 21, razon_social: 'RECEPTOR PRUEBA 05', rut_receptor: '1-9', detalle: 'S', monto: 100, status: 'success', final_click_occurred: true, folio: 1002, retryable: false },
      { id: 22, razon_social: 'RECEPTOR PRUEBA 07', rut_receptor: '2-7', detalle: 'S', monto: 100, status: 'emission_status_unknown', retryable: false },
      { id: 23, razon_social: 'RECEPTOR PRUEBA 01', rut_receptor: '3-5', detalle: 'S', monto: 100, status: 'failed', final_click_occurred: true, retryable: false },
      { id: 24, razon_social: 'Folio', rut_receptor: '4-3', detalle: 'S', monto: 100, status: 'failed', folio: 1003, retryable: false },
      { id: 25, razon_social: 'RECEPTOR TEST 021', rut_receptor: '5-1', detalle: 'S', monto: 100, status: 'failed', retryable: false },
    ] }
    batchesApi.list.mockResolvedValue([guarded])
    batchesApi.get.mockResolvedValue(guarded)
    render(<App />)
    await waitFor(() => expect(batchesApi.get).toHaveBeenCalledWith(6))
    expect(screen.queryByRole('button', { name: 'Reintentar' })).toBeNull()
  })

  it('asks before leaving a ready batch', async () => {
    const ready = { id: 3, period: '2026-09', status: 'ready', provider: 'sii', fecha_emision: '2026-09-15', items: [] }
    batchesApi.list.mockResolvedValue([ready])
    batchesApi.get.mockResolvedValue(ready)
    render(<App />)
    await waitFor(() => expect(batchesApi.get).toHaveBeenCalledWith(3))
    fireEvent.click(screen.getByRole('button', { name: /nuevo lote/i }))
    expect(screen.getByText(/preparado pero a/)).toBeTruthy()
    expect(batchesApi.create).not.toHaveBeenCalled()
  })

  beforeEach(() => {
    testClock.set(testClock.initialDate)
    vi.clearAllMocks()
    window.history.pushState({}, '', '/')
    batchesApi.list.mockResolvedValue([])
    batchesApi.create.mockResolvedValue(draft())
    batchesApi.get.mockResolvedValue(draft())
    batchesApi.update.mockResolvedValue(draft())
    batchesApi.applyGlobalDetail.mockResolvedValue(draft())
  })

  async function openDraft() {
    render(<App />)
    await waitFor(() => expect(batchesApi.list).toHaveBeenCalled())
    await screen.findByText('Comienza el proceso mensual')
    await new Promise(resolve => setTimeout(resolve, 20))
    fireEvent.click(await screen.findByText('Crear batch draft'))
    expect(await screen.findByText('Clientes disponibles')).toBeTruthy()
  }

  it('muestra y actualiza el preview disponible sin guardar', async () => {
    await openDraft()
    expect(screen.getAllByText('Detalle: SERVICIOS ECOGRAFÍA MES SEPTIEMBRE 2026').length).toBe(2)
    const input = screen.getByDisplayValue('SERVICIOS ECOGRAFÍA MES SEPTIEMBRE 2026')
    fireEvent.change(input, { target: { value: 'SERVICIOS ECOGRAFÍA SEPTIEMBRE 2026' } })
    expect(screen.getAllByText('Detalle: SERVICIOS ECOGRAFÍA SEPTIEMBRE 2026').length).toBe(2)
    expect(batchesApi.update).not.toHaveBeenCalled()
  })

  it('muestra dirty state y feedback al guardar cambios', async () => {
    await openDraft()
    fireEvent.change(screen.getByDisplayValue('SERVICIOS ECOGRAFÍA MES SEPTIEMBRE 2026'), { target: { value: 'Detalle nuevo' } })
    expect(screen.getByText('Cambios sin guardar')).toBeTruthy()
    fireEvent.click(screen.getByText('Guardar cambios'))
    await waitFor(() => expect(batchesApi.update).toHaveBeenCalledTimes(1))
    expect(screen.getByText('✓ Guardado')).toBeTruthy()
  })

  it('usa el detalle actual como snapshot al agregar y no reescribe el item al cambiar global', async () => {
    await openDraft()
    fireEvent.change(screen.getByDisplayValue('SERVICIOS ECOGRAFÍA MES SEPTIEMBRE 2026'), { target: { value: 'Detalle snapshot' } })
    fireEvent.click(screen.getByRole('button', { name: 'Agregar CLINICA PRUEBA' }))
    fireEvent.change(screen.getByPlaceholderText('Monto CLP'), { target: { value: '100000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Agregar 1 clientes al lote' }))
    await waitFor(() => expect(batchesApi.addItem).toHaveBeenCalledWith(7, 1, 100000, 'Detalle snapshot'))
    fireEvent.change(screen.getByDisplayValue('Detalle snapshot'), { target: { value: 'Detalle global nuevo' } })
    expect(batchesApi.updateItem).not.toHaveBeenCalled()
  })

  it('mantiene agregados como resumen y permite editarlos de forma contextual', async () => {
    const item = { id: 11, client_id: 1, razon_social: 'EMPRESA DEMO SPA', rut_receptor: '999900011', detalle: 'Anterior', monto: 100000, status: 'draft' }
    batchesApi.list.mockResolvedValue([draft([item])])
    batchesApi.get.mockResolvedValue(draft([item]))
    render(<App />)
    await waitFor(() => expect(batchesApi.get).toHaveBeenCalledWith(7))
    expect(screen.queryByText('Aplicar detalle global a agregados')).toBeNull()
    expect(screen.getByText('Anterior')).toBeTruthy()
    expect(screen.queryByText('999900011')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Editar item' }))
    fireEvent.change(screen.getByDisplayValue('Anterior'), { target: { value: 'Nuevo detalle' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Guardar cambios' })[1])
    await waitFor(() => expect(batchesApi.updateItem).toHaveBeenCalledWith(7, 11, 100000, 'Nuevo detalle', null))
  })

  it('usa detalle_default solo cuando el detalle global está vacío', async () => {
    await openDraft()
    fireEvent.change(screen.getByDisplayValue('SERVICIOS ECOGRAFÍA MES SEPTIEMBRE 2026'), { target: { value: '' } })
    expect(screen.getAllByText('Detalle: Detalle por defecto').length).toBeGreaterThan(0)
  })

  it('mantiene las transiciones disponibles, preparación y agregados sin duplicación', async () => {
    await openDraft()
    const clientA = screen.getByRole('button', { name: 'Agregar CLINICA PRUEBA' })
    fireEvent.click(clientA)
    expect(screen.queryByRole('button', { name: 'Agregar CLINICA PRUEBA' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Agregar Receptor Test 004' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Quitar de selección' }))
    expect(screen.getByRole('button', { name: 'Agregar CLINICA PRUEBA' })).toBeTruthy()

    batchesApi.get.mockResolvedValueOnce(draft([{ id: 31, client_id: 1, razon_social: 'EMPRESA DEMO SPA', detalle: 'Detalle', monto: 100000, status: 'draft' }]))
    fireEvent.click(screen.getByRole('button', { name: 'Agregar CLINICA PRUEBA' }))
    fireEvent.change(screen.getByPlaceholderText('Monto CLP'), { target: { value: '100000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Agregar 1 clientes al lote' }))
    await waitFor(() => expect(screen.getAllByText('Detalle').length).toBeGreaterThan(0))
    expect(screen.queryByRole('button', { name: 'Agregar CLINICA PRUEBA' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Quitar de selección' })).toBeNull()
    batchesApi.get.mockResolvedValueOnce(draft([]))
    fireEvent.click(screen.getByRole('button', { name: 'Eliminar del lote' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Agregar CLINICA PRUEBA' })).toBeTruthy())
  })

  it('bloquea clientes sin detalle cuando no existe configuración global', async () => {
    await openDraft()
    fireEvent.change(screen.getByDisplayValue('SERVICIOS ECOGRAFÍA MES SEPTIEMBRE 2026'), { target: { value: '' } })
    const incomplete = screen.getByRole('button', { name: 'Agregar CLINICA PRUEBA' })
    expect(incomplete).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('Faltan datos')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Agregar Receptor Test 004' })).not.toBeDisabled()
  })

  it('la búsqueda filtra los clientes que siguen disponibles', async () => {
    await openDraft()
    fireEvent.change(screen.getByRole('textbox', { name: 'Buscar clientes' }), { target: { value: 'Receptor Test 004' } })
    expect(screen.getByRole('button', { name: 'Agregar Receptor Test 004' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Agregar CLINICA PRUEBA' })).toBeNull()
  })

  it('distingue período de servicios y fecha tributaria', async () => {
    await openDraft()
    expect(screen.getByText('Mes al que corresponde este lote. No modifica la fecha tributaria de las boletas.')).toBeTruthy()
    expect(screen.getByText('Fecha que se utilizará al emitir las boletas en el SII.')).toBeTruthy()
    const date = screen.getByLabelText(/Fecha de emisión/) as HTMLInputElement
    const originalDate = date.value
    fireEvent.change(screen.getByDisplayValue('2026-09'), { target: { value: '2026-10' } })
    expect(date.value).toBe(originalDate)
  })

  it('muestra información adicional del cliente mediante focus fuera del contenedor', async () => {
    await openDraft()
    const info = screen.getByRole('button', { name: 'Información de CLINICA PRUEBA' })
    fireEvent.focus(info)
    const tooltip = await screen.findByRole('tooltip')
    expect(tooltip.parentElement).toBe(document.body)
    expect(tooltip.textContent).toContain('RUT: 99.990.001-1')
    expect(tooltip.textContent).toContain('Puerto Varas')
  })

  it('hover mantiene visible el popover sin filtrar el RUT en logs', async () => {
    const spies = [vi.spyOn(console, 'log'), vi.spyOn(console, 'info'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'error')]
    try {
      await openDraft()
      expect(screen.queryByText('999900011')).toBeNull()
      expect(screen.queryByText(/RUT:/)).toBeNull()
      const info = screen.getAllByRole('button').find((button) => button.classList.contains('client-info'))!
      fireEvent.mouseEnter(info.parentElement!)
      const tooltip = await screen.findByRole('tooltip')
      expect(tooltip.textContent).toContain('RUT: 99.990.001-1')
      expect(spies.flatMap((spy) => spy.mock.calls.flat())).not.toContain('999900011')
    } finally {
      spies.forEach((spy) => spy.mockRestore())
    }
  })

  it('calcula el periodo y detalle iniciales al cruzar el cambio de ano', async () => {
    vi.resetModules()
    testClock.set(new testClock.NativeDate('2027-01-15T12:00:00-03:00'))
    const { default: AppAtYearChange } = await import('../App')
    batchesApi.list.mockResolvedValue([])

    render(<AppAtYearChange />)

    await waitFor(() => expect(batchesApi.list).toHaveBeenCalled())
    expect(screen.getByDisplayValue('2027-01')).toBeTruthy()
    expect(screen.getByDisplayValue(/MES ENERO 2027/)).toBeTruthy()

    testClock.set(testClock.initialDate)
  })

  it.skip('muestra el popover al pasar el mouse y no imprime el RUT completo en logs', async () => {
    const spies = [vi.spyOn(console, 'log'), vi.spyOn(console, 'info'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'error')]
    try {
      await openDraft()
    expect(screen.queryByText('999900011')).toBeNull()
    expect(screen.queryByText(/RUT:/)).toBeNull()
    const info = screen.getByRole('button', { name: 'Información de CLINICA PRUEBA' })
      fireEvent.mouseEnter(info.parentElement!)
      const tooltip = await screen.findByRole('tooltip')
      expect(tooltip.textContent).toContain('RUT: 99.990.001-1')
      expect(spies.flatMap((spy) => spy.mock.calls.flat())).not.toContain('999900011')
    } finally {
      spies.forEach((spy) => spy.mockRestore())
    }
  })
})
