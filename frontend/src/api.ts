export type User = { id: string; username: string; role: 'admin' | 'user'; disabled: boolean }
export type Server = {
  id: string; name: string; host: string; port: number; username: string; auth_type: string
  public_key: string; host_key_fingerprint: string; trusted: boolean
}
export type Agent = {
  id: string; name: string; kind: string; token_hint: string; paused: boolean
  rate_limit_per_minute: number; last_seen_at: number | null; token?: string
}
export type PathRule = { path: string; mode: 'ro' | 'rw' }
export type GrantConfig = {
  level: string; paths: PathRule[]; deny_globs?: string[]; services: string[]; allowed_commands: string[]
  denied_patterns: string[]; force_sandbox: boolean; sandbox_network: boolean; allow_docker: boolean
  use_sudo_for_services: boolean; auto_approve_destructive: boolean; max_runtime_seconds: number
  max_output_kb: number; max_memory_mb: number; max_concurrent_jobs: number
}
export type Approval = {
  id: string; agent_name: string; server_name: string; tool: string; summary: string; preview: string
  status: string; reason: string; created_at: number; expires_at: number; decided_by: string
}
export type Job = {
  id: string; server_id: string; server_name: string; actor_name: string; actor_type: string; command: string
  cwd: string; status: string; exit_code: number | null; sandboxed: boolean; created_at: number; finished_at: number | null
}
export type AuditEntry = {
  seq: number; ts: number; actor_type: string; actor_name: string; server_id: string; action: string
  status: string; detail: Record<string, unknown>
}
export type LiveEvent = { type: string; ts: number; [key: string]: unknown }

const TOKEN_KEY = 'atb.session'
let memoryToken = ''

export function getToken(): string {
  try { return sessionStorage.getItem(TOKEN_KEY) ?? memoryToken } catch { return memoryToken }
}

export function setToken(token: string): void {
  memoryToken = token
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch { /* storage unavailable: keep the token in memory only */ }
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) { super(message) }
}

export async function api<T = unknown>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await response.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { /* non-JSON error page */ }
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/auth/login') {
      setToken('')
      window.dispatchEvent(new Event('atb-signed-out'))
    }
    throw new ApiError(data?.message ?? `HTTP ${response.status}`, response.status, data?.error ?? 'error')
  }
  return data as T
}

export const tool = <T = any>(name: string, args: Record<string, unknown>) =>
  api<T>(`/api/v1/tools/${name}`, 'POST', args)

export function socketUrl(path: string): string {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`
}

export function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

export const fromBase64 = (text: string): Uint8Array<ArrayBuffer> => Uint8Array.from(atob(text), (c) => c.charCodeAt(0))
