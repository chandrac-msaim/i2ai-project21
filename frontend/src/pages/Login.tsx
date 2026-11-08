import { useEffect, useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { homeFor, useAuth } from '../auth'
import { Button, Card } from '../components/ui'

const DEMO_PASSWORD = 'demo1234'
const DEMO_LOGINS = [
  { email: 'priya.raman@demo.graydi.local', label: 'Priya Raman', note: 'Student · Spring 2027, has history' },
  { email: 'alex.rivera@demo.graydi.local', label: 'Alex Rivera', note: 'Student · new, no history' },
  { email: 'daniel.okafor@demo.graydi.local', label: 'Daniel Okafor', note: 'Student · low QPA flag' },
  { email: 'morgan.lee@demo.graydi.local', label: 'Dr. Morgan Lee', note: 'Advisor' },
]

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [devMode, setDevMode] = useState(false) // demo logins only when the server runs with APP_ENV=development

  useEffect(() => {
    api
      .get<{ env?: string }>('/health')
      .then((h) => setDevMode(h.env === 'development'))
      .catch(() => setDevMode(false))
  }, [])

  if (user) return <Navigate to={homeFor(user)} replace />

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const u = await login(email, password)
      navigate(homeFor(u), { replace: true })
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.status === 401
            ? 'Wrong email or password. Check them and try again.'
            : err.message
          : 'Could not log in',
      )
    } finally {
      setBusy(false)
    }
  }

  const input = 'h-11 w-full rounded-control border border-border bg-card px-3 text-[15px] focus:border-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary'

  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-6 py-10">
      <div className="w-full max-w-[420px]">
        <div className="mb-8 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-control bg-primary text-white">
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M3 17l6-6 4 4 8-8" />
            </svg>
          </span>
          <span className="text-[18px] font-semibold">Gray DI Degree Pathway Advisor</span>
        </div>

        <Card>
          <h1 className="mb-1 text-[32px] font-semibold leading-tight">Log in</h1>
          <p className="mb-6 text-muted">Sign in to plan your degree pathway</p>
          <form onSubmit={submit} className="space-y-4">
            <label className="block">
              <span className="mb-1 block text-[14px] font-medium">Email</span>
              <input className={input} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="block">
              <span className="mb-1 block text-[14px] font-medium">Password</span>
              <input className={input} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            {error && (
              <p role="alert" className="rounded-control bg-danger-bg px-3 py-2 text-[14px] text-danger-text">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? 'Logging in…' : 'Log in'}
            </Button>
          </form>
        </Card>

        {devMode && (
        <Card className="mt-4">
          <h2 className="mb-3 text-[16px] font-semibold">Demo logins</h2>
          <ul className="space-y-2">
            {DEMO_LOGINS.map((d) => (
              <li key={d.email}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between rounded-control border border-border px-3 py-2 text-left hover:bg-page"
                  onClick={() => {
                    setEmail(d.email)
                    setPassword(DEMO_PASSWORD)
                    setError(null)
                  }}
                >
                  <span className="font-medium">{d.label}</span>
                  <span className="text-[13px] text-muted">{d.note}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[13px] text-muted">Click one to fill the form. Password for all demo accounts: {DEMO_PASSWORD}</p>
        </Card>
        )}
      </div>
    </div>
  )
}
