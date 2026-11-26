import { useState } from 'react'
import { tool } from '../api'
import { Alert, bytes, Card, Empty, Meter, Page, useLoad } from '../ui'
import { useServer } from './common'

type Info = {
  hostname: string; os: string; kernel: string; cpu_count: number; cpu_percent: number; load_average: number[]
  uptime_seconds: number
  memory: { total_bytes: number; used_bytes: number }
  disks: { mount: string; total_bytes: number; used_bytes: number; available_bytes: number }[]
  network: { interface: string; rx_bytes_per_sec: number; tx_bytes_per_sec: number; rx_bytes: number; tx_bytes: number }[]
}
type Process = { pid: number; user: string; cpu_percent: number; memory_percent: number; command: string }

function Details({ serverId }: { serverId: string }) {
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState('cpu')
  const info = useLoad(() => tool<Info>('get_system_info', { server: serverId }), [serverId], 5000)
  const processes = useLoad(
    () => tool<{ processes: Process[] }>('get_processes', { server: serverId, filter, sort, limit: 25 }),
    [serverId, filter, sort], 6000,
  )
  const d = info.data
  const uptime = d ? `${Math.floor(d.uptime_seconds / 86400)} روز و ${Math.floor((d.uptime_seconds % 86400) / 3600)} ساعت` : ''
  return (
    <>
      <Alert text={info.error} />
      {!d && !info.error && <Empty text="در حال اتصال…" />}
      {d && (
        <>
          <p className="muted"><bdi>{d.hostname}</bdi> · <bdi>{d.os}</bdi> · <bdi>{d.kernel}</bdi> · روشن: {uptime}</p>
          <div className="grid">
            <Card><Meter label="پردازنده" percent={d.cpu_percent} detail={`${d.cpu_count} cores · load ${d.load_average.join(' ')}`} /></Card>
            <Card><Meter label="حافظه" percent={(100 * d.memory.used_bytes) / (d.memory.total_bytes || 1)} detail={`${bytes(d.memory.used_bytes)} / ${bytes(d.memory.total_bytes)}`} /></Card>
            {d.disks.slice(0, 4).map((disk) => (
              <Card key={disk.mount}><Meter label={<>دیسک <bdi dir="ltr">{disk.mount}</bdi></>} percent={(100 * disk.used_bytes) / (disk.used_bytes + disk.available_bytes || 1)} detail={`${bytes(disk.used_bytes)} / ${bytes(disk.total_bytes)}`} /></Card>
            ))}
          </div>
          <Card title="شبکه">
            <div className="scroll"><table dir="ltr"><thead><tr><th>Interface</th><th>↓ / s</th><th>↑ / s</th><th>Received</th><th>Sent</th></tr></thead>
              <tbody>{d.network.map((n) => <tr key={n.interface}><td>{n.interface}</td><td>{bytes(n.rx_bytes_per_sec)}</td><td>{bytes(n.tx_bytes_per_sec)}</td><td>{bytes(n.rx_bytes)}</td><td>{bytes(n.tx_bytes)}</td></tr>)}</tbody></table></div>
          </Card>
        </>
      )}
      <Card title="پردازش‌ها" actions={<>
        <input dir="ltr" placeholder="filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <select value={sort} onChange={(e) => setSort(e.target.value)}><option value="cpu">بر اساس CPU</option><option value="memory">بر اساس حافظه</option></select>
      </>}>
        <Alert text={processes.error} />
        <div className="scroll"><table dir="ltr"><thead><tr><th>PID</th><th>User</th><th>CPU%</th><th>MEM%</th><th>Command</th></tr></thead>
          <tbody>{processes.data?.processes.map((p) => <tr key={p.pid}><td>{p.pid}</td><td>{p.user}</td><td>{p.cpu_percent}</td><td>{p.memory_percent}</td><td className="mono clip" title={p.command}>{p.command}</td></tr>)}</tbody></table></div>
      </Card>
    </>
  )
}

export function Monitor() {
  const { server, picker, placeholder } = useServer()
  return <Page title="پایش سرور" actions={picker}>{placeholder}{server && <Details key={server.id} serverId={server.id} />}</Page>
}
