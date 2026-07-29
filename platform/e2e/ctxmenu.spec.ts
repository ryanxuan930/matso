import { test, expect, type Page } from '@playwright/test'

// WP-G1b：右鍵選單（#3 ATAK 式移動/攻擊）原本零 e2e 覆蓋。抽成 MapContextMenu 子元件時
// 沒有任何自動化能證明「行為零變更」，故補這一條——選我方單位 → 右鍵地圖 → 選單出現且有「移動到這裡」。
async function loginToCop(page: Page): Promise<void> {
  await page.goto('/login')
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await page.goto('/session/e2e-orders/cop')
  await expect(page.getByTestId('map-canvas')).toHaveAttribute('data-map-loaded', 'true', {
    timeout: 20_000,
  })
}

test('右鍵選單：選我方單位後右鍵地圖 → 出現「移動到這裡」', async ({ page }) => {
  await loginToCop(page)
  await page.getByTestId('unit-item').first().click()
  const canvas = page.getByTestId('map-canvas')
  await canvas.click({ button: 'right', position: { x: 400, y: 300 } })
  await expect(page.getByTestId('ctx-menu')).toBeVisible()
  await expect(page.getByTestId('ctx-move-here')).toBeVisible()
})
