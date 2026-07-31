import { test, expect, type Page } from '@playwright/test'

/**
 * WP-G3：裝備範本庫（`/armory`）。
 *
 * **這條在守什麼**：範本的 `base_stats` 就是兵推的物理輸入（射程、彈種、命中率）。
 * 這一頁的招牌壞法是「表單填了、存了、顯示成功，但結構化欄位沒被寫進 base_stats」
 * ——武器範本看起來建好了，實際上射程還是預設值，而兵推照樣跑、照樣出結果。
 * 故這條不只驗「清單多一列」，而是**存完之後重新讀回來，射程/彈種還是我填的那個值**。
 */
/**
 * ⚠ 每一次**整頁載入**（goto/reload）都要等水合：SSR 已經把表單畫出來了，
 * 但在 Vue 接手之前操作下拉，選了之後會被水合打回原值——症狀是「選了沒反應」，
 * 而測試看起來只是「找不到某個欄位」，很難連回真正的原因。
 */
async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

async function loginToArmory(page: Page): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await gotoHydrated(page, '/armory')
  await expect(page.getByTestId('armory-editor')).toBeVisible({ timeout: 20_000 })
}

test('新增 KINETIC 範本：存得進去、重新整理讀得回來、刪得掉', async ({ page }) => {
  await loginToArmory(page)

  // 種子已建立機動/武器範本（seed_session_equipment），清單本來就不該是空的。
  const items = page.getByTestId('armory-item')
  await expect(items.first()).toBeVisible({ timeout: 20_000 })
  const before = await items.count()

  const name = `E2E 測試機槍 ${Date.now()}`
  await page.getByTestId('armory-new').click()
  await page.getByTestId('armory-name').fill(name)
  await page.getByTestId('armory-category').selectOption('KINETIC')
  await page.getByTestId('armory-maxrange').fill('1234')
  await page.getByTestId('armory-ammo').fill('AMMO_E2E')
  await page.getByTestId('armory-save').click()

  await expect(page.getByTestId('toast')).toContainText(`已儲存：${name}`)
  await expect(items).toHaveCount(before + 1)

  // ——真正的斷言：重新整理（＝重新從後端讀）之後，我填的數字還在。
  await page.reload()
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await expect(page.getByTestId('armory-editor')).toBeVisible({ timeout: 20_000 })
  const mine = page.getByTestId('armory-item').filter({ hasText: name })
  await expect(mine).toHaveCount(1)
  await mine.click()
  await expect(page.getByTestId('armory-name')).toHaveValue(name)
  await expect(page.getByTestId('armory-maxrange')).toHaveValue('1234')
  await expect(page.getByTestId('armory-ammo')).toHaveValue('AMMO_E2E')

  // 刪除（二次確認）→ 清單真的少一列，不是只在畫面上藏起來。
  await mine.getByTestId('armory-delete').click()
  await expect(page.getByTestId('armory-delete-modal')).toBeVisible()
  await page.getByTestId('armory-delete-confirm').click()
  await expect(page.getByTestId('armory-item').filter({ hasText: name })).toHaveCount(0)
  await expect(page.getByTestId('armory-item')).toHaveCount(before)
})

test('切換類別會換掉表單欄位：飛彈有導引/彈頭，動能武器沒有', async ({ page }) => {
  // 類別是決定裁決走哪條路的欄位。若切了類別但表單沒換，作者就會用步槍的欄位建飛彈，
  // 存出一份缺導引/彈頭的 MISSILE——引擎讀得到、但打起來完全不是飛彈。
  await loginToArmory(page)
  await page.getByTestId('armory-new').click()

  await expect(page.getByTestId('armory-kinetic-kind')).toBeVisible()
  await expect(page.getByTestId('armory-guidance')).toHaveCount(0)

  await page.getByTestId('armory-category').selectOption('MISSILE')
  await expect(page.getByTestId('armory-missile-kind')).toBeVisible()
  await expect(page.getByTestId('armory-guidance')).toBeVisible()
  await expect(page.getByTestId('armory-warhead')).toBeVisible()
  await expect(page.getByTestId('armory-kinetic-kind')).toHaveCount(0)
})
