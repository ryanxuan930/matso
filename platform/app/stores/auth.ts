import { defineStore } from 'pinia'
import type { components } from '~/types/api'
import { apiFetch, apiLogin, useAuthTokens } from '~/composables/useApi'

type CurrentUser = components['schemas']['CurrentUser']

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const { access, refresh } = useAuthTokens()

  const isAuthenticated = computed(() => !!access.value)

  /** 登入：取 token 對 → 存 cookie → 拉當前使用者。 */
  async function login(username: string, password: string): Promise<void> {
    const pair = await apiLogin(username, password)
    access.value = pair.access_token
    refresh.value = pair.refresh_token
    await fetchMe()
  }

  /** 以現有 access token 拉當前使用者（重整後回填 user）。 */
  async function fetchMe(): Promise<void> {
    if (!access.value) {
      user.value = null
      return
    }
    user.value = await apiFetch<CurrentUser>('/auth/me')
  }

  /**
   * 登出：**先請後端撤銷 refresh token**，再清本地。
   *
   * ⚠ 只清本地是不夠的（WP-E2 之前就是那樣）：refresh token 沒被撤銷，
   * 撿到它的人照樣能一直換發新的 access。撤銷失敗仍照清本地——
   * 使用者按了登出就該登出，後端連不上不該把他困在已登入狀態。
   */
  async function logout(): Promise<void> {
    const token = refresh.value
    if (token) {
      try {
        await apiFetch('/auth/logout', { method: 'POST', body: { refresh_token: token } })
      }
      catch {
        // 已過期/後端不可用——照樣清本地。
      }
    }
    user.value = null
    access.value = null
    refresh.value = null
  }

  return { user, isAuthenticated, login, fetchMe, logout }
})
