import { useEffect, useState } from 'react'
import { api, type Server } from '../api'
import { Alert, Empty, useLoad } from '../ui'

/** Server selector shared by the pages that act on one server. */
export function useServer() {
  const { data: servers, error } = useLoad(() => api<Server[]>('/api/servers'), [])
  const [selected, setSelected] = useState('')
  const usable = servers?.filter((s) => s.trusted) ?? []
  useEffect(() => {
    if (!selected && usable.length) setSelected(usable[0].id)
  }, [usable, selected])
  const picker = usable.length > 0 && (
    <select aria-label="سرور" value={selected} onChange={(e) => setSelected(e.target.value)}>
      {usable.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.username}@{s.host})</option>)}
    </select>
  )
  const placeholder = !selected && (
    <>
      <Alert text={error} />
      {servers && <Empty text="هنوز سروری با کلید میزبان تأییدشده ندارید. از بخش «سرورها» یکی اضافه کنید." />}
    </>
  )
  return { server: usable.find((s) => s.id === selected), picker, placeholder }
}
