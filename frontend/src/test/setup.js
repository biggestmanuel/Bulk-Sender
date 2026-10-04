import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup, configure } from '@testing-library/react'

// The default 1s async timeout is too tight when vitest runs test files in
// parallel and the machine is loaded: findBy* assertions that depend on an
// async render (a parsed CSV, a polling tick) would expire even though the
// behaviour is correct. This is a timeout for a slow machine, not a way to
// hide logic errors — a real failure still fails, just with more room.
configure({ asyncUtilTimeout: 5000 })

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