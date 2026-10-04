import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite reads process.env.DEBUG as a `debug` namespace filter (DEBUG=vite:* is
// how you turn on its debug logging), and it does so while building this
// config — before any test runs, and before `test.env` in the block below
// would apply. A shell that exports DEBUG as something else therefore changes
// how the tool configures itself: the run collapses to worker-startup
// timeouts and only a fraction of the suite executes. This machine had
// DEBUG='-LH:*, LH:*:error', which is captured reporter output, not a boolean.
//
// Cleared here, before defineConfig() is evaluated, so `npm test` is a
// function of the code rather than of the developer's shell. The backend has
// the same hazard and handles it in backend/config/settings_test.py.
delete process.env.DEBUG

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Proxies /api to the Django dev server so the browser talks to a single
    // origin in development.
    //
    // This matters because of CORS: the app is token-authenticated, but a
    // cross-origin request from :5173 to :8000 still needs the backend's
    // CORS_ALLOWED_ORIGINS to list the exact frontend origin. Proxying
    // removes that moving part from local development entirely. It does
    // change the meaning of the default API base URL, so it is opt-in via
    // VITE_API_BASE_URL being unset — a configured base URL still wins and
    // talks to the backend directly, which is what production does.
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    // The React plugin already handles JSX; vitest needs this to transform
    // it in the test environment too.
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],
    css: false,
    // Vitest's own default is 5s per test. With ten files running in
    // parallel, a loaded machine can exceed that on tests that legitimately
    // wait on several renders, and the suite became flaky for reasons that
    // had nothing to do with the code under test.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
})