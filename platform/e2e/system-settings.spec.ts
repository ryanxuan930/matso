import { test, expect, type Page } from '@playwright/test'

/**
 * WP-G3：系統設定（`/system-settings`）。
 *
 * **這條在守什麼**：
 *   1. 可編輯區（AI 模式 / LLM 後端）存的是 DB、全系統適用。「按了儲存、顯示已儲存、
 *      其實沒寫進去」在這一頁特別致命——白軍以為切到本機模型了，戰場 COP 還在往雲端送。
 *      故斷言在**重新載入之後**。
 *   2. 唯讀區顯示的必須是**後端真的在用的值**，不是前端寫死的字串。E2E core 是以
 *      `STUB_GATEWAY=1` 起的，所以那一格必須顯示「啟用（E2E）」——寫死的話這條會紅。
 *   3. 雲端後端的資料外送警示要跟著 Base URL 走（機敏兵推誤送雲端是不可逆的事故）。
 */
/** 整頁載入後一律等水合——SSR 畫得出表單，但 Vue 接手前的輸入會被水合打回原值。 */
async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

async function loginToSettings(page: Page): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await gotoHydrated(page, '/system-settings')
  await expect(page.getByTestId('ai-settings')).toBeVisible({ timeout: 20_000 })
}

test('LLM 後端設定：改了 model 存檔，重新載入仍是新值', async ({ page }) => {
  await loginToSettings(page)
  const modelInput = page.getByTestId('llm-model')
  const original = await modelInput.inputValue()

  const probe = `e2e-model-${Date.now()}`
  await modelInput.fill(probe)
  await page.getByTestId('save-settings').click()
  await expect(page.getByTestId('save-msg')).toHaveText('已儲存')
  await expect(page.getByTestId('settings-err')).toHaveCount(0)

  // ——真正的斷言：重新載入（＝重新 GET /system/config）之後值還在。
  await page.reload()
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await expect(page.getByTestId('ai-settings')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('llm-model')).toHaveValue(probe)

  // 收尾：還原，別把測試值留給別的推演用。
  await page.getByTestId('llm-model').fill(original)
  await page.getByTestId('save-settings').click()
  await expect(page.getByTestId('save-msg')).toHaveText('已儲存')
})

test('唯讀系統資訊顯示後端實際組態（E2E core 的 stub gateway 為啟用）', async ({ page }) => {
  await loginToSettings(page)
  const info = page.getByTestId('system-info')
  await expect(info).toBeVisible()
  // playwright.config 以 STUB_GATEWAY=1 起 core：這一格若寫死或沒接後端，這條就紅。
  await expect(info).toContainText('啟用（E2E）')
  // Redis 也是由 ENV 決定；顯示得出真正的連線字串才代表這一區真的在讀後端。
  await expect(info).toContainText('redis://')

  // 推演參數區：數字要真的從契約預設帶進來（空白/NaN 代表 sim 那段沒接上）。
  await expect(page.getByTestId('sim-params')).toBeVisible()
  await expect(page.getByTestId('sim-foot-xc')).not.toHaveValue('')
  await expect(page.getByTestId('sim-resupply')).not.toHaveValue('')
})

test('填入雲端 LLM 位址時出現資料外送警示，改回本機即消失', async ({ page }) => {
  await loginToSettings(page)
  // 不儲存，只驗提示——這是「送出去之前」的最後一道人為把關。
  await page.getByTestId('preset-google').click()
  await expect(page.getByTestId('egress-warn')).toContainText('戰場 COP')

  await page.getByTestId('preset-ollama').click()
  await expect(page.getByTestId('egress-warn')).toHaveCount(0)
})
