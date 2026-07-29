import type { CookieRef } from '#app'
import type { components } from '~/types/api'

type TokenPair = components['schemas']['TokenPair']

export interface ApiError {
  status: number
  code: string
  message: string
  /**
   * 契約 `Error.details`——**在此之前這一欄整個被丟掉**。
   *
   * 後端一直在 details 裡放結構化資訊（預檢逐項結果、被擋下的整備鍵、席位越權原因…），
   * 而 `toApiError` 只取 code/message，於是那些資訊在前端從來沒有到達過。
   * `useCopOrdering` 甚至已經在讀 `err.details.precheck`——它讀到的永遠是 undefined，
   * 所以下令被拒的 toast 一直只顯示一行通用訊息，逐項失敗原因從未出現過。
   */
  details?: Record<string, unknown>
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
  const e = err as {
    status?: number
    response?: { status?: number }
    data?: { error?: { code?: string; message?: string; details?: Record<string, unknown> } }
  }
  const status = e.status ?? e.response?.status ?? 0
  return {
    status,
    code: e.data?.error?.code ?? 'NETWORK_ERROR',
    message: e.data?.error?.message ?? '無法連線至伺服器',
    details: e.data?.error?.details,
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
    // access 過期且有 refresh → 換發後重試一次（滑動續期：同時更新 refresh，延長 session 視窗）
    if (apiErr.status === 401 && apiErr.code === 'AUTH_TOKEN_EXPIRED' && refresh.value) {
      try {
        const refreshed = await $fetch<TokenPair>(apiUrl('/auth/refresh'), {
          method: 'POST',
          body: { refresh_token: refresh.value },
        })
        access.value = refreshed.access_token
        refresh.value = refreshed.refresh_token
        return await call(access.value)
      } catch {
        access.value = null
        refresh.value = null
      }
    }
    throw apiErr
  }
}

/** 主動以 refresh token 換一枚新 token 對（WS 連線前確保 token 新鮮；滑動續期同時更新 refresh）。 */
export async function refreshAccessToken(): Promise<boolean> {
  const { access, refresh } = useAuthTokens()
  if (!refresh.value) return false
  try {
    const r = await $fetch<TokenPair>(apiUrl('/auth/refresh'), {
      method: 'POST',
      body: { refresh_token: refresh.value },
    })
    access.value = r.access_token
    refresh.value = r.refresh_token
    return true
  } catch {
    return false
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
