import { test, expect, type Page } from '@playwright/test'

// WP-D6.1：AAR 地圖重播。
//
// **這條非寫不可**：in-app 瀏覽器 harness 把頁面回報為 `document.visibilityState === 'hidden'`，
// 瀏覽器會暫停隱藏頁的 requestAnimationFrame，而 MapLibre 的 render loop 完全靠 rAF——
// 於是地圖在 harness 裡**永遠是空白的**，除非剛好有東西強制合成（例如截圖）。
// 實測：閒置時 rAF 觸發 0 次，截一張圖後立刻變 4 次。
// 也就是說 harness 完全無法判斷「地圖有沒有畫出來」。只有真 Playwright 算數。
async function loginToAar(page: Page): Promise<void> {
  await page.goto('/login')
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await page.goto('/session/e2e-orders/aar')
  await expect(page.getByTestId('aar-timeline')).toBeVisible({ timeout: 30_000 })
}

test('AAR 重播地圖：單位符號實際畫在畫布上（非只是資料進來）', async ({ page }) => {
  await loginToAar(page)

  const map = page.getByTestId('replay-map').locator('[data-testid=map-canvas]')
  await expect(map).toHaveAttribute('data-map-loaded', 'true', { timeout: 30_000 })

  // 光看 source 有資料不夠——symbol 要經過 placement 才會真的出現在畫面上。
  // queryRenderedFeatures 只回「已繪製」的特徵，正是 harness 裡恆為 0 的那個數字。
  await expect
    .poll(
      () =>
        page.evaluate(() => {
          const m = (window as unknown as { __matsoMap?: maplibregl.Map }).__matsoMap
          return m ? m.queryRenderedFeatures({ layers: ['units'] }).length : 0
        }),
      { timeout: 20_000, message: '重播地圖沒有畫出任何單位符號' },
    )
    .toBeGreaterThan(0)
})

test('AAR 重播：拖時間軸改變畫面，書籤可跳轉', async ({ page }) => {
  await loginToAar(page)
  const scrub = page.getByTestId('scrub')
  const max = Number(await scrub.getAttribute('max'))

  await expect(page.getByTestId('replay-tick')).toContainText('tick 0')
  if (max > 0) {
    await scrub.fill(String(max))
    await expect(page.getByTestId('replay-tick')).toContainText(`tick ${max}`)
  }

  // 書籤跳轉（有事件的局才有書籤；沒有就略過，不假裝測到）。
  const bookmark = page.getByTestId('bookmark').first()
  if (await bookmark.count()) {
    await bookmark.click()
    await expect(page.getByTestId('replay-tick')).not.toContainText(`tick ${max}`)
  }
})
