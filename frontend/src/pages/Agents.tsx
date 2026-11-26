import { type FormEvent, useState } from 'react'
import { type Agent, api, type GrantConfig, type Server } from '../api'
import { Alert, Badge, Card, Empty, Field, Modal, Page, time, useAction, useLoad } from '../ui'

const LEVELS: [string, string, string][] = [
  ['read_only', 'فقط خواندن', 'مشاهده‌ی فایل‌ها و وضعیت سیستم؛ بدون اجرای دستور.'],
  ['developer', 'توسعه‌دهنده', 'نوشتن در مسیرهای مجاز و اجرای دستور درون sandbox. بدون sandbox هر دستور تأیید می‌خواهد.'],
  ['operator', 'اپراتور', 'اجرای دستور با دسترسی کامل کاربر SSH (مسیرها دستورها را محدود نمی‌کنند). عملیات مخرب تأیید می‌خواهد.'],
  ['approval_required', 'نیازمند تأیید', 'هر تغییر یا اجرای دستور منتظر تأیید شما می‌ماند.'],
  ['autonomous', 'خودمختار', 'کار مستقل در محدوده‌ی تعیین‌شده؛ عملیات مخرب پیش‌فرض تأیید می‌خواهد.'],
]

const DEFAULT_GRANT: GrantConfig = {
  level: 'read_only', paths: [], services: [], allowed_commands: [], denied_patterns: [], force_sandbox: false,
  sandbox_network: false, allow_docker: false, use_sudo_for_services: false, auto_approve_destructive: false,
  max_runtime_seconds: 600, max_output_kb: 2048, max_memory_mb: 0, max_concurrent_jobs: 4,
}

const words = (text: string) => text.split(/[\s,]+/).filter(Boolean)
const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

function GrantEditor({ agent, server, current, onClose, onSaved }: {
  agent: Agent; server: Server; current?: GrantConfig; onClose: () => void; onSaved: () => void
}) {
  const start = current ?? DEFAULT_GRANT
  const [config, setConfig] = useState(start)
  const [paths, setPaths] = useState(start.paths.map((p) => `${p.mode} ${p.path}`).join('\n'))
  const [services, setServices] = useState(start.services.join(' '))
  const [allowed, setAllowed] = useState(start.allowed_commands.join(' '))
  const [denied, setDenied] = useState(start.denied_patterns.join('\n'))
  const { busy, error, run } = useAction()
  const set = <K extends keyof GrantConfig>(key: K, value: GrantConfig[K]) => setConfig({ ...config, [key]: value })
  const flag = (key: 'force_sandbox' | 'sandbox_network' | 'allow_docker' | 'use_sudo_for_services' | 'auto_approve_destructive', label: string) => (
    <label className="check"><input type="checkbox" checked={config[key]} onChange={(e) => set(key, e.target.checked)} />{label}</label>
  )
  const number = (key: 'max_runtime_seconds' | 'max_output_kb' | 'max_memory_mb' | 'max_concurrent_jobs', label: string) => (
    <Field label={label}><input dir="ltr" type="number" min={key === 'max_memory_mb' ? 0 : 1} value={config[key]} onChange={(e) => set(key, Number(e.target.value))} /></Field>
  )
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      const rules = lines(paths).map((line) => {
        const match = /^(?:(ro|rw)\s+)?(\/.*)$/.exec(line)
        if (!match) throw new Error(`خط مسیر نامعتبر است: ${line}`)
        return { mode: (match[1] ?? 'rw') as 'ro' | 'rw', path: match[2] }
      })
      await api(`/api/agents/${agent.id}/grants/${server.id}`, 'PUT', {
        ...config, paths: rules, services: words(services), allowed_commands: words(allowed), denied_patterns: lines(denied),
      })
      onSaved()
    })
  }
  return (
    <Modal title={`دسترسی ${agent.name} روی ${server.name}`} onClose={onClose} wide>
      <form onSubmit={submit}>
        <Field label="سطح دسترسی" hint={LEVELS.find((l) => l[0] === config.level)?.[2]}>
          <select value={config.level} onChange={(e) => set('level', e.target.value)}>{LEVELS.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>
        </Field>
        <Field label="مسیرهای مجاز (هر خط: rw یا ro و سپس مسیر مطلق)" hint="پوشه‌ی پروژه را بدهید، نه پوشه‌ی خانگی کاربر.">
          <textarea dir="ltr" rows={3} placeholder={'rw /srv/myproject\nro /var/log/myapp'} value={paths} onChange={(e) => setPaths(e.target.value)} />
        </Field>
        <Field label="سرویس‌های مجاز systemd" hint="با فاصله جدا کنید"><input dir="ltr" value={services} onChange={(e) => setServices(e.target.value)} placeholder="nginx myapp" /></Field>
        <Field label="فهرست برنامه‌های مجاز (خالی = بدون محدودیت)" hint="اگر پر شود، فقط همین برنامه‌ها و بدون عملگرهای shell اجرا می‌شوند. برنامه‌هایی مثل python یا bash عملاً همه‌چیز را مجاز می‌کنند.">
          <input dir="ltr" value={allowed} onChange={(e) => setAllowed(e.target.value)} placeholder="git ls cat" />
        </Field>
        <Field label="الگوهای ممنوع (regex، هر خط یکی)"><textarea dir="ltr" rows={2} value={denied} onChange={(e) => setDenied(e.target.value)} /></Field>
        <div className="checks">
          {flag('force_sandbox', 'اجبار sandbox')}{flag('sandbox_network', 'شبکه درون sandbox')}{flag('allow_docker', 'اجازه‌ی Docker (هم‌ارز root)')}
          {flag('use_sudo_for_services', 'sudo برای سرویس‌ها')}{flag('auto_approve_destructive', 'خودمختار: عملیات مخرب بدون تأیید')}
        </div>
        <div className="row">{number('max_runtime_seconds', 'حداکثر زمان (ثانیه)')}{number('max_output_kb', 'حداکثر خروجی (KB)')}{number('max_memory_mb', 'حداکثر حافظه (MB، صفر=آزاد)')}{number('max_concurrent_jobs', 'دستور هم‌زمان')}</div>
        <p className="muted">ذخیره‌ی دسترسی، دستورهای در حال اجرای این دستیار روی این سرور را متوقف می‌کند.</p>
        <Alert text={error} />
        <div className="row">
          <button disabled={busy}>ذخیره</button>
          {current && <button type="button" className="danger ghost" disabled={busy} onClick={() => void run(async () => { await api(`/api/agents/${agent.id}/grants/${server.id}`, 'DELETE'); onSaved() })}>حذف دسترسی</button>}
        </div>
      </form>
    </Modal>
  )
}

function Connection({ agent, onClose }: { agent: Agent; onClose: () => void }) {
  const url = location.origin
  const http = JSON.stringify({ mcpServers: { 'terminal-bridge': { type: 'http', url: `${url}/mcp`, headers: { Authorization: `Bearer ${agent.token}` } } } }, null, 2)
  const stdio = JSON.stringify({ mcpServers: { 'terminal-bridge': { command: 'atb', args: ['mcp-stdio'], env: { ATB_URL: url, ATB_AGENT_TOKEN: agent.token } } } }, null, 2)
  const block = (title: string, text: string) => (
    <><h3>{title}</h3><pre dir="ltr" className="wrap">{text}</pre><button className="ghost" onClick={() => void navigator.clipboard?.writeText(text)}>کپی</button></>
  )
  return (
    <Modal title="توکن دستیار — فقط همین یک بار نمایش داده می‌شود" onClose={onClose} wide>
      {block('توکن', agent.token!)}
      {block('Claude Code (از خط فرمان)', `claude mcp add --transport http terminal-bridge ${url}/mcp --header "Authorization: Bearer ${agent.token}"`)}
      {block('کلاینت MCP با اتصال HTTP', http)}
      {block('کلاینت MCP با اتصال stdio (مثل Claude Desktop)', stdio)}
      <p className="muted">مدل تنظیمات هر کلاینت متفاوت است؛ راهنمای docs/CLIENTS.md را ببینید.</p>
    </Modal>
  )
}

export function Agents() {
  const agents = useLoad(() => api<Agent[]>('/api/agents'), [])
  const servers = useLoad(() => api<Server[]>('/api/servers'), [])
  const [selected, setSelected] = useState('')
  const grants = useLoad(
    () => (selected ? api<{ server_id: string; config: GrantConfig }[]>(`/api/agents/${selected}/grants`) : Promise.resolve([])),
    [selected],
  )
  const [name, setName] = useState('')
  const [revealed, setRevealed] = useState<Agent | null>(null)
  const [editing, setEditing] = useState<Server | null>(null)
  const { busy, error, run } = useAction()
  const agent = agents.data?.find((a) => a.id === selected)
  const create = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      const created = await api<Agent>('/api/agents', 'POST', { name })
      setName(''); setRevealed(created); setSelected(created.id); await agents.reload()
    })
  }
  const act = (action: () => Promise<unknown>) => void run(async () => { await action(); await agents.reload() })
  return (
    <Page title="دستیارهای هوش مصنوعی و سطح دسترسی">
      <Card title="دستیار جدید">
        <form className="row" onSubmit={create}><input placeholder="مثلاً Claude Code روی لپ‌تاپ" value={name} onChange={(e) => setName(e.target.value)} required maxLength={64} /><button disabled={busy}>ساخت توکن</button></form>
      </Card>
      <Alert text={error || agents.error} />
      <Card title="دستیارها">
        {agents.data?.length === 0 && <Empty text="هنوز دستیاری تعریف نشده است." />}
        <div className="scroll"><table><thead><tr><th>نام</th><th>توکن</th><th>آخرین فعالیت</th><th>وضعیت</th><th /></tr></thead><tbody>
          {agents.data?.map((a) => (
            <tr key={a.id} className={a.id === selected ? 'selected' : ''}>
              <td><button className="link" onClick={() => setSelected(a.id)}>{a.name}</button></td>
              <td dir="ltr" className="mono">…{a.token_hint}</td><td>{time(a.last_seen_at)}</td>
              <td><Badge value={a.paused ? 'متوقف' : 'فعال'} tone={a.paused ? 'danger' : 'ok'} /></td>
              <td className="row end">
                <button className="ghost" onClick={() => setSelected(a.id)}>دسترسی‌ها</button>
                <button className="ghost" onClick={() => act(() => api(`/api/agents/${a.id}`, 'PATCH', { paused: !a.paused }))}>{a.paused ? 'فعال‌سازی' : 'توقف'}</button>
                <button className="ghost" onClick={() => { if (confirm('توکن فعلی باطل و توکن تازه ساخته شود؟')) act(async () => setRevealed(await api<Agent>(`/api/agents/${a.id}/rotate`, 'POST'))) }}>توکن تازه</button>
                <button className="ghost danger" onClick={() => { if (confirm(`دستیار ${a.name} حذف شود؟`)) act(async () => { await api(`/api/agents/${a.id}`, 'DELETE'); setSelected('') }) }}>حذف</button>
              </td>
            </tr>
          ))}
        </tbody></table></div>
      </Card>
      {agent && (
        <Card title={`دسترسی‌های ${agent.name}`}>
          <Alert text={grants.error} />
          <div className="scroll"><table><thead><tr><th>سرور</th><th>سطح</th><th>مسیرها</th><th /></tr></thead><tbody>
            {servers.data?.map((s) => {
              const grant = grants.data?.find((g) => g.server_id === s.id)?.config
              return (
                <tr key={s.id}><td><bdi>{s.name}</bdi></td>
                  <td>{grant ? LEVELS.find((l) => l[0] === grant.level)?.[1] : <span className="muted">بدون دسترسی</span>}</td>
                  <td dir="ltr" className="mono">{grant?.paths.map((p) => `${p.mode} ${p.path}`).join(', ')}</td>
                  <td className="row end"><button className="ghost" disabled={!grants.data} onClick={() => setEditing(s)}>{grant ? 'ویرایش' : 'اعطای دسترسی'}</button></td></tr>
              )
            })}
          </tbody></table></div>
          {servers.data?.length === 0 && <Empty text="ابتدا یک سرور اضافه کنید." />}
        </Card>
      )}
      {editing && agent && <GrantEditor agent={agent} server={editing} current={grants.data?.find((g) => g.server_id === editing.id)?.config}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void grants.reload() }} />}
      {revealed && <Connection agent={revealed} onClose={() => setRevealed(null)} />}
    </Page>
  )
}
