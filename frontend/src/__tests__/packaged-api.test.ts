import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.resetModules()
})

describe('API del frontend portable', () => {
  it('usa el mismo origen aunque exista una URL de desarrollo en el entorno de build', async () => {
    vi.resetModules()
    vi.stubEnv('DEV', false)
    vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8001')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] })
    vi.stubGlobal('fetch', fetchMock)
    const { request } = await import('../api/client')
    await request('/clients')
    expect(fetchMock).toHaveBeenCalledWith('/clients', expect.anything())
  })

  it('mantiene la URL configurable para desarrollo', async () => {
    vi.resetModules()
    vi.stubEnv('DEV', true)
    vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8123/')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] })
    vi.stubGlobal('fetch', fetchMock)
    const { request } = await import('../api/client')
    await request('/clients')
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8123/clients', expect.anything())
  })
})
