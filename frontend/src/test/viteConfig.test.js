import { describe, expect, it } from 'vitest'

// Guards the one line in vite.config.js that keeps `npm test` a function of the
// code rather than of the developer's shell.
//
// Vite reads process.env.DEBUG as a `debug` namespace filter (DEBUG=vite:* is
// how you get its debug logging), and it reads it *while building the config* —
// before `test.env` in the config body would ever be applied. So a shell that
// exports DEBUG as something else changes how the tool configures itself. This
// machine had DEBUG='-LH:*, LH:*:error' (captured reporter output), and the run
// collapsed to worker-startup timeouts: 42 of 173 tests executing, 94s instead
// of 25s.
//
// Deleting it from the real process environment is what fixes it. Setting
// `test.env: { DEBUG: '' }` instead is the natural-looking mistake and it does
// not work — that was tried, and it broke 7 of 10 test files while the comment
// above it claimed the suite was protected.
//
// This test is behavioural rather than a source grep: it plants the hostile
// value, imports the config, and checks the variable is actually gone.

const HOSTILE_DEBUG = '-LH:*, LH:*:error'

describe('vite config environment', () => {
  it('removes a leaked DEBUG from the environment', async () => {
    process.env.DEBUG = HOSTILE_DEBUG

    // Importing the config executes `delete process.env.DEBUG`.
    await import('../../vite.config.js')

    expect(process.env.DEBUG).toBeUndefined()
  })

  it('is idempotent when DEBUG was never set', async () => {
    delete process.env.DEBUG

    await import('../../vite.config.js')

    expect(process.env.DEBUG).toBeUndefined()
  })
})