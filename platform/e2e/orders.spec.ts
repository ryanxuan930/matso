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
  // 冷啟時 Nuxt 要先編譯這一頁、快照 API 也還沒回來——預設 5 秒不夠，
  // 之前這條就是這樣紅的（Received: 0，不是數量錯）。
  await expect(page.getByTestId('unit-item')).toHaveCount(5, { timeout: 20_000 })
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

  // pending 列表出現此指令（列表容器；e2e-orders session 跨測試共用，可能有多筆）
  // 指令列顯示的是**中文標籤**（ORDER_TYPE_LABELS）——斷言生的 enum 值一直是紅的。
  await expect(page.getByTestId('order-list')).toContainText('移動')

  // 取消 → 出現 CANCELLED
  await page.getByTestId('cancel-order').first().click()
  await expect(page.getByTestId('order-list')).toContainText('已取消')
})

test('下 ENGAGE 令：選單位 → 選目標 → precheck 可行', async ({ page }) => {
  await loginToOrdersCop(page)
  await page.getByTestId('unit-item').first().click()
  await page.getByTestId('order-type').selectOption('ENGAGE')
  await page.getByTestId('engage-target').selectOption({ label: 'R1' }) // 第一個可選目標
  await page.getByTestId('submit-order').click()
  await expect(page.getByTestId('precheck')).toContainText('可行')
  await expect(page.getByTestId('order-list')).toContainText('交戰')
})

// WP-C10.2 面目標射擊：打座標而非打單位。砲兵 ARTY 持 155 榴（曲射），故預檢可過。
test('下 FIRE_MISSION 令：選砲兵 → 點地圖設落點 → precheck 可行', async ({ page }) => {
  await loginToOrdersCop(page)
  await page.getByTestId('unit-item').filter({ hasText: 'ARTY' }).click()
  await page.getByTestId('order-type').selectOption('FIRE_MISSION')

  // 未設落點 → 送出鈕停用（沒有目標座標的火力任務不成立）。
  await expect(page.getByTestId('submit-order')).toBeDisabled()

  await page.getByTestId('pick-fire-point').click()
  await page.getByTestId('map-canvas').click({ position: { x: 400, y: 300 } })
  await expect(page.getByTestId('fire-point')).toContainText('🎯')
  await expect(page.getByTestId('submit-order')).toBeEnabled()

  // 讀數對了不代表地圖上畫得出來——準星要真的被繪製，操作員才看得見自己要打哪裡。
  // queryRenderedFeatures 只回「已繪製」的特徵（見 aar-replay.spec 的同一道理）。
  await expect
    .poll(
      () =>
        page.evaluate(() => {
          const m = (window as unknown as { __matsoMap?: maplibregl.Map }).__matsoMap
          return m ? m.queryRenderedFeatures({ layers: ['fire-aim-ring'] }).length : 0
        }),
      { timeout: 10_000, message: '地圖上沒有畫出面射擊準星' },
    )
    .toBeGreaterThan(0)

  await page.getByTestId('submit-order').click()
  await expect(page.getByTestId('precheck')).toContainText('可行')
  await expect(page.getByTestId('order-list')).toContainText('火力任務')
})

test('面射擊的誤傷警語一定看得到——這不是可選的提示', async ({ page }) => {
  await loginToOrdersCop(page)
  await page.getByTestId('unit-item').filter({ hasText: 'ARTY' }).click()
  await page.getByTestId('order-type').selectOption('FIRE_MISSION')
  await expect(page.getByTestId('fire-danger')).toContainText('敵我皆受損')
})
