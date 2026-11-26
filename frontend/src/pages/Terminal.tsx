import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { useEffect, useRef, useState } from 'react'
import { getToken, socketUrl } from '../api'
import { Alert, Page } from '../ui'
import { useServer } from './common'

function Session({ serverId, name, generation }: { serverId: string; name: string; generation: number }) {
  const host = useRef<HTMLDivElement>(null)
  const [state, setState] = useState('در حال اتصال…')
  const [persistent, setPersistent] = useState(false)
  const [problem, setProblem] = useState('')
  useEffect(() => {
    setState('در حال اتصال…'); setProblem(''); setPersistent(false)
    const terminal = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 5000, fontFamily: 'ui-monospace, Consolas, "DejaVu Sans Mono", monospace' })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(host.current!)
    fit.fit()
    const socket = new WebSocket(socketUrl(`/api/ws/terminal/${serverId}`))
    socket.binaryType = 'arraybuffer'
    const send = (message: object) => { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)) }
    socket.onopen = () => send({ token: getToken(), cols: terminal.cols, rows: terminal.rows, session: name })
    socket.onmessage = ({ data }) => {
      if (typeof data !== 'string') return terminal.write(new Uint8Array(data))
      const message = JSON.parse(data)
      if (message.type === 'ready') {
        setPersistent(message.persistent)
        setState(message.persistent ? 'متصل — نشست ماندگار tmux:' : 'متصل — بدون ماندگاری (tmux روی سرور نیست یا نام نشست خالی است)')
        terminal.focus()
      } else if (message.type === 'exit') setState('نشست پایان یافت')
      else if (message.type === 'error') setProblem(message.message)
    }
    socket.onclose = (event) => setState((s) => (s === 'نشست پایان یافت' ? s : `ارتباط قطع شد${event.reason ? `: ${event.reason}` : ''}`))
    const input = terminal.onData((data) => send({ type: 'input', data }))
    const resize = terminal.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }))
    const observer = new ResizeObserver(() => fit.fit())
    observer.observe(host.current!)
    return () => { observer.disconnect(); input.dispose(); resize.dispose(); socket.close(); terminal.dispose() }
  }, [serverId, name, generation])
  return (
    <>
      <p className="muted">{state} {persistent && <bdi dir="ltr">{name}</bdi>}</p>
      <Alert text={problem} />
      <div className="term-host" dir="ltr" ref={host} />
    </>
  )
}

export function TerminalPage() {
  const { server, picker, placeholder } = useServer()
  const [name, setName] = useState('main')
  const [active, setActive] = useState('main')
  const [generation, setGeneration] = useState(0)
  return (
    <Page title="ترمینال زنده" actions={<>
      {picker}
      <form className="row" onSubmit={(e) => { e.preventDefault(); setActive(name); setGeneration((g) => g + 1) }}>
        <input dir="ltr" aria-label="نام نشست" placeholder="session name" value={name} pattern="[A-Za-z0-9_\-]{0,32}" onChange={(e) => setName(e.target.value)} />
        <button className="ghost">اتصال دوباره</button>
      </form>
    </>}>
      {placeholder}
      {server && <Session serverId={server.id} name={active} generation={generation} />}
    </Page>
  )
}
