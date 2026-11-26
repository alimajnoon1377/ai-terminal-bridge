import { type FormEvent, useState } from 'react'
import { api, type Server, tool } from '../api'
import { Alert, Badge, Card, Empty, Field, Modal, Page, useAction, useLoad } from '../ui'

function AddServer({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [form, setForm] = useState({ name: '', host: '', port: 22, username: '', auth_type: 'generate', secret: '' })
  const { busy, error, run } = useAction()
  const set = (key: string, value: unknown) => setForm({ ...form, [key]: value })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => { await api('/api/servers', 'POST', form); onDone() })
  }
  return (
    <Modal title="افزودن سرور" onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="نام" hint="حروف لاتین، عدد، خط تیره و نقطه"><input dir="ltr" value={form.name} onChange={(e) => set('name', e.target.value)} pattern="[A-Za-z0-9_.\-]{1,64}" required /></Field>
        <div className="row">
          <Field label="آدرس"><input dir="ltr" value={form.host} onChange={(e) => set('host', e.target.value)} required /></Field>
          <Field label="پورت"><input dir="ltr" type="number" min={1} max={65535} value={form.port} onChange={(e) => set('port', Number(e.target.value))} /></Field>
        </div>
        <Field label="کاربر SSH" hint="یک کاربر غیر root با کمترین دسترسی لازم بسازید"><input dir="ltr" value={form.username} onChange={(e) => set('username', e.target.value)} required /></Field>
        <Field label="روش احراز هویت">
          <select value={form.auth_type} onChange={(e) => set('auth_type', e.target.value)}>
            <option value="generate">ساخت کلید جدید (پیشنهادی)</option><option value="key">کلید خصوصی موجود</option><option value="password">گذرواژه</option>
          </select>
        </Field>
        {form.auth_type === 'key' && <Field label="کلید خصوصی (بدون passphrase)"><textarea dir="ltr" rows={6} value={form.secret} onChange={(e) => set('secret', e.target.value)} required /></Field>}
        {form.auth_type === 'password' && <Field label="گذرواژه‌ی SSH"><input dir="ltr" type="password" autoComplete="new-password" value={form.secret} onChange={(e) => set('secret', e.target.value)} required /></Field>}
        <Alert text={error} />
        <button disabled={busy}>ذخیره</button>
      </form>
    </Modal>
  )
}

function ServerCard({ server, onChanged }: { server: Server; onChanged: () => void }) {
  const [probe, setProbe] = useState<{ fingerprint: string; algorithm: string; pinned_fingerprint: string } | null>(null)
  const [result, setResult] = useState('')
  const { busy, error, run } = useAction()
  return (
    <Card title={<bdi>{server.name}</bdi>} actions={<Badge value={server.trusted ? 'کلید میزبان تأیید شده' : 'نیازمند تأیید'} tone={server.trusted ? 'ok' : 'warn'} />}>
      <p dir="ltr" className="mono">{server.username}@{server.host}:{server.port} ({server.auth_type})</p>
      {server.public_key && (
        <>
          <p className="muted">این کلید عمومی باید در <bdi className="mono">~/.ssh/authorized_keys</bdi> کاربر سرور باشد:</p>
          <pre dir="ltr" className="wrap">{server.public_key}</pre>
          <button className="ghost" onClick={() => void navigator.clipboard?.writeText(server.public_key)}>کپی کلید</button>
        </>
      )}
      {server.trusted && <p dir="ltr" className="mono muted">{server.host_key_fingerprint}</p>}
      {probe && (
        <div className="alert warn">
          {probe.pinned_fingerprint && probe.pinned_fingerprint !== probe.fingerprint && <p><strong>هشدار: کلید سرور با کلید قبلی فرق دارد.</strong></p>}
          <p>اثر انگشت دریافت‌شده ({probe.algorithm}). آن را با خروجی <bdi className="mono">ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub</bdi> روی خود سرور مقایسه کنید:</p>
          <pre dir="ltr" className="wrap">{probe.fingerprint}</pre>
          <button disabled={busy} onClick={() => void run(async () => {
            await api(`/api/servers/${server.id}/trust`, 'POST', { fingerprint: probe.fingerprint }); setProbe(null); onChanged()
          })}>مطابقت دارد؛ اعتماد کن</button>
        </div>
      )}
      <Alert text={error} />
      <Alert text={result} tone="ok" />
      <div className="row">
        <button className="ghost" disabled={busy} onClick={() => void run(async () => setProbe(await api(`/api/servers/${server.id}/probe`, 'POST')))}>دریافت اثر انگشت</button>
        {server.trusted && <button disabled={busy} onClick={() => void run(async () => {
          setResult('')
          const info = await tool('connect_server', { server: server.id })
          setResult(`متصل شد: ${info.system} ${info.architecture} · کاربر ${info.user}${info.is_root ? ' (root! توصیه نمی‌شود)' : ''} · sandbox: ${info.sandbox_available ? 'فعال' : 'در دسترس نیست'}`)
        })}>آزمایش اتصال</button>}
        <button className="danger ghost" disabled={busy} onClick={() => {
          if (confirm(`سرور ${server.name} و دسترسی‌ها و تاریخچه‌ی وابسته حذف شود؟`)) void run(async () => { await api(`/api/servers/${server.id}`, 'DELETE'); onChanged() })
        }}>حذف</button>
      </div>
    </Card>
  )
}

export function Servers() {
  const { data, error, reload } = useLoad(() => api<Server[]>('/api/servers'), [])
  const [adding, setAdding] = useState(false)
  return (
    <Page title="سرورها و اتصال‌های SSH" actions={<button onClick={() => setAdding(true)}>+ افزودن سرور</button>}>
      <Alert text={error} />
      {data?.length === 0 && <Empty text="هنوز سروری ثبت نشده است." />}
      <div className="grid wide">{data?.map((s) => <ServerCard key={s.id} server={s} onChanged={() => void reload()} />)}</div>
      {adding && <AddServer onClose={() => setAdding(false)} onDone={() => { setAdding(false); void reload() }} />}
    </Page>
  )
}
