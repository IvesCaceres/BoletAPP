import { describe, expect, it } from 'vitest'
import { manualDateBounds, manualDateError } from '../manualDate'

describe('fechas de emisión manuales según el calendario local', () => {
  const localToday = new Date(2026, 10, 6, 12)

  it('admite hoy y exactamente 90 días atrás, pero bloquea 91 días y el futuro', () => {
    expect(manualDateBounds(localToday)).toEqual({ min: '2026-08-08', max: '2026-11-06' })
    for (const day of ['2026-11-06', '2026-08-09', '2026-08-08']) {
      expect(manualDateError(day, localToday)).toBeNull()
    }
    expect(manualDateError('2026-08-07', localToday)).toContain('90 días')
    expect(manualDateError('2026-11-07', localToday)).toContain('futura')
    expect(manualDateError('2026-09-31', localToday)).toContain('válida')
  })
})
