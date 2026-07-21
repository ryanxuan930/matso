import { test, expect, type Page } from '@playwright/test'

// O4.1 驗收（SPEC §12/§13.1）：登入→lobby、錯誤密碼被拒、token refresh 自動換發。
// 種子使用者 commander/exercise（EXERCISE_DIRECTOR）由 playwright.config 的 core webServer 建立。

/** 前往頁面並等待 Vue 水合完成（避免在事件處理器附掛前互動 → 誤觸原生表單送出）。 */
async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

async function login(page: Page, username = 'commander', password = 'exercise'): Promise<void> {
  await page.getByTestId('username').fill(username)
  await page.getByTestId('password').fill(password)
  await page.getByTestId('login-submit').click()
}

test('登入成功後導向 lobby 並顯示使用者', async ({ page }) => {
  await gotoHydrated(page, '/login')
  await login(page)

  await expect(page).toHaveURL(/\/lobby$/)
  await expect(page.getByTestId('current-user')).toContainText('commander')
  await expect(page.getByTestId('current-user')).toContainText('EXERCISE_DIRECTOR')
})

test('錯誤密碼被拒，留在登入頁', async ({ page }) => {
  await gotoHydrated(page, '/login')
  await login(page, 'commander', 'wrong-password')

  await expect(page.getByTestId('login-error')).toContainText('帳號或密碼錯誤')
  await expect(page).toHaveURL(/\/login$/)
})

test('未登入存取 lobby 被導回 login', async ({ page }) => {
  await page.goto('/lobby')
  await expect(page).toHaveURL(/\/login$/)
})

test('建立推演後出現在列表', async ({ page }) => {
  await gotoHydrated(page, '/login')
  await login(page)
  await expect(page).toHaveURL(/\/lobby$/)

  const name = `煙硝演習 ${Date.now()}`
  await page.getByTestId('new-session-name').fill(name)
  await page.getByTestId('create-session').click()

  await expect(page.getByTestId('session-list')).toContainText(name)
})

test('access token 過期後自動 refresh，操作仍成功', async ({ page }) => {
  await gotoHydrated(page, '/login')
  await login(page)
  await expect(page).toHaveURL(/\/lobby$/)

  // access TTL=3s（見 config）；等待逾期，隨後的 API 呼叫應以 refresh token 自動換發。
  await page.waitForTimeout(4000)

  const name = `逾期後建局 ${Date.now()}`
  await page.getByTestId('new-session-name').fill(name)
  await page.getByTestId('create-session').click()

  // 若 refresh 未生效，建局會 401 失敗、列表不會出現此名。
  await expect(page.getByTestId('session-list')).toContainText(name)
})
