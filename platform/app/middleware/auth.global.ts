/**
 * 全域路由守衛：未認證存取受保護頁 → 導向 /login；已認證存取 /login → 導向 /lobby。
 * token 存於 cookie（SSR 可讀）；faction-scope 的真正強制在後端（前端過濾不可信，SPEC §12）。
 */
const PUBLIC_ROUTES = new Set(['/login', '/help'])

export default defineNuxtRouteMiddleware((to) => {
  const { access } = useAuthTokens()
  const authed = !!access.value
  const isPublic = PUBLIC_ROUTES.has(to.path) || to.meta.public === true

  if (!authed && !isPublic) {
    return navigateTo('/login')
  }
  if (authed && to.path === '/login') {
    return navigateTo('/lobby')
  }
})
