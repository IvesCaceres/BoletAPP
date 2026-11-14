import { describe, expect, it, vi } from 'vitest'
import { base64ToPdfBlobUrl, openPdf } from '../api/pdf'

describe('PDF', () => {
  it('convierte base64 en Blob URL PDF', () => { const url = base64ToPdfBlobUrl(btoa('%PDF-test')); expect(URL.createObjectURL).toHaveBeenCalled(); expect(url).toContain('blob:') })
  it('abre PDF sin escribir base64 en almacenamiento', () => { const open = vi.spyOn(window, 'open').mockImplementation(() => null); openPdf(btoa('%PDF-test')); expect(open).toHaveBeenCalledWith(expect.stringContaining('blob:'), '_blank', 'noopener,noreferrer'); open.mockRestore() })
})
