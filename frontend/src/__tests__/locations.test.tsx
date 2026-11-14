import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { getCommunesByRegion, getRegionName, getRegions } from '../data/chileLocations'
import { clientsApi } from '../api/clients'
import { batchesApi } from '../api/batches'

vi.mock('../api/clients', () => ({ clientsApi: { list: vi.fn().mockResolvedValue([]), create: vi.fn(), update: vi.fn(), deactivate: vi.fn() } }))
vi.mock('../api/batches', () => ({ batchesApi: { list: vi.fn().mockResolvedValue([]), get: vi.fn(), create: vi.fn(), createManual: vi.fn(), addManualItem: vi.fn(), updateManualItem: vi.fn(), update: vi.fn(), applyGlobalDetail: vi.fn(), addItem: vi.fn(), updateItem: vi.fn(), removeItem: vi.fn(), prepare: vi.fn(), emit: vi.fn(), retryFailed: vi.fn() } }))
vi.mock('../api/history', () => ({ historyApi: { list: vi.fn().mockResolvedValue([]), get: vi.fn() } }))
vi.mock('../api/bhe', () => ({ emitBhe: vi.fn().mockResolvedValue({ success: false, status: 'failed', message: 'offline test' }) }))

describe('catalogo de ubicaciones chilenas', () => {
  it('contiene las 16 regiones y conserva el codigo de Los Lagos', () => {
    expect(getRegions()).toHaveLength(16)
    expect(getRegionName(10)).toBe('Los Lagos')
  })
  it('expone comunas validas por region', () => {
    expect(getCommunesByRegion(10)).toContain('Puerto Varas')
    expect(getCommunesByRegion(13)).toContain('Santiago')
  })
})

describe('selectores dependientes en Nueva boleta', () => {
  it('deshabilita Comuna sin Region y limpia una seleccion incompatible', () => {
    window.history.pushState({}, '', '/new-bhe')
    render(<App />)
    const region = screen.getByLabelText(/Regi/) as HTMLSelectElement
    const comuna = screen.getByLabelText('Comuna') as HTMLSelectElement
    fireEvent.change(region, { target: { value: '' } })
    expect(comuna.disabled).toBe(true)
    fireEvent.change(region, { target: { value: '10' } })
    expect(comuna.disabled).toBe(false)
    fireEvent.change(comuna, { target: { value: 'Puerto Varas' } })
    fireEvent.change(region, { target: { value: '13' } })
    expect(comuna.value).toBe('')
  })

  it('conserva el contrato numerico al enviar Los Lagos a la API', async () => {
    window.history.pushState({}, '', '/new-bhe')
    vi.mocked(batchesApi.createManual).mockResolvedValueOnce({ id: 4, period: '2026-11', source: 'manual', status: 'ready', fecha_emision: '2026-11-05', provider: 'sii', items: [{ id: 1, client_id: null, rut_receptor: '999900011', razon_social: 'Receptor', direccion: 'DIRECCION PRUEBA 002', region: 10, comuna: 'Puerto Varas', detalle: [{ descripcion: 'Servicio' }], monto: 100000, fecha_emision: '2026-11-05', status: 'pending', retention_mode_override: 'issuer_handles_ppm' }] } as any)
    render(<App />)
    fireEvent.change(screen.getByLabelText('RUT receptor'), { target: { value: '99990001-1' } })
    fireEvent.click(screen.getByRole('button', { name: /Personalizar esta boleta/ }))
    fireEvent.change(screen.getByLabelText(/Nombre \/ raz/), { target: { value: 'Receptor' } })
    fireEvent.change(screen.getAllByLabelText(/Direcci/)[1], { target: { value: 'DIRECCION PRUEBA 002' } })
    fireEvent.change(screen.getAllByLabelText(/Regi/)[1], { target: { value: '10' } })
    fireEvent.change(screen.getAllByLabelText('Comuna')[1], { target: { value: 'Puerto Varas' } })
    fireEvent.change(screen.getAllByLabelText('Detalle')[1], { target: { value: 'Servicio' } })
    fireEvent.change(screen.getByLabelText('Monto CLP'), { target: { value: '100000' } })
    await waitFor(() => expect(screen.getByRole('button', { name: /Agregar boleta/ })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: /Agregar boleta/ }))
    expect(await screen.findByText('Boletas preparadas')).toBeTruthy()
  })
})
