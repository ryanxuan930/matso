import { test, expect, type Page } from '@playwright/test'

// O4.5 驗收（SPEC §13.4）：下 MOVE/ENGAGE 令全流程——選單位 → 指令面板 → precheck 顯示 →
// pending 出現 → 取消。E2E core 以 SEED_SESSION 建 e2e-orders（3 藍軍）+ STUB_GATEWAY（precheck 可行）。

async function loginToOrdersCop(page: Page): Promise<void> {
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

test('單位列表載入真單位', async ({ page }) => {
  await loginToOrdersCop(page)
  await expect(page.getByTestId('unit-item')).toHaveCount(3) // 3 藍軍
})

test('下 MOVE 令全流程：選單位 → 點地圖 → precheck 可行 → pending → 取消', async ({ page }) => {
  await loginToOrdersCop(page)
  await page.getByTestId('unit-item').first().click()
  await expect(page.getByTestId('order-panel')).toBeVisible()

  // MOVE：設目標點（點地圖）
  await page.getByTestId('pick-dest').click()
  const canvas = page.getByTestId('map-canvas')
  await canvas.click({ position: { x: 400, y: 300 } })
  await expect(page.getByTestId('dest-h3')).not.toHaveText('未設目標')

  // 送出 → precheck 可行（stub gateway）
  await page.getByTestId('submit-order').click()
  await expect(page.getByTestId('precheck')).toContainText('可行')

  // pending 列表出現此指令
  await expect(page.getByTestId('order-row')).toContainText('MOVE')

  // 取消
  await page.getByTestId('cancel-order').first().click()
  await expect(page.getByTestId('order-row')).toContainText('CANCELLED')
})

test('下 ENGAGE 令：選單位 → 選目標 → precheck 可行', async ({ page }) => {
  await loginToOrdersCop(page)
  await page.getByTestId('unit-item').first().click()
  await page.getByTestId('order-type').selectOption('ENGAGE')
  await page.getByTestId('engage-target').selectOption({ index: 1 }) // 第一個可選目標
  await page.getByTestId('submit-order').click()
  await expect(page.getByTestId('precheck')).toContainText('可行')
  await expect(page.getByTestId('order-list')).toContainText('ENGAGE')
})
