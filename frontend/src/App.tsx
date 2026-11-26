import { type FormEvent, lazy, Suspense, useEffect, useState } from 'react'
import { api, getToken, type LiveEvent, setToken, socketUrl, type User } from './api'
import { Agents } from './pages/Agents'
import { Approvals } from './pages/Approvals'
import { Activity, Audit, Jobs } from './pages/History'
import { Monitor } from './pages/Monitor'
import { Servers } from './pages/Servers'
import { Settings } from './pages/Settings'
import { Alert, Field, useAction } from './ui'

const Files = lazy(() => import('./pages/Files').then((m) => ({ default: m.Files })))
const TerminalPage = lazy(() => import('./pages/Terminal').then((m) => ({ default: m.TerminalPage })))

const PAGES = [
  ['monitor', 'پایش سرور'], ['servers', 'سرورها'], ['agents', 'دستیارها و دسترسی'], ['approvals', 'تأیید عملیات'],
  ['activity', 'فعالیت زنده'], ['terminal', 'ترمینال'], ['files', 'فایل‌ها و ویرایشگر'], ['jobs', 'دستورها و نشست‌ها'],
  ['audit', 'گزارش ممیزی'], ['settings', 'تنظیمات و امنیت'],
] as const

function useRoute(): string {
  const [hash, setHash] = useState(location.hash)
  useEffect(() => {
    const update = () => setHash(location.hash)
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  const page = hash.replace('#/', '')
  return PAGES.some(([id]) => id === page) ? page : 'monitor'
}

/** Live events over WebSocket, reconnecting with backoff. */
function useEvents(signedIn: boolean) {
  const [events, setEvents] = useState<LiveEvent[]>([])
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    if (!signedIn) return
    let socket: WebSocket, timer: number, closed = false, delay = 1000
    const connect = () => {
      socket = new WebSocket(socketUrl('/api/ws/events'))
      socket.onopen = () => socket.send(JSON.stringify({ token: getToken() }))
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as LiveEvent
        if (event.type === 'ready') { setConnected(true); delay = 1000; return }
        setEvents((previous) => [event, ...previous].slice(0, 400))
      }
      socket.onclose = (close) => {
        setConnected(false)
        if (closed) return
        if (close.code === 4401) { setToken(''); window.dispatchEvent(new Event('atb-signed-out')); return }
        timer = window.setTimeout(connect, delay)
        delay = Math.min(delay * 2, 15000)
      }
    }
    connect()
    return () => { closed = true; clearTimeout(timer); socket.close(); setEvents([]) }
  }, [signedIn])
  return { events, connected }
}

function useTheme() {
  const [theme, setTheme] = useState(() => {
    try { const saved = localStorage.getItem('atb.theme'); if (saved) return saved } catch { /* ignore */ }
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('atb.theme', theme) } catch { /* ignore */ }
  }, [theme])
  return [theme, () => setTheme(theme === 'dark' ? 'light' : 'dark')] as const
}

function Login({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const { busy, error, run } = useAction()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      const result = await api<{ token: string; user: User }>('/api/auth/login', 'POST', { username, password })
      setToken(result.token)
      onSignedIn(result.user)
    })
  }
  return (
    <main className="login">
      <form className="card" onSubmit={submit}>
        <div className="brand"><span className="logo">&gt;_</span><div><strong>AI Terminal Bridge</strong><small>پل امن میان دستیارهای هوش مصنوعی و سرورها</small></div></div>
        <Field label="نام کاربری"><input dir="ltr" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required /></Field>
        <Field label="گذرواژه"><input dir="ltr" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus /></Field>
        <Alert text={error} />
        <button disabled={busy}>ورود</button>
      </form>
    </main>
  )
}

export function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(Boolean(getToken()))
  const [stopped, setStopped] = useState(false)
  const [pending, setPending] = useState(0)
  const route = useRoute()
  const [theme, toggleTheme] = useTheme()
  const { events, connected } = useEvents(Boolean(user))
  const { error, run } = useAction()
  const approvalMarker = events.find((e) => e.type.startsWith('approval'))?.ts
  const stopMarker = events.find((e) => e.type === 'kill_switch')?.ts

  useEffect(() => {
    if (getToken()) api<User>('/api/auth/me').then(setUser).catch(() => setToken('')).finally(() => setChecking(false))
    const signOut = () => setUser(null)
    window.addEventListener('atb-signed-out', signOut)
    return () => window.removeEventListener('atb-signed-out', signOut)
  }, [])

  useEffect(() => {
    if (!user) return
    const refresh = () => {
      api<unknown[]>('/api/approvals?status=pending').then((list) => setPending(list.length)).catch(() => {})
      api<{ enabled: boolean }>('/api/settings/kill-switch').then((s) => setStopped(s.enabled)).catch(() => {})
    }
    refresh()
    const timer = setInterval(refresh, 10000)
    return () => clearInterval(timer)
  }, [user, approvalMarker, stopMarker, route])

  if (checking) return null
  if (!user) return <Login onSignedIn={setUser} />

  const toggleStop = () => {
    const question = stopped ? 'فعالیت دستیارها دوباره مجاز شود؟' : 'همه‌ی فعالیت دستیارها متوقف و دستورهای در حال اجرا قطع شود؟'
    if (!confirm(question)) return
    void run(async () => {
      const result = await api<{ enabled: boolean; not_stopped: string[] }>('/api/settings/kill-switch', 'PUT', { enabled: !stopped })
      setStopped(result.enabled)
      if (result.not_stopped.length) throw new Error(`این دستورها متوقف نشدند: ${result.not_stopped.join(', ')}`)
    })
  }

  const pages: Record<string, () => React.ReactNode> = {
    monitor: () => <Monitor />, servers: () => <Servers />, agents: () => <Agents />,
    approvals: () => <Approvals refreshKey={approvalMarker} />, activity: () => <Activity events={events} connected={connected} />,
    terminal: () => <TerminalPage />, files: () => <Files />, jobs: () => <Jobs events={events} />,
    audit: () => <Audit user={user} />, settings: () => <Settings user={user} onSignOut={() => setUser(null)} />,
  }

  return (
    <div className="shell">
      <nav className="side">
        <div className="brand"><span className="logo">&gt;_</span><strong>Terminal Bridge</strong></div>
        {PAGES.map(([id, label]) => (
          <a key={id} href={`#/${id}`} className={route === id ? 'active' : ''}>
            {label}{id === 'approvals' && pending > 0 && <span className="count">{pending}</span>}
          </a>
        ))}
      </nav>
      <div className="content">
        <header className="top">
          <span className={`dot ${connected ? 'on' : ''}`} title={connected ? 'متصل' : 'قطع'} />
          <span className="grow">{user.username}</span>
          <button className="ghost" onClick={toggleTheme}>{theme === 'dark' ? 'تم روشن' : 'تم تاریک'}</button>
          {user.role === 'admin' && <button className={stopped ? '' : 'danger'} onClick={toggleStop}>{stopped ? 'ازسرگیری دستیارها' : '■ توقف اضطراری'}</button>}
        </header>
        {stopped && <div className="alert danger banner">توقف اضطراری فعال است؛ هیچ دستیاری نمی‌تواند ابزاری اجرا کند.</div>}
        <Alert text={error} />
        <Suspense fallback={<p className="empty">در حال بارگذاری…</p>}>{pages[route]()}</Suspense>
      </div>
    </div>
  )
}
