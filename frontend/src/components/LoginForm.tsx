/**
 * Login form shown when the API reports that authentication is required and no
 * session cookie is present.
 *
 * The password is sent once to /api/auth/login and never stored in component
 * state, a cookie readable by JS, or localStorage. The server answers with an
 * HttpOnly session cookie that the browser attaches automatically.
 */
import { useState } from 'react'
import { ApiError, api } from '../api/client'

export function LoginForm({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(username, password)
      // Drop the password from component state as soon as it is no longer needed.
      setPassword('')
      onAuthenticated()
    } catch (err) {
      setPassword('')
      setError(err instanceof ApiError ? err.detail : 'Could not reach the backend.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="login" onSubmit={submit}>
      <h2>Sign in</h2>
      <p className="faint">This workspace is protected. Enter your account to continue.</p>
      <label>
        Username
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          required
        />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="btn primary" type="submit" disabled={busy}>
        {busy ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  )
}
