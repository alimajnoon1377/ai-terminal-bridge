import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'

export const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error))

/** Load data, optionally refreshing on an interval. Stale responses are ignored. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[], intervalMs = 0) {
  const [data, setData] = useState<T | undefined>()
  const [error, setError] = useState('')
  const generation = useRef(0)
  const busy = useRef(false)
  const reload = useCallback(async () => {
    const mine = ++generation.current
    busy.current = true
    try {
      const result = await load()
      if (mine === generation.current) { setData(result); setError('') }
    } catch (e) {
      if (mine === generation.current) setError(errorText(e))
    } finally {
      if (mine === generation.current) busy.current = false
    }
    // eslint-disable-next-line
  }, deps)
  useEffect(() => {
    setData(undefined)
    void reload()
    if (!intervalMs) return () => { generation.current++ }
    const timer = setInterval(() => { if (!busy.current && !document.hidden) void reload() }, intervalMs)
    return () => { clearInterval(timer); generation.current++ }
  }, [reload, intervalMs])
  return { data, error, reload }
}

/** Run an action with a busy flag and visible errors. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = useCallback(async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError('')
    try { await action(); return true } catch (e) { setError(errorText(e)); return false } finally { setBusy(false) }
  }, [])
  return { busy, error, run, setError }
}

/** Make control and bidirectional-override characters visible, so text cannot disguise itself. */
export function revealHidden(text: string): string {
  return text.replace(
    /[\u0000-\u0008\u000B-\u001F\u007F-\u009F\u200B-\u200F\u2028-\u202E\u2060-\u2069\uFEFF\u061C\u00AD\u180E\u{E0000}-\u{E007F}]/gu,
    (c) => `⟨U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}⟩`,
  )
}

export const Alert = ({ text, tone = 'danger' }: { text: string; tone?: 'danger' | 'warn' | 'ok' }) =>
  text ? <div className={`alert ${tone}`} role="alert">{text}</div> : null

export function Page({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="page">
      <header className="page-head"><h1>{title}</h1><div className="row">{actions}</div></header>
      {children}
    </section>
  )
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="card">
      {(title || actions) && <div className="card-head"><h2>{title}</h2><div className="row">{actions}</div></div>}
      {children}
    </div>
  )
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal() }, [])
  return (
    <dialog ref={ref} className={wide ? 'wide' : ''} onCancel={(e) => { e.preventDefault(); onClose() }}>
      <div className="card-head"><h2>{title}</h2><button className="ghost" onClick={onClose} aria-label="بستن">✕</button></div>
      {children}
    </dialog>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>
}

export const Empty = ({ text }: { text: string }) => <p className="empty">{text}</p>

const STATUS_TONE: Record<string, string> = {
  ok: 'ok', succeeded: 'ok', approved: 'ok', executed: 'ok', running: 'info', pending: 'warn', approval_pending: 'warn',
  denied: 'danger', failed: 'danger', error: 'danger', timed_out: 'danger', lost: 'danger', terminated: 'warn',
}
export const Badge = ({ value, tone }: { value: string; tone?: string }) =>
  <span className={`badge ${tone ?? STATUS_TONE[value] ?? ''}`}>{value}</span>

export const time = (seconds: number | null | undefined) =>
  seconds ? new Intl.DateTimeFormat('fa-IR', { dateStyle: 'short', timeStyle: 'medium' }).format(seconds * 1000) : '—'

export function bytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++ }
  return `${value.toFixed(i && value < 10 ? 1 : 0)} ${units[i]}`
}

export function Meter({ label, percent, detail }: { label: ReactNode; percent: number; detail: string }) {
  const tone = percent >= 90 ? 'danger' : percent >= 70 ? 'warn' : 'ok'
  return (
    <div className="meter">
      <div className="meter-head"><span>{label}</span><strong dir="ltr">{percent.toFixed(0)}%</strong></div>
      <div className="track" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        <div className={`fill ${tone}`} style={{ width: `${Math.min(100, percent)}%` }} />
      </div>
      <small dir="ltr">{detail}</small>
    </div>
  )
}
