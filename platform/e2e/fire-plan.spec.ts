import { test, expect, type Page } from '@playwright/test'

// WP-C10.3 火力計畫：預劃目標 → on-call 呼叫 → FIRE_MISSION 令落地。
//
// **走的是真後端**（e2e core），所以這條同時驗證了「on-call 沒有繞過 OrderService」——
// 令真的出現在指令列，而不是前端自己畫一個「已下令」。

async function openCop(page: Page): Promise<void> {
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

/** 開火力計畫小工具（預設關閉，從工具選單勾）。 */
async function openFirePlanWidget(page: Page): Promise<void> {
  await page.getByTestId('nav-widgets').click()
  await page.getByTestId('widget-toggle-fireplan').click()
  // 收掉工具選單——它的 backdrop 蓋住整頁（連開選單的按鈕本身都蓋住），
  // 不關的話後續每一次點擊都會被它吃掉。點 backdrop 是它自己的關閉路徑。
  await page.locator('.wm-backdrop').click()
  await expect(page.getByTestId('widget-menu')).toBeHidden()
  await expect(page.getByTestId('fireplan-list')).toBeVisible()
}

/** 在下令面板把火力任務落點點出來——火力計畫的落點就取自這裡（刻意共用一套互動）。 */
async function pickAimPoint(page: Page): Promise<void> {
  await page.getByTestId('unit-item').filter({ hasText: 'ARTY' }).click()
  await page.getByTestId('order-type').selectOption('FIRE_MISSION')
  await page.getByTestId('pick-fire-point').click()
  await page.getByTestId('map-canvas').click({ position: { x: 420, y: 320 } })
  await expect(page.getByTestId('fire-point')).toContainText('🎯')
}

test('建立火力計畫 → 呼叫待命目標 → FIRE_MISSION 令真的落地', async ({ page }) => {
  await openCop(page)
  await pickAimPoint(page)
  await openFirePlanWidget(page)

  await page.getByTestId('fireplan-name').fill('攻擊準備射擊')
  await page.getByTestId('fireplan-shooter').selectOption({ label: 'ARTY' })
  await page.getByTestId('fireplan-schedule').selectOption('ON_CALL')

  // 沒有目標不能建計畫——一份空的火力計畫沒有意義。
  await expect(page.getByTestId('fireplan-create')).toBeDisabled()
  await page.getByTestId('fireplan-add-target').click()
  await expect(page.getByTestId('fireplan-draft')).toContainText('AB1000')
  await expect(page.getByTestId('fireplan-create')).toBeEnabled()

  await page.getByTestId('fireplan-create').click()
  await expect(page.getByTestId('fireplan-item')).toContainText('攻擊準備射擊')
  await expect(page.getByTestId('fireplan-target')).toContainText('待命')

  // on-call 呼叫 → 目標轉「已下令」，且指令列真的多一道火力任務。
  await page.getByTestId('fire-on-call').first().click()
  await expect(page.getByTestId('fireplan-target').first()).toContainText('已下令')
  await expect(page.getByTestId('order-list')).toContainText('火力任務')
})

test('火力計畫面板一定看得到誤傷警語', async ({ page }) => {
  await openCop(page)
  await openFirePlanWidget(page)
  await expect(page.getByText('敵我皆受損')).toBeVisible()
})
