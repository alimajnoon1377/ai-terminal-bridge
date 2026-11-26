import { useEffect, useRef, useState } from 'react'
import { api, type AuditEntry, type Job, type LiveEvent, tool, type User } from '../api'
import { Alert, Badge, Card, Empty, Modal, Page, revealHidden, time, useAction, useLoad } from '../ui'

export function Activity({ events, connected }: { events: LiveEvent[]; connected: boolean }) {
  const shown = events.filter((e) => e.type !== 'job_output').slice(0, 200)
  const output = events.filter((e) => e.type === 'job_output').slice(0, 60).reverse()
  return (
    <Page title="فعالیت زنده‌ی دستیارها" actions={<Badge value={connected ? 'زنده' : 'در حال اتصال…'} tone={connected ? 'ok' : 'warn'} />}>
      {output.length > 0 && <Card title="خروجی دستورها"><pre dir="ltr" className="console">{output.map((e) => String(e.data)).join('')}</pre></Card>}
      <Card title="رویدادها (از زمان باز شدن این صفحه)">
        {shown.length === 0 && <Empty text="هنوز رویدادی دریافت نشده است." />}
        <ul className="feed">{shown.map((e, i) => (
          <li key={`${e.ts}-${i}`}>
            <span className="muted">{time(e.ts)}</span>
            <bdi><strong>{String(e.actor ?? '')}</strong></bdi>
            <Badge value={String(e.status ?? e.type)} />
            <span dir="ltr" className="mono clip grow">{revealHidden(String(e.summary ?? e.command ?? e.job_id ?? ''))}</span>
          </li>
        ))}</ul>
      </Card>
    </Page>
  )
}

function Output({ job, onClose }: { job: Job; onClose: () => void }) {
  const [text, setText] = useState('')
  const [status, setStatus] = useState(job.status)
  const [error, setError] = useState('')
  const box = useRef<HTMLPreElement>(null)
  useEffect(() => {
    let active = true
    void (async () => {
      let offset = 0
      try {
        while (active) {
          const r = await tool('get_command_output', { command_id: job.id, offset, wait_seconds: 0 })
          if (!active) return
          offset = r.next_offset
          setStatus(r.status)
          if (r.output) setText((t) => (t + r.output).slice(-500_000))
          if (r.status !== 'running' && !r.more_output) break
          if (!r.more_output) await new Promise((done) => setTimeout(done, 800))
        }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : String(e)) }
    })()
    return () => { active = false }
  }, [job.id])
  useEffect(() => { box.current?.scrollTo(0, box.current.scrollHeight) }, [text])
  return (
    <Modal title="خروجی دستور" onClose={onClose} wide>
      <pre dir="ltr" className="wrap">$ {revealHidden(job.command)}</pre>
      <Badge value={status} />
      <Alert text={error} />
      <pre dir="ltr" className="console" ref={box}>{text || ' '}</pre>
    </Modal>
  )
}

export function Jobs({ events }: { events: LiveEvent[] }) {
  const { data, error, reload } = useLoad(() => api<Job[]>('/api/jobs'), [], 5000)
  const marker = events.find((e) => e.type === 'job_started' || e.type === 'job_finished')?.ts
  useEffect(() => { if (marker) void reload() }, [marker, reload])
  const [open, setOpen] = useState<Job | null>(null)
  const action = useAction()
  return (
    <Page title="تاریخچه‌ی دستورها و نشست‌های اجرا">
      <Alert text={error || action.error} />
      <Card>
        {data?.length === 0 && <Empty text="هنوز دستوری اجرا نشده است." />}
        <div className="scroll"><table><thead><tr><th>زمان</th><th>اجراکننده</th><th>سرور</th><th>دستور</th><th>وضعیت</th><th>جداسازی</th><th /></tr></thead><tbody>
          {data?.map((j) => (
            <tr key={j.id}><td>{time(j.created_at)}</td><td>{j.actor_name}</td><td>{j.server_name}</td>
              <td dir="ltr" className="mono clip" title={j.command}>{revealHidden(j.command)}</td>
              <td><Badge value={j.status} />{j.exit_code !== null && <small> ({j.exit_code})</small>}</td>
              <td>{j.sandboxed ? 'sandbox' : '—'}</td>
              <td className="row end"><button className="ghost" onClick={() => setOpen(j)}>خروجی</button>
                {j.status === 'running' && <button className="danger" disabled={action.busy} onClick={() => void action.run(async () => { await api(`/api/jobs/${j.id}/terminate`, 'POST'); await reload() })}>توقف</button>}</td></tr>
          ))}
        </tbody></table></div>
      </Card>
      {open && <Output job={open} onClose={() => setOpen(null)} />}
    </Page>
  )
}

export function Audit({ user }: { user: User }) {
  const [before, setBefore] = useState<number | null>(null)
  const { data, error, reload } = useLoad(() => api<AuditEntry[]>(`/api/audit?limit=100${before ? `&before=${before}` : ''}`), [before])
  const [verdict, setVerdict] = useState('')
  const action = useAction()
  return (
    <Page title="گزارش ممیزی" actions={<>
      <button className="ghost" onClick={() => { setBefore(null); void reload() }}>تازه‌سازی</button>
      {user.role === 'admin' && <button disabled={action.busy} onClick={() => void action.run(async () => {
        const r = await api<{ valid: boolean; entries: number; broken_at?: number }>('/api/audit/verify')
        setVerdict(r.valid ? `زنجیره سالم است (${r.entries} رکورد).` : `دستکاری شناسایی شد: رکورد ${r.broken_at}`)
      })}>بررسی یکپارچگی</button>}
    </>}>
      <Alert text={error || action.error} />
      <Alert text={verdict} tone={verdict.startsWith('زنجیره') ? 'ok' : 'danger'} />
      <Card>
        <div className="scroll"><table><thead><tr><th>#</th><th>زمان</th><th>کاربر / دستیار</th><th>عملیات</th><th>نتیجه</th><th>جزئیات</th></tr></thead><tbody>
          {data?.map((e) => (
            <tr key={e.seq}><td>{e.seq}</td><td>{time(e.ts)}</td><td>{e.actor_name} <small className="muted">{e.actor_type}</small></td>
              <td dir="ltr" className="mono">{e.action}</td><td><Badge value={e.status} /></td>
              <td><details><summary dir="ltr" className="clip">{revealHidden(String(e.detail.summary ?? '…'))}</summary><pre dir="ltr" className="wrap">{revealHidden(JSON.stringify(e.detail, null, 2))}</pre></details></td></tr>
          ))}
        </tbody></table></div>
        {data && data.length === 100 && <button className="ghost" onClick={() => setBefore(data[data.length - 1].seq)}>قدیمی‌تر</button>}
      </Card>
    </Page>
  )
}
