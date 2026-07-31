import { test, expect, type Page } from '@playwright/test'

/**
 * WP-G3：帳號管理（`/accounts`）。
 *
 * **這條在守什麼**：這一頁是權限的來源。壞法有兩種、都不會噴錯：
 *   1. 建了帳號但角色沒跟著存 → 新進的觀察員拿到統裁權限（或反過來，指揮官登入後什麼都做不了）。
 *   2. 下拉改了角色、畫面也變了，但 PATCH 沒送出去 → 重整就打回原形。
 * 所以這條的斷言都在「重新從後端讀回來之後」，而不是改完當下的畫面。
 *
 * 另外守一條安全性質的 UI 約束：**不能刪除自己**（刪掉唯一的統裁＝系統沒人管得動）。
 */
/** 整頁載入後一律等水合——SSR 畫得出表單，但 Vue 接手前的操作會被水合打回原值。 */
async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

async function loginToAccounts(page: Page): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await gotoHydrated(page, '/accounts')
  await expect(page.getByTestId('user-table')).toBeVisible({ timeout: 20_000 })
}

async function reloadHydrated(page: Page): Promise<void> {
  await page.reload()
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await expect(page.getByTestId('user-table')).toBeVisible({ timeout: 20_000 })
}

test('建立帳號 → 改角色 → 刪除：每一步都以重新載入後的後端資料為準', async ({ page }) => {
  await loginToAccounts(page)

  // 種子帳號本來就在（統裁 commander）——空清單代表 /users 根本沒接上。
  await expect(page.getByTestId('user-row').filter({ hasText: 'commander' })).toHaveCount(1)

  // 用唯一帳號名做斷言而不是總筆數：spec 檔之間是平行跑的，
  // exercise-panel.spec 也會建帳號，數總數會變成一條隨機紅燈。
  const username = `e2e-observer-${Date.now()}`
  await page.getByTestId('new-username').fill(username)
  await page.getByTestId('new-password').fill('e2e-password-1234')
  await page.getByTestId('new-role').selectOption('OBSERVER')
  await page.getByTestId('create-user-btn').click()

  await expect(page.getByTestId('accounts-err')).toHaveCount(0)
  const row = page.getByTestId('user-row').filter({ hasText: username })
  await expect(row).toHaveCount(1)
  await expect(row.getByTestId('role-select')).toHaveValue('OBSERVER')

  // 改角色 → 重新載入後仍是新角色（驗 PATCH 真的落地，不是只改了畫面）。
  await row.getByTestId('role-select').selectOption('ANALYST')
  await expect(page.getByTestId('accounts-err')).toHaveCount(0)
  await reloadHydrated(page)
  const row2 = page.getByTestId('user-row').filter({ hasText: username })
  await expect(row2.getByTestId('role-select')).toHaveValue('ANALYST')

  // 刪除（二次確認）→ 重新載入後真的不見了。
  await row2.getByTestId('delete-user').click()
  await expect(page.getByTestId('delete-modal')).toContainText(username)
  await page.getByTestId('confirm-delete-user').click()
  await expect(page.getByTestId('user-row').filter({ hasText: username })).toHaveCount(0)
  await reloadHydrated(page)
  await expect(page.getByTestId('user-row').filter({ hasText: username })).toHaveCount(0)
})

test('自己的帳號刪不掉，密碼太短建不了帳號', async ({ page }) => {
  await loginToAccounts(page)

  // 刪掉自己＝把自己鎖在系統外（若是唯一的統裁，整套就沒人管得動了）。
  const me = page.getByTestId('user-row').filter({ hasText: 'commander' })
  await expect(me.getByTestId('delete-user')).toBeDisabled()

  // 密碼 <8 → 建立鈕不可按（後端也會擋，但使用者要在按之前就知道）。
  await page.getByTestId('new-username').fill('e2e-too-short')
  await page.getByTestId('new-password').fill('short')
  await expect(page.getByTestId('create-user-btn')).toBeDisabled()
  await page.getByTestId('new-password').fill('long-enough-1234')
  await expect(page.getByTestId('create-user-btn')).toBeEnabled()
})
