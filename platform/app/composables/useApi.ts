import type { CookieRef } from '#app'
import type { components } from '~/types/api'

type TokenPair = components['schemas']['TokenPair']
type AccessToken = components['schemas']['AccessToken']

export interface ApiError {
  status: number
  code: string
  message: string
}

interface AuthTokens {
  access: CookieRef<string | null>
  refresh: CookieRef<string | null>
}

/**
 * access / refresh token 存於 cookie（SSR + 重整皆可讀）。
 * refs 記憶在 per-request 的 nuxtApp 上，讓所有呼叫端共用同一 ref——否則登入後 `access.value=…`
 * 尚未寫回 cookie 時，另一次 useCookie 會讀到舊值（null），導致下一個請求漏帶 Bearer。
 */
export function useAuthTokens(): AuthTokens {
  const nuxtApp = useNuxtApp() as unknown as { _matsoTokens?: AuthTokens }
  if (nuxtApp._matsoTokens) return nuxtApp._matsoTokens
  nuxtApp._matsoTokens = {
    access: useCookie<string | null>('matso_access', { sameSite: 'lax', default: () => null }),
    refresh: useCookie<string | null>('matso_refresh', { sameSite: 'lax', default: () => null }),
  }
  return nuxtApp._matsoTokens
}

function apiUrl(path: string): string {
  const base = useRuntimeConfig().public.apiBase
  return `${base}/api/v1${path}`
}

/** 從 $fetch 錯誤萃取契約 Error 格式（{error:{code,message}}）。 */
function toApiError(err: unknown): ApiError {
  const e = err as { status?: number; response?: { status?: number }; data?: { error?: { code?: string; message?: string } } }
  const status = e.status ?? e.response?.status ?? 0
  return {
    status,
    code: e.data?.error?.code ?? 'NETWORK_ERROR',
    message: e.data?.error?.message ?? '無法連線至伺服器',
  }
}

/**
 * 帶 Bearer 的 API 呼叫；access 過期（401 AUTH_TOKEN_EXPIRED）時以 refresh token 自動換發並重試一次。
 * 失敗拋 ApiError（契約 code）。
 */
export async function apiFetch<T>(path: string, opts: Parameters<typeof $fetch>[1] = {}): Promise<T> {
  const { access, refresh } = useAuthTokens()

  const call = (token: string | null) =>
    $fetch<T>(apiUrl(path), {
      ...opts,
      headers: {
        ...(opts?.headers as Record<string, string> | undefined),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })

  try {
    return await call(access.value)
  } catch (err) {
    const apiErr = toApiError(err)
    // access 過期且有 refresh → 換發後重試一次
    if (apiErr.status === 401 && apiErr.code === 'AUTH_TOKEN_EXPIRED' && refresh.value) {
      try {
        const refreshed = await $fetch<AccessToken>(apiUrl('/auth/refresh'), {
          method: 'POST',
          body: { refresh_token: refresh.value },
        })
        access.value = refreshed.access_token
        return await call(access.value)
      } catch {
        access.value = null
        refresh.value = null
      }
    }
    throw apiErr
  }
}

export async function apiLogin(username: string, password: string): Promise<TokenPair> {
  return $fetch<TokenPair>(apiUrl('/auth/login'), {
    method: 'POST',
    body: { username, password },
  }).catch((err) => {
    throw toApiError(err)
  })
}
