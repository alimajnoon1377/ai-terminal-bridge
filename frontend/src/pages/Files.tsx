import { type ChangeEvent, type FormEvent, useState } from 'react'
import { api, fromBase64, toBase64, tool } from '../api'
import { CodeEditor, DiffView } from '../monaco'
import { Alert, bytes, Card, Empty, Modal, Page, time, useAction, useLoad } from '../ui'
import { useServer } from './common'

type Entry = { name: string; type: string; size: number; mtime: number; mode: string }
type Open = { path: string; original: string; sha: string | null }
type History = {
  changes: { id: string; path: string; operation: string; rolled_back: boolean; created_at: number }[]
  snapshots: { id: string; path: string; label: string; created_at: number }[]
}
const join = (dir: string, name: string) => (dir === '/' ? `/${name}` : `${dir}/${name}`)

function HistoryModal({ serverId, onClose, onRestored }: { serverId: string; onClose: () => void; onRestored: () => void }) {
  const { data, error, reload } = useLoad(() => api<History>(`/api/servers/${serverId}/history`), [serverId])
  const action = useAction()
  const restore = (args: Record<string, string>) => {
    if (!confirm('محتوای فعلی با نسخه‌ی انتخابی جایگزین شود؟')) return
    void action.run(async () => { await tool('rollback_changes', { server: serverId, ...args }); await reload(); onRestored() })
  }
  return (
    <Modal title="تغییرات و نسخه‌های پشتیبان" onClose={onClose} wide>
      <Alert text={error || action.error} />
      <h3>نسخه‌های پوشه (Snapshot)</h3>
      {data?.snapshots.length === 0 && <Empty text="موردی نیست." />}
      <table><tbody>{data?.snapshots.map((s) => <tr key={s.id}><td>{time(s.created_at)}</td><td dir="ltr" className="mono">{s.path}</td><td>{s.label}</td><td className="row end"><button className="ghost" onClick={() => restore({ snapshot_id: s.id })}>بازیابی</button></td></tr>)}</tbody></table>
      <h3>تغییرات فایل</h3>
      {data?.changes.length === 0 && <Empty text="موردی نیست." />}
      <table><tbody>{data?.changes.map((c) => <tr key={c.id}><td>{time(c.created_at)}</td><td dir="ltr" className="mono">{c.path}</td><td>{c.operation}</td><td className="row end">{c.rolled_back ? <span className="muted">برگردانده شد</span> : <button className="ghost" onClick={() => restore({ change_id: c.id })}>برگرداندن</button>}</td></tr>)}</tbody></table>
    </Modal>
  )
}

function Browser({ serverId }: { serverId: string }) {
  const [directory, setDirectory] = useState('.')
  const [address, setAddress] = useState('')
  const [open, setOpen] = useState<Open | null>(null)
  const [draft, setDraft] = useState('')
  const [reviewing, setReviewing] = useState(false)
  const [history, setHistory] = useState(false)
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<{ path: string; line: number; text: string }[] | null>(null)
  const listing = useLoad(() => tool<{ path: string; entries: Entry[]; truncated: boolean }>('list_directory', { server: serverId, path: directory }), [serverId, directory])
  const { busy, error, run } = useAction()
  const here = listing.data?.path ?? ''
  const dirty = open !== null && draft !== open.original
  const leave = () => !dirty || confirm('تغییرات ذخیره‌نشده کنار گذاشته شود؟')
  const go = (path: string) => { setDirectory(path); setAddress(''); setMatches(null) }

  const openFile = (path: string) => {
    if (!leave()) return
    void run(async () => {
      const file = await tool('read_file', { server: serverId, path, max_lines: 20000 })
      if (file.truncated) throw new Error('فایل برای ویرایشگر بیش از حد بلند است (بیش از ۲۰٬۰۰۰ خط).')
      setOpen({ path: file.path, original: file.content, sha: file.sha256 }); setDraft(file.content)
    })
  }
  const save = () => void run(async () => {
    const result = await tool('write_file', { server: serverId, path: open!.path, content: draft, ...(open!.sha ? { expected_sha256: open!.sha } : {}) })
    setOpen({ path: open!.path, original: draft, sha: result.sha256 }); setReviewing(false); await listing.reload()
  })
  const change = (action: () => Promise<unknown>) => void run(async () => { await action(); await listing.reload() })
  const download = (path: string, name: string) => void run(async () => {
    const file = await tool('download_file', { server: serverId, path })
    const url = URL.createObjectURL(new Blob([fromBase64(file.content_base64)]))
    Object.assign(document.createElement('a'), { href: url, download: name }).click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
  const upload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    change(async () => {
      if (file.size > 5 * 1024 * 1024) throw new Error('حداکثر اندازه‌ی بارگذاری ۵ مگابایت است.')
      const exists = listing.data?.entries.some((e) => e.name === file.name)
      if (exists && !confirm(`${file.name} جایگزین شود؟`)) return
      await tool('upload_file', { server: serverId, path: join(here, file.name), overwrite: exists, content_base64: toBase64(new Uint8Array(await file.arrayBuffer())) })
    })
  }
  const search = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => setMatches((await tool('search_files', { server: serverId, path: here || '.', pattern: query })).matches))
  }

  return (
    <>
      <Alert text={error || listing.error} />
      <div className="split">
        <Card>
          <form className="row" onSubmit={(e) => { e.preventDefault(); if (address) go(address) }}>
            <button type="button" className="ghost" onClick={() => go(here.replace(/\/[^/]*$/, '') || '/')} title="پوشه‌ی بالاتر">↑</button>
            <input dir="ltr" className="grow mono" aria-label="مسیر" value={address || here} onChange={(e) => setAddress(e.target.value)} />
          </form>
          <div className="row">
            <button className="ghost" disabled={!here} onClick={() => { const name = prompt('نام فایل جدید'); if (name && leave()) { setOpen({ path: join(here, name), original: '', sha: null }); setDraft('') } }}>فایل جدید</button>
            <button className="ghost" disabled={!here} onClick={() => { const name = prompt('نام پوشه‌ی جدید'); if (name) change(() => tool('create_directory', { server: serverId, path: join(here, name) })) }}>پوشه‌ی جدید</button>
            <label className="button ghost">بارگذاری<input type="file" hidden onChange={upload} disabled={!here} /></label>
            <button className="ghost" disabled={!here} onClick={() => { const label = prompt(`برچسب نسخه‌ی پشتیبان از ${here}`, ''); if (label !== null) change(() => tool('create_snapshot', { server: serverId, path: here, label })) }}>Snapshot</button>
            <button className="ghost" onClick={() => setHistory(true)}>تغییرات</button>
          </div>
          <form className="row" onSubmit={search}><input dir="ltr" className="grow" placeholder="search (regex)" value={query} onChange={(e) => setQuery(e.target.value)} required /><button className="ghost">جست‌وجو</button></form>
          {matches ? (
            <ul className="files" dir="ltr">
              <li><button className="link" onClick={() => setMatches(null)}>✕ close results ({matches.length})</button></li>
              {matches.map((m, i) => <li key={i}><button className="link mono" onClick={() => openFile(m.path)}>{m.path}:{m.line}</button><small className="clip">{m.text}</small></li>)}
            </ul>
          ) : (
            <ul className="files" dir="ltr">
              {listing.data?.entries.map((e) => (
                <li key={e.name}>
                  <button className="link grow mono" onClick={() => (e.type === 'directory' ? go(join(here, e.name)) : openFile(join(here, e.name)))}>{e.type === 'directory' ? '📁' : e.type === 'symlink' ? '🔗' : '📄'} {e.name}</button>
                  <small>{e.type === 'file' ? bytes(e.size) : ''}</small>
                  {e.type === 'file' && <button className="ghost small" title="download" onClick={() => download(join(here, e.name), e.name)}>⇩</button>}
                  <button className="ghost small danger" title="delete" onClick={() => {
                    if (!confirm(`${e.name} حذف شود؟${e.type === 'directory' ? ' کل محتوای پوشه برای همیشه حذف می‌شود.' : ''}`)) return
                    change(async () => { await tool('delete_file', { server: serverId, path: join(here, e.name), recursive: e.type === 'directory' }); if (open?.path === join(here, e.name)) setOpen(null) })
                  }}>✕</button>
                </li>
              ))}
              {listing.data?.entries.length === 0 && <li className="muted">empty</li>}
            </ul>
          )}
        </Card>
        <Card title={open ? <bdi className="mono">{open.path}{dirty ? ' •' : ''}</bdi> : 'ویرایشگر'} actions={open && <>
          <button disabled={busy || (!dirty && open.sha !== null)} onClick={() => setReviewing(true)}>بررسی و ذخیره</button>
          <button className="ghost" onClick={() => { if (leave()) setOpen(null) }}>بستن</button>
        </>}>
          {open ? <CodeEditor path={open.path} value={draft} onChange={setDraft} onSave={() => setReviewing(true)} /> : <Empty text="برای ویرایش، فایلی را انتخاب کنید." />}
        </Card>
      </div>
      {reviewing && open && (
        <Modal title="تغییرات پیش از اعمال" onClose={() => setReviewing(false)} wide>
          <DiffView path={open.path} before={open.original} after={draft} />
          <Alert text={error} />
          <div className="row"><button disabled={busy} onClick={save}>اعمال تغییرات</button><button className="ghost" onClick={() => setReviewing(false)}>بازگشت</button></div>
        </Modal>
      )}
      {history && <HistoryModal serverId={serverId} onClose={() => setHistory(false)} onRestored={() => { setOpen(null); void listing.reload() }} />}
    </>
  )
}

export function Files() {
  const { server, picker, placeholder } = useServer()
  return <Page title="فایل‌ها و ویرایشگر کد" actions={picker}>{placeholder}{server && <Browser key={server.id} serverId={server.id} />}</Page>
}
