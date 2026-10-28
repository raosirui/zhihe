import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import { setToken } from '../lib'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }))
beforeEach(() => { localStorage.clear(); setToken(); vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('Unexpected fetch in test')))) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })
