import { test, expect, type Page } from '@playwright/test'

// O4.4 驗收（SPEC §13.2/§13.3）：單位/contact 渲染、fog of war 三級、OFFLINE 虛影、500 單位 FPS。

async function gotoCop(page: Page, query = ''): Promise<void> {
  await page.goto('/login')
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await page.goto(`/session/e2e-units/cop${query}`)
  await expect(page.getByTestId('map-canvas')).toHaveAttribute('data-map-loaded', 'true', {
    timeout: 20_000,
  })
}

function unitState(page: Page) {
  return page.evaluate(() => {
    interface F { properties: { icon: string; opacity: number; kind: string } }
    const m = (window as unknown as {
      __matsoMap: { querySourceFeatures(id: string): F[]; hasImage(k: string): boolean }
    }).__matsoMap
    const feats = m.querySourceFeatures('units')
    const contacts = feats.filter((f) => f.properties.kind === 'contact')
    const own = feats.filter((f) => f.properties.kind === 'own')
    return {
      total: feats.length,
      ownCount: own.length,
      contactIcons: [...new Set(contacts.map((f) => f.properties.icon))],
      minOpacity: feats.length ? Math.min(...feats.map((f) => f.properties.opacity)) : 1,
    }
  })
}

/** GeoJSON 源 setData 後 tiling 為非同步 → 輪詢至 querySourceFeatures 有值。 */
async function waitUnits(page: Page): Promise<Awaited<ReturnType<typeof unitState>>> {
  await expect.poll(async () => (await unitState(page)).total, { timeout: 15_000 }).toBeGreaterThan(0)
  return unitState(page)
}

test('單位與 contact 渲染於地圖', async ({ page }) => {
  await gotoCop(page, '?units=20')
  const s = await waitUnits(page)
  expect(s.ownCount).toBeGreaterThan(0)
  expect(s.total).toBeGreaterThan(20) // 己方 + 3 contact
})

test('fog of war + N 方：contact 依情報等級與陣營產生相異符號', async ({ page }) => {
  await gotoCop(page, '?demo=1')
  const s = await waitUnits(page)
  // DETECTED / CLASSIFIED / IDENTIFIED(RED,敵) / IDENTIFIED(YELLOW,中立) → 4 個相異 icon
  // （SIDC affiliation H vs N + faction 顏色不同 → icon key 不同，§12.1/O6.10）。
  expect(s.contactIcons.length).toBe(4)
})

test('OFFLINE 己方單位為淡化虛影', async ({ page }) => {
  await gotoCop(page, '?demo=1')
  const s = await waitUnits(page)
  expect(s.minOpacity).toBeLessThan(0.5) // OFFLINE 虛影 opacity 0.4
})

test('500 單位 render FPS（量測）', async ({ page }) => {
  await gotoCop(page, '?units=500')
  await expect
    .poll(async () => (await unitState(page)).ownCount, { timeout: 15_000 })
    .toBeGreaterThan(300)

  const fps = await page.evaluate(async () => {
    const m = (window as unknown as {
      __matsoMap: {
        on(e: string, cb: () => void): void
        off(e: string, cb: () => void): void
        easeTo(o: Record<string, unknown>): void
      }
    }).__matsoMap
    let renders = 0
    const onRender = () => renders++
    m.on('render', onRender)
    m.easeTo({ center: [121.5, 24.1], zoom: 8, duration: 2000 })
    await new Promise((r) => setTimeout(r, 2100))
    m.off('render', onRender)
    return renders / 2 // renders per second
  })
  console.log(`[O4.4] 500-unit render FPS (headless): ${fps.toFixed(1)}`)
  expect(fps).toBeGreaterThan(30)
})
