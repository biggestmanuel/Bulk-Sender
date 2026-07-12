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
      setError('Please fill in both username and password')
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
      setError(err.message || 'Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: '360px', margin: '80px auto', padding: '30px', border: '1px solid lightgray', borderRadius: '8px' }}>
      <h2 style={{ textAlign: 'center' }}>
        {mode === 'login' ? 'Log In' : 'Create an Account'}
      </h2>

      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: '12px' }}>
          <label style={{ display: 'block', marginBottom: '4px' }}>Username</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            style={{ width: '100%', padding: '8px', boxSizing: 'border-box' }}
          />
        </div>

        {mode === 'register' && (
          <div style={{ marginBottom: '12px' }}>
            <label style={{ display: 'block', marginBottom: '4px' }}>Email (optional)</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{ width: '100%', padding: '8px', boxSizing: 'border-box' }}
            />
          </div>
        )}

        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', marginBottom: '4px' }}>Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ width: '100%', padding: '8px', boxSizing: 'border-box' }}
          />
        </div>

        {error && <p style={{ color: 'red', fontSize: '14px' }}>{error}</p>}

        <button type="submit" disabled={loading} style={{ width: '100%', padding: '10px', marginBottom: '10px' }}>
          {loading ? 'Please wait...' : (mode === 'login' ? 'Log In' : 'Create Account')}
        </button>
      </form>

      <p style={{ textAlign: 'center', fontSize: '14px' }}>
        {mode === 'login' ? (
          <>Don't have an account?{' '}
            <button onClick={() => setMode('register')} style={{ border: 'none', background: 'none', color: 'blue', cursor: 'pointer', textDecoration: 'underline' }}>
              Sign up
            </button>
          </>
        ) : (
          <>Already have an account?{' '}
            <button onClick={() => setMode('login')} style={{ border: 'none', background: 'none', color: 'blue', cursor: 'pointer', textDecoration: 'underline' }}>
              Log in
            </button>
          </>
        )}
      </p>
    </div>
  )
}

export default Auth
