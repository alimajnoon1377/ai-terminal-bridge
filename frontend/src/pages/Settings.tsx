import { type FormEvent, useState } from 'react'
import { api, setToken, type User } from '../api'
import { Alert, Badge, Card, Field, Page, useAction, useLoad } from '../ui'

function Password({ onSignOut }: { onSignOut: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const { busy, error, run } = useAction()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    void run(async () => { await api('/api/auth/password', 'POST', { current_password: current, new_password: next }); setToken(''); onSignOut() })
  }
  return (
    <Card title="تغییر گذرواژه">
      <form onSubmit={submit}>
        <Field label="گذرواژه‌ی فعلی"><input dir="ltr" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></Field>
        <Field label="گذرواژه‌ی جدید (حداقل ۱۰ نویسه)"><input dir="ltr" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} required /></Field>
        <Alert text={error} /><button disabled={busy}>تغییر و خروج</button>
      </form>
    </Card>
  )
}

function Users({ me }: { me: User }) {
  const { data, error, reload } = useLoad(() => api<User[]>('/api/users'), [])
  const [form, setForm] = useState({ username: '', password: '', role: 'user' })
  const action = useAction()
  const change = (fn: () => Promise<unknown>) => void action.run(async () => { await fn(); await reload() })
  return (
    <Card title="کاربران">
      <Alert text={error || action.error} />
      <table><tbody>{data?.map((u) => (
        <tr key={u.id}><td><bdi>{u.username}</bdi></td><td>{u.role === 'admin' ? 'مدیر' : 'کاربر'}</td><td><Badge value={u.disabled ? 'غیرفعال' : 'فعال'} tone={u.disabled ? 'danger' : 'ok'} /></td>
          <td className="row end">{u.id !== me.id && <button className="ghost" onClick={() => change(() => api(`/api/users/${u.id}`, 'PATCH', { disabled: !u.disabled }))}>{u.disabled ? 'فعال‌سازی' : 'غیرفعال‌سازی'}</button>}</td></tr>
      ))}</tbody></table>
      <form className="row" onSubmit={(e) => { e.preventDefault(); change(async () => { await api('/api/users', 'POST', form); setForm({ username: '', password: '', role: 'user' }) }) }}>
        <input dir="ltr" placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        <input dir="ltr" type="password" autoComplete="new-password" placeholder="password (10+)" minLength={10} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="user">کاربر</option><option value="admin">مدیر</option></select>
        <button disabled={action.busy}>افزودن</button>
      </form>
    </Card>
  )
}

export function Settings({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const signOut = async () => { try { await api('/api/auth/logout', 'POST') } finally { setToken(''); onSignOut() } }
  return (
    <Page title="تنظیمات و امنیت" actions={<button className="ghost" onClick={() => void signOut()}>خروج از حساب</button>}>
      <Password onSignOut={onSignOut} />
      {user.role === 'admin' && <Users me={user} />}
      <Card title="نکته‌های امنیتی">
        <ul>
          <li>داشبورد را فقط پشت HTTPS و ترجیحاً درون VPN در دسترس قرار دهید.</li>
          <li>برای هر دستیار توکن جدا بسازید و کمترین سطح دسترسی لازم را بدهید.</li>
          <li>مرز امنیتی اصلی، دسترسی‌های کاربر SSH روی سرور و sandbox است؛ فیلتر دستورها فقط نرده‌ی محافظ است.</li>
          <li>فایل <bdi className="mono">master.key</bdi> یا مقدار <bdi className="mono">ATB_MASTER_KEY</bdi> را جدا از پایگاه داده پشتیبان بگیرید.</li>
        </ul>
      </Card>
    </Page>
  )
}
