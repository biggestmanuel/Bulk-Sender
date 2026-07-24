import { useState } from 'react'
import { registerUser, loginUser } from '../api'

function Auth({ onAuthSuccess }) {
  const [mode, setMode] = useState('login') // 'login' or 'register'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')

    if (!username.trim() || !password.trim()) {
      setError('Enter both a username and password.')
      return
    }

    setLoading(true)
    try {
      const data = mode === 'login'
        ? await loginUser({ username, password })
        : await registerUser({ username, password, email })

      // Store the token so future requests can use it
      localStorage.setItem('authToken', data.token)
      localStorage.setItem('username', data.username)

      onAuthSuccess(data.token, data.username)
    } catch (err) {
      setError(err.message || 'Something went wrong. Try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 'var(--space-lg)',
    }}>
      <div className="bs-card" style={{ width: '380px' }}>
        <div style={{ marginBottom: 'var(--space-lg)' }}>
          <div className="bs-pace" style={{ marginBottom: 'var(--space-md)' }} aria-hidden="true">
            <span className="bs-pace-tick is-done" />
            <span className="bs-pace-tick is-done" />
            <span className="bs-pace-tick" />
          </div>
          <h1>{mode === 'login' ? 'Log in' : 'Create your account'}</h1>
          <p>
            {mode === 'login'
              ? 'Send at your own pace.'
              : 'Set up an account to start sending.'}
          </p>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 'var(--space-md)' }}>
            <label className="bs-label" htmlFor="username">Username</label>
            <input
              id="username"
              className="bs-input"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="yourname"
              autoComplete="username"
            />
          </div>

          {mode === 'register' && (
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <label className="bs-label" htmlFor="email">Email (optional)</label>
              <input
                id="email"
                className="bs-input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@company.com"
                autoComplete="email"
              />
            </div>
          )}

          <div style={{ marginBottom: 'var(--space-lg)' }}>
            <label className="bs-label" htmlFor="password">Password</label>
            <input
              id="password"
              className="bs-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
          </div>

          {error && (
            <p style={{
              color: 'var(--danger)',
              fontSize: '13px',
              marginBottom: 'var(--space-md)',
            }}>
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="bs-btn bs-btn-primary"
            style={{ width: '100%', marginBottom: 'var(--space-md)' }}
          >
            {loading ? 'Just a moment…' : (mode === 'login' ? 'Log in' : 'Create account')}
          </button>
        </form>

        <p style={{ textAlign: 'center', fontSize: '13px' }}>
          {mode === 'login' ? (
            <>Don't have an account?{' '}
              <button
                onClick={() => setMode('register')}
                style={{ border: 'none', background: 'none', color: 'var(--accent)', cursor: 'pointer', fontWeight: 500, padding: 0, fontSize: '13px' }}
              >
                Sign up
              </button>
            </>
          ) : (
            <>Already have an account?{' '}
              <button
                onClick={() => setMode('login')}
                style={{ border: 'none', background: 'none', color: 'var(--accent)', cursor: 'pointer', fontWeight: 500, padding: 0, fontSize: '13px' }}
              >
                Log in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  )
}

export default Auth
