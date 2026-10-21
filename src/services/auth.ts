import { reactive } from 'vue'

export interface AuthUser { id: string; username: string; display_name?: string }
export const auth = reactive({
  user: null as AuthUser | null,
  checked: false,
  checking: false,
  serviceError: '',
  guideOpen: false,
  guideReason: 'required' as 'required' | 'expired' | 'changed',
  redirect: '/',
})
let check: Promise<void> | undefined
let generation = 0
let sessionRequests = new AbortController()
const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('justread-auth') : null

export function safeReturnPath(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')
    && !/[\\\r\n]/.test(value) && !/^\/login(?:[/?#]|$)/.test(value) ? value : '/'
}

function readUser(value: unknown): AuthUser {
  const user = (value as { user?: AuthUser } | null)?.user
  if (!user || typeof user.id !== 'string' || !user.id || typeof user.username !== 'string' || !user.username) {
    throw new Error('登录服务返回格式不正确，请联系管理员。')
  }
  return { id: user.id, username: user.username, ...(typeof user.display_name === 'string' ? { display_name: user.display_name } : {}) }
}

async function authRequest(path: string, options: RequestInit = {}) {
  try {
    return await fetch(`/api/v1/auth/${path}`, {
      ...options, credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000),
      headers: { 'Content-Type': 'application/json', ...options.headers },
    })
  } catch { throw new Error('暂时无法连接登录服务，请检查网络后重试。') }
}

async function failure(response: Response): Promise<string> {
  if ([404, 428, 501].includes(response.status)) return '登录服务尚未接入，请联系管理员。'
  if (response.status === 429) return '登录尝试过于频繁，请稍后再试。'
  if (response.status === 401) return '账号或密码不正确，请重新输入。'
  const body = await response.json().catch(() => null)
  return typeof body?.error?.message === 'string' ? body.error.message.slice(0, 300) : '登录服务暂不可用，请稍后重试。'
}

export function checkSession(force = false): Promise<void> {
  if (check) return check
  if (auth.checked && !force) return Promise.resolve()
  const version = generation
  auth.checking = true
  check = (async () => {
    try {
      const response = await authRequest('me')
      if (version !== generation) return
      if (response.status === 401) { auth.user = null; auth.serviceError = ''; return }
      if (!response.ok) throw new Error(await failure(response))
      const user = readUser(await response.json())
      if (version !== generation) return
      auth.user = user
      auth.serviceError = ''
    } catch (error) {
      if (version === generation) { auth.user = null; auth.serviceError = error instanceof Error ? error.message : '无法验证登录状态。' }
    } finally { auth.checked = true; auth.checking = false; check = undefined }
  })()
  return check
}

export function requireLogin(reason: 'required' | 'expired' | 'changed' = 'required') {
  const alreadyRequired = !auth.user && auth.guideOpen
  generation++
  sessionRequests.abort()
  sessionRequests = new AbortController()
  auth.user = null
  auth.checked = true
  auth.guideReason = reason
  auth.guideOpen = true
  if (!location.hash.startsWith('#/login')) auth.redirect = safeReturnPath(location.hash.slice(1))
  if (!alreadyRequired) window.dispatchEvent(new Event('justread:login-required'))
}

/** All application API calls go through this gate, including downloads and SSE. */
export async function authenticatedFetch(input: string, options: RequestInit = {}): Promise<Response> {
  if (!auth.user) {
    if (!location.hash.startsWith('#/login')) requireLogin()
    throw new Error('请先登录后再使用工作空间。')
  }
  const version = generation
  const signal = options.signal ? AbortSignal.any([options.signal, sessionRequests.signal]) : sessionRequests.signal
  const response = await fetch(input, { ...options, credentials: 'same-origin', signal })
  if (version !== generation) throw new DOMException('Session changed', 'AbortError')
  if (response.status === 401) {
    requireLogin('expired')
    throw new Error('登录已失效，请重新登录。')
  }
  return response
}

export async function login(username: string, password: string, redirect: unknown) {
  const response = await authRequest('login', { method: 'POST', body: JSON.stringify({ username: username.trim(), password }) })
  if (!response.ok) throw new Error(await failure(response))
  readUser(await response.json())
  // Verify the server actually issued a usable cookie before navigating.
  const verification = await authRequest('me')
  if (!verification.ok) throw new Error('登录状态未能保存，请确认浏览器允许 Cookie 后重试。')
  readUser(await verification.json())
  channel?.postMessage({ type: 'changed' })
  auth.guideOpen = false
  // A fresh app instance also clears old-account stores and background polling.
  location.replace(location.pathname + location.search + '#' + safeReturnPath(redirect))
  location.reload()
}

export async function logout() {
  const response = await authRequest('logout', { method: 'POST' })
  if (!response.ok && response.status !== 401) throw new Error(await failure(response))
  channel?.postMessage({ type: 'logout' })
  requireLogin('required')
  auth.guideOpen = false
  location.replace(location.pathname + location.search + '#/login')
  location.reload()
}

channel?.addEventListener('message', event => {
  if (event.data?.type === 'logout' || event.data?.type === 'changed') requireLogin('changed')
})

export function accountStorageKey(key: string): string {
  if (!auth.user) throw new Error('请先登录后再读取工作空间。')
  return `${key}:${encodeURIComponent(auth.user.id)}`
}
