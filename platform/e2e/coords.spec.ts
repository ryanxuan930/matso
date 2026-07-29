import { test, expect, type Page } from '@playwright/test'

// WP-G1b：座標查詢（#10）原本零 e2e 覆蓋。抽成 CoordReadout 子元件時，
// 「點地圖 → 顯示經緯度 + MGRS」這條路徑沒有任何自動化能證明行為零變更——
// 而瀏覽器 harness 的合成 click 打不進 MapLibre 的事件層，只有真 Playwright 點擊算數。
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

test('座標查詢：開小工具後點地圖 → 顯示經緯度與 MGRS', async ({ page }) => {
  await loginToCop(page)

  // 「座標」小工具預設關閉（defaultWidgets），需由工具選單開啟。
  await page.getByTestId('nav-widgets').click()
  await expect(page.getByTestId('widget-menu')).toBeVisible()
  await page.getByTestId('widget-toggle-coords').click()

  const readout = page.getByTestId('coord-readout')
  await expect(readout).toBeVisible()
  await expect(readout).toContainText('尚未點選')

  // 工具選單的 backdrop 會擋住地圖點擊，先關掉選單。
  await page.locator('.wm-backdrop').click()
  await expect(page.getByTestId('widget-menu')).toBeHidden()

  await page.getByTestId('map-canvas').click({ position: { x: 400, y: 300 } })

  // 三列（緯度/經度/MGRS）取代「尚未點選」；緯經度為 5 位小數，MGRS 為非空字串。
  await expect(readout).not.toContainText('尚未點選')
  await expect(readout.locator('.cr-row')).toHaveCount(3)
  await expect(readout).toContainText(/緯度\s*-?\d+\.\d{5}/)
  await expect(readout).toContainText(/經度\s*-?\d+\.\d{5}/)
  await expect(page.getByTestId('coord-mgrs')).not.toBeEmpty()
})
