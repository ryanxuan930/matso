import { test, expect, type Page } from '@playwright/test'

// O4.6 M4 煙霧測試（SPEC §19）：登入 → 開局 → 下令 → 看到裁決事件（經 WS stream）。
// E2E core：SEED_SESSION（e2e-orders 3 藍軍）+ STUB_GATEWAY（precheck 可行）+ REDIS（WS 事件）。

async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

test('M4 全鏈路：登入 → lobby → COP → 下令 → 看到裁決事件', async ({ page }) => {
  // 1) 登入
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)

  // 2) 開局：進入已種子的 e2e-orders session 的 COP
  await page.goto('/session/e2e-orders/cop')
  await expect(page.getByTestId('map-canvas')).toHaveAttribute('data-map-loaded', 'true', {
    timeout: 20_000,
  })
  // WS 連上
  await expect.poll(async () => page.getByTestId('ws-status').textContent()).toContain('live')
  await expect(page.getByTestId('unit-item')).toHaveCount(4)

  // 3) 下令：ENGAGE（stub gateway → precheck 可行）
  await page.getByTestId('unit-item').first().click()
  await page.getByTestId('order-type').selectOption('ENGAGE')
  await page.getByTestId('engage-target').selectOption({ label: 'R1' })
  await page.getByTestId('submit-order').click()
  await expect(page.getByTestId('precheck')).toContainText('可行')

  // 4) 看到裁決事件（後端下令成功後發 ENGAGEMENT_RESOLVED 到 WS stream → cop 事件面板顯示）
  await expect(page.getByTestId('event-list')).toContainText('ENGAGEMENT_RESOLVED', {
    timeout: 10_000,
  })
})
