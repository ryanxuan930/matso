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

  function logout(): void {
    user.value = null
    access.value = null
    refresh.value = null
  }

  return { user, isAuthenticated, login, fetchMe, logout }
})
