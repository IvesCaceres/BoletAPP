import { vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
Object.defineProperty(URL, 'createObjectURL', { writable: true, value: vi.fn(() => 'blob:test') })
