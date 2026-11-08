/** Fetch wrapper: sends the JWT, turns the API's {detail, code} errors into ApiError. */
const TOKEN_KEY = 'graydi_token'
const USER_KEY = 'graydi_user'

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  },
}

export class ApiError extends Error {
  status: number
  code: string
  body: Record<string, unknown>

  constructor(status: number, message: string, code: string, body: Record<string, unknown>) {
    super(message)
    this.status = status
    this.code = code
    this.body = body
  }
}

type Json = Record<string, unknown> | unknown[]

/** Called when the API answers 401 on a logged-in request (token expired). */
let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

async function request<T>(method: string, path: string, body?: Json | FormData): Promise<T> {
  const headers: Record<string, string> = {}
  const token = tokenStore.get()
  if (token) headers.Authorization = `Bearer ${token}`
  let payload: BodyInit | undefined
  if (body instanceof FormData) {
    payload = body // the browser sets the multipart boundary
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  let res: Response
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Is the backend running on port 8000?', 'network', {})
  }

  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>
    // FastAPI validation errors use detail: [{msg}]; domain errors use detail: "text"
    const detail = data.detail
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail) && detail[0] && typeof detail[0] === 'object' && 'msg' in detail[0]
          ? String((detail[0] as { msg: unknown }).msg)
          : `Request failed (${res.status})`
    if (res.status === 401 && token && path !== '/auth/login') onUnauthorized()
    throw new ApiError(res.status, message, typeof data.code === 'string' ? data.code : 'error', data)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: Json | FormData) => request<T>('POST', path, body),
  patch: <T>(path: string, body?: Json) => request<T>('PATCH', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}

export const userStore = {
  get: <T>(): T | null => {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as T) : null
  },
  set: (user: unknown) => localStorage.setItem(USER_KEY, JSON.stringify(user)),
}
