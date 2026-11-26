import { useEffect, useState } from 'react'
import { api, type Approval } from '../api'
import { Alert, Badge, Card, Empty, Page, revealHidden, time, useAction, useLoad } from '../ui'

function Preview({ text }: { text: string }) {
  return (
    <pre dir="ltr" className="diff">{revealHidden(text).split('\n').map((line, i) => (
      <span key={i} className={line.startsWith('+') && !line.startsWith('+++') ? 'add' : line.startsWith('-') && !line.startsWith('---') ? 'del' : ''}>{line}{'\n'}</span>
    ))}</pre>
  )
}

function Request({ item, onDecided }: { item: Approval; onDecided: () => void }) {
  const [reason, setReason] = useState('')
  const { busy, error, run } = useAction()
  const decide = (approve: boolean) => void run(async () => {
    await api(`/api/approvals/${item.id}/decision`, 'POST', { approve, reason }); onDecided()
  })
  return (
    <Card title={<><bdi>{item.agent_name}</bdi> روی <bdi>{item.server_name}</bdi></>} actions={<Badge value={item.tool} tone="info" />}>
      <p dir="ltr" className="mono wrap">{revealHidden(item.summary)}</p>
      <Preview text={item.preview} />
      <p className="muted">ثبت: {time(item.created_at)} · انقضا: {time(item.expires_at)}</p>
      <Alert text={error} />
      <div className="row">
        <input placeholder="توضیح برای دستیار (اختیاری)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button disabled={busy} onClick={() => decide(true)}>تأیید</button>
        <button className="danger" disabled={busy} onClick={() => decide(false)}>رد</button>
      </div>
    </Card>
  )
}

export function Approvals({ refreshKey }: { refreshKey?: number }) {
  const { data, error, reload } = useLoad(() => api<Approval[]>('/api/approvals'), [], 5000)
  useEffect(() => { if (refreshKey) void reload() }, [refreshKey, reload])
  const open = data?.filter((a) => a.status === 'pending' && a.expires_at * 1000 > Date.now()) ?? []
  const past = data?.filter((a) => !open.includes(a)) ?? []
  return (
    <Page title="تأیید یا رد عملیات حساس">
      <Alert text={error} />
      {data && open.length === 0 && <Empty text="درخواستی در انتظار تأیید نیست." />}
      {open.map((item) => <Request key={item.id} item={item} onDecided={() => void reload()} />)}
      {past.length > 0 && (
        <Card title="تاریخچه"><div className="scroll"><table><thead><tr><th>زمان</th><th>دستیار</th><th>سرور</th><th>عملیات</th><th>وضعیت</th><th>تصمیم‌گیرنده</th></tr></thead><tbody>
          {past.map((a) => <tr key={a.id}><td>{time(a.created_at)}</td><td>{a.agent_name}</td><td>{a.server_name}</td><td dir="ltr" className="mono clip" title={a.summary}>{revealHidden(a.summary)}</td><td><Badge value={a.status === 'pending' ? 'expired' : a.status} /></td><td>{a.decided_by}</td></tr>)}
        </tbody></table></div></Card>
      )}
    </Page>
  )
}
