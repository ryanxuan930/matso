import { test, expect, type Page } from '@playwright/test'

// O4.2 驗收（SPEC §13.2）：COP 地圖基座——可平移縮放、hex 層開關、離線可用。
// tileUrl 預設空 → 地圖純離線渲染（背景+經緯網格+hex），不打任何外部瓦片來源。

async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

async function loginToCop(page: Page): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await page.goto('/session/e2e-map/cop')
  // 等 MapLibre 在 headless WebGL 初始化完成
  await expect(page.getByTestId('map-canvas')).toHaveAttribute('data-map-loaded', 'true', {
    timeout: 20_000,
  })
}

// 讀取暴露的地圖實例狀態
function mapState(page: Page) {
  return page.evaluate(() => {
    interface MapLike {
      getZoom(): number
      getCenter(): { lng: number; lat: number }
      getLayoutProperty(layer: string, prop: string): string
      getSource(id: string): unknown
      querySourceFeatures(id: string): unknown[]
    }
    const m = (window as unknown as { __matsoMap: MapLike }).__matsoMap
    return {
      zoom: m.getZoom(),
      center: [m.getCenter().lng, m.getCenter().lat] as [number, number],
      hexVisible: m.getLayoutProperty('hexgrid-line', 'visibility'),
      hexFeatureCount: m.querySourceFeatures('hexgrid').length,
      hasBasemap: !!m.getSource('basemap'),
    }
  })
}

test('地圖在 headless WebGL 初始化並置中台灣', async ({ page }) => {
  await loginToCop(page)
  const s = await mapState(page)
  expect(s.zoom).toBeGreaterThan(0)
  expect(s.center[0]).toBeGreaterThan(118) // 台灣經度範圍
  expect(s.center[0]).toBeLessThan(123)
})

test('離線：無 tile server 時無底圖來源，hex 仍可計算', async ({ page }) => {
  await loginToCop(page)
  const s = await mapState(page)
  expect(s.hasBasemap).toBe(false) // tileUrl 空 → 純離線
  // 開 hex → 視野內有 H3 cell 被計算出（客戶端離線）
  await page.getByTestId('toggle-hex').check()
  await expect.poll(async () => (await mapState(page)).hexFeatureCount).toBeGreaterThan(0)
})

test('hex 層開關切換可見性', async ({ page }) => {
  await loginToCop(page)
  expect((await mapState(page)).hexVisible).toBe('none')
  await page.getByTestId('toggle-hex').check()
  await expect.poll(async () => (await mapState(page)).hexVisible).toBe('visible')
  await page.getByTestId('toggle-hex').uncheck()
  await expect.poll(async () => (await mapState(page)).hexVisible).toBe('none')
})

test('地圖可縮放與平移', async ({ page }) => {
  await loginToCop(page)
  const before = await mapState(page)

  // 縮放：雙擊放大
  const canvas = page.getByTestId('map-canvas')
  await canvas.dblclick({ position: { x: 300, y: 200 } })
  await expect.poll(async () => (await mapState(page)).zoom).toBeGreaterThan(before.zoom)

  // 平移：拖曳畫布
  const box = (await canvas.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 - 150, box.y + box.height / 2, { steps: 8 })
  await page.mouse.up()
  await expect
    .poll(async () => (await mapState(page)).center[0])
    .not.toBe(before.center[0])
})

test('從 lobby 點擊 session 進入 COP 地圖', async ({ page }) => {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)

  await page.getByTestId('new-session-name').fill(`地圖局 ${Date.now()}`)
  await page.getByTestId('create-session').click()
  await page.getByTestId('session-item').first().click()

  await expect(page).toHaveURL(/\/session\/.+\/cop$/)
  await expect(page.getByTestId('map-canvas')).toHaveAttribute('data-map-loaded', 'true', {
    timeout: 20_000,
  })
})
