import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// jsdom implements neither of these, and several components rely on them.
if (!globalThis.matchMedia) {
  globalThis.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}

if (!globalThis.scrollTo) {
  globalThis.scrollTo = () => {}
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.restoreAllMocks()
})