import { test, expect, type Page } from '@playwright/test'

/**
 * WP-G3：自主推演主控台（`/session/{id}/autonomy`）。
 *
 * **這條在守什麼**：這一頁的內容（任務敘述、逐條任務目標）會**逐字進入 AI 指揮官的提示詞**，
 * 而它存在 Redis、不在畫面上——所以「按了儲存、畫面顯示已儲存、其實沒存進去」是這一頁
 * 最典型也最看不出來的壞法：白軍以為自己交代了任務，AI 拿到的是空的。
 * 故這條的重點不是「按得下去」，而是**重新載入之後那些字還在**。
 *
 * 順帶守住一個真的發生過的坑：任務目標的 `extra` 結構欄位在存檔時被吃掉、
 * 以及「全部取消勾選＝清除指派」走的是 DELETE 而不是 PUT 一個空表。
 */
/** 整頁載入後一律等水合——Vue 接手前的勾選/輸入會被水合打回原值（症狀是「按了沒反應」）。 */
async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

async function loginToAutonomy(page: Page): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await gotoHydrated(page, '/session/e2e-orders/autonomy')
  await expect(page.getByTestId('ai-BLUE')).toBeVisible({ timeout: 20_000 })
}

async function reloadHydrated(page: Page): Promise<void> {
  await page.reload()
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await expect(page.getByTestId('ai-BLUE')).toBeVisible({ timeout: 20_000 })
}

/**
 * 把本局的自主設定歸零。
 *
 * ⚠ **非做不可**：自主設定存在 Redis，而 e2e 的 Redis（db 15）**不會**隨每次跑測試重建
 * （只有 sqlite 會被砍掉重建），session id 又是固定的 `e2e-orders`。前一輪若中途失敗、
 * 沒跑到收尾，殘留的指派就會讓「沒被指派的陣營不該被打開」這條在下一輪莫名其妙變紅。
 */
async function resetAutonomy(page: Page): Promise<void> {
  await page.getByTestId('ai-BLUE').uncheck()
  await page.getByTestId('ai-RED').uncheck()
  await page.getByTestId('save-autonomy').click()
  await expect(page.getByTestId('autonomy-save-msg')).toContainText('已清除自主指派')
  await reloadHydrated(page)
}

test('指派 AI 陣營 + 任務敘述 + 任務目標：存得進去，重新載入讀得回來', async ({ page }) => {
  await loginToAutonomy(page)

  // 陣營清單由本局單位導出（BLUE/RED 各有單位）——不是寫死的兩個常數。
  await expect(page.getByTestId('ai-RED')).toBeVisible()
  await resetAutonomy(page)

  const mission = `肅清當面之敵 ${Date.now()}`
  const objective = `奪取並確保 218 高地 ${Date.now()}`

  await page.getByTestId('ai-RED').check()
  await page.getByTestId('ai-mission-RED').fill(mission)
  await page.getByTestId('obj-add-RED').click()
  await page.getByTestId('obj-RED-0').fill(objective)
  await page.getByTestId('ai-heartbeat').fill('60')

  await page.getByTestId('save-autonomy').click()
  await expect(page.getByTestId('autonomy-save-msg')).toContainText('已儲存並啟動')
  await expect(page.getByTestId('autonomy-save-msg')).toContainText('RED')
  await expect(page.getByTestId('autonomy-err')).toHaveCount(0)

  // ——真正的斷言：重新載入後，後端（Redis）把同樣的字還回來。
  await reloadHydrated(page)
  await expect(page.getByTestId('ai-RED')).toBeChecked({ timeout: 20_000 })
  await expect(page.getByTestId('ai-mission-RED')).toHaveValue(mission)
  await expect(page.getByTestId('obj-RED-0')).toHaveValue(objective)
  await expect(page.getByTestId('ai-heartbeat')).toHaveValue('60')
  // 沒被指派的陣營不該被順手打開。
  await expect(page.getByTestId('ai-BLUE')).not.toBeChecked()

  // ——收尾兼第二個斷言：全部取消 ⇒ 清除指派（走 DELETE），且重載後真的空了。
  await page.getByTestId('ai-RED').uncheck()
  await page.getByTestId('save-autonomy').click()
  await expect(page.getByTestId('autonomy-save-msg')).toContainText('已清除自主指派')
  await reloadHydrated(page)
  await expect(page.getByTestId('ai-RED')).not.toBeChecked({ timeout: 20_000 })
  await expect(page.getByTestId('ai-mission-RED')).toHaveValue('')
})

test('對照實驗開關（AI ground truth）預設關閉，且不會被一般儲存靜默清掉', async ({ page }) => {
  // 這個開關關掉 AI 的戰場迷霧——後端 pydantic 對未帶的欄位補 false，
  // 前端每次 PUT 若漏帶，白軍每按一次儲存就把實驗設定清掉，而畫面上毫無跡象。
  await loginToAutonomy(page)
  await resetAutonomy(page)
  await expect(page.getByTestId('ai-ground-truth')).not.toBeChecked()

  await page.getByTestId('ai-ground-truth').check()
  await page.getByTestId('ai-BLUE').check()
  await page.getByTestId('save-autonomy').click()
  await expect(page.getByTestId('autonomy-save-msg')).toContainText('已儲存並啟動')

  await reloadHydrated(page)
  await expect(page.getByTestId('ai-ground-truth')).toBeChecked({ timeout: 20_000 })

  // 收尾：清掉指派，別把「AI 接管 BLUE」留給後面的測試。
  await page.getByTestId('ai-ground-truth').uncheck()
  await page.getByTestId('ai-BLUE').uncheck()
  await page.getByTestId('save-autonomy').click()
  await expect(page.getByTestId('autonomy-save-msg')).toContainText('已清除自主指派')
})
