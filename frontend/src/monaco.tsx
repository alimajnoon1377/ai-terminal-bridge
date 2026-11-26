import * as monaco from 'monaco-editor/editor/editor.api.js'
import 'monaco-editor/features/register.all.js'
import 'monaco-editor/languages/definitions/cpp/register.js'
import 'monaco-editor/languages/definitions/csharp/register.js'
import 'monaco-editor/languages/definitions/css/register.js'
import 'monaco-editor/languages/definitions/dockerfile/register.js'
import 'monaco-editor/languages/definitions/go/register.js'
import 'monaco-editor/languages/definitions/html/register.js'
import 'monaco-editor/languages/definitions/ini/register.js'
import 'monaco-editor/languages/definitions/java/register.js'
import 'monaco-editor/languages/definitions/javascript/register.js'
import 'monaco-editor/languages/definitions/markdown/register.js'
import 'monaco-editor/languages/definitions/php/register.js'
import 'monaco-editor/languages/definitions/powershell/register.js'
import 'monaco-editor/languages/definitions/python/register.js'
import 'monaco-editor/languages/definitions/ruby/register.js'
import 'monaco-editor/languages/definitions/rust/register.js'
import 'monaco-editor/languages/definitions/shell/register.js'
import 'monaco-editor/languages/definitions/sql/register.js'
import 'monaco-editor/languages/definitions/typescript/register.js'
import 'monaco-editor/languages/definitions/xml/register.js'
import 'monaco-editor/languages/definitions/yaml/register.js'
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker'
import { useEffect, useRef } from 'react'

// Everything is bundled with the dashboard; nothing is fetched from a CDN.
self.MonacoEnvironment = { getWorker: () => new EditorWorker() }

const EXTENSIONS: Record<string, string> = {
  c: 'cpp', h: 'cpp', cc: 'cpp', cpp: 'cpp', cs: 'csharp', css: 'css', go: 'go', html: 'html', ini: 'ini', toml: 'ini',
  conf: 'ini', env: 'ini', java: 'java', js: 'javascript', jsx: 'javascript', mjs: 'javascript', json: 'javascript',
  md: 'markdown', php: 'php', ps1: 'powershell', py: 'python', rb: 'ruby', rs: 'rust', sh: 'shell', bash: 'shell',
  sql: 'sql', ts: 'typescript', tsx: 'typescript', xml: 'xml', yml: 'yaml', yaml: 'yaml',
}

function languageFor(path: string): string {
  const name = path.split('/').pop()!.toLowerCase()
  if (name === 'dockerfile') return 'dockerfile'
  return EXTENSIONS[name.split('.').pop()!] ?? 'plaintext'
}

const theme = () => (document.documentElement.dataset.theme === 'dark' ? 'vs-dark' : 'vs')

export function CodeEditor({ path, value, onChange, onSave }: {
  path: string; value: string; onChange: (value: string) => void; onSave: () => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const handlers = useRef({ onChange, onSave })
  handlers.current = { onChange, onSave }
  useEffect(() => {
    const model = monaco.editor.createModel(value, languageFor(path))
    const editor = monaco.editor.create(host.current!, {
      model, theme: theme(), automaticLayout: true, minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false,
    })
    const subscription = model.onDidChangeContent(() => handlers.current.onChange(model.getValue()))
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => handlers.current.onSave())
    return () => { subscription.dispose(); editor.dispose(); model.dispose() }
    // A different file gets a new editor; typing does not.
    // eslint-disable-next-line
  }, [path])
  return <div className="editor" dir="ltr" ref={host} />
}

export function DiffView({ path, before, after }: { path: string; before: string; after: string }) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const language = languageFor(path)
    const original = monaco.editor.createModel(before, language)
    const modified = monaco.editor.createModel(after, language)
    const editor = monaco.editor.createDiffEditor(host.current!, {
      theme: theme(), automaticLayout: true, readOnly: true, minimap: { enabled: false }, fontSize: 13,
      renderSideBySide: window.innerWidth > 900, renderGutterMenu: false,
    })
    editor.setModel({ original, modified })
    return () => { editor.dispose(); original.dispose(); modified.dispose() }
  }, [path, before, after])
  return <div className="editor" dir="ltr" ref={host} />
}
