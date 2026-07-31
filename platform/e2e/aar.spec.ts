import { readFile } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'

/**
 * WP-G3：AAR 儀表板（`/session/{id}/aar`）。
 *
 * **與 aar-replay.spec.ts 的分工**：那一支守「重播地圖真的把單位畫出來 + 時間軸拖得動」，
 * 本支補**另一半**——統計、部隊狀況表、AI 敘事的引用查核、匯出。
 *
 * 這幾條在守什麼：檢討會當場打開 AAR，若統計與敘事各讀各的帳本（例如敘事用了另一份
 * 事件來源），畫面上會是兩個互相矛盾的數字而沒有任何錯誤——所以這裡**交叉比對**
 * 統計端點的「總事件」與敘事端點寫的「共 N 起事件」必須相等，而不是各自斷言「有顯示」。
 *
 * ⚠ 覆蓋率的實話：e2e 種子局（e2e-orders）**沒有跑過 tick**，帳本是空的（下令只發 WS，
 * 不落帳）。故統計那半是「空帳本」情境；部隊狀況表那半才是真資料（5 個種子單位）。
 */
/** 整頁載入後一律等水合（見其他 spec 的說明：SSR 內容早於 Vue 接手）。 */
async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

async function loginToAar(page: Page): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await gotoHydrated(page, '/session/e2e-orders/aar')
  // 後端彙整/敘事可能耗時 → 先等載入動畫收掉，否則量到的是骨架不是資料。
  await expect(page.getByTestId('aar-loading')).toHaveCount(0, { timeout: 30_000 })
}

test('統計與 AI 敘事讀的是同一本帳：總事件數兩處相符，引用查核為全部有效', async ({ page }) => {
  await loginToAar(page)

  const stats = page.getByTestId('aar-stats')
  await expect(stats).toBeVisible()
  const statsText = (await stats.innerText()).replace(/\s/g, '')
  const total = statsText.match(/總事件：(\d+)/)?.[1]
  expect(total, `統計區沒有可解析的「總事件」：${statsText}`).toBeDefined()
  // 統計的其他欄位也要真的算出數字（不是 NaN%／undefined）。
  expect(statsText).toMatch(/交戰次數：\d+/)
  expect(statsText).toMatch(/命中率：\d+%/)
  expect(statsText).toMatch(/護欄攔截：\d+/)

  // 敘事報告由另一個端點（/aar/report）產出——它寫的事件數必須與統計一致。
  const report = page.getByTestId('aar-report')
  await expect(report).toBeVisible()
  await expect(report).toContainText(`本場推演共 ${total} 起事件`)
  // 引用查核：帳本查無的引用要被抓出來。空帳本＝沒有引用＝全部有效。
  await expect(page.getByTestId('citation-verdict')).toContainText('全部有效')
  await expect(page.getByTestId('citation-warning')).toHaveCount(0)
})

test('部隊狀況表列出全部 5 個種子單位，含番號/陣營/圖上與否', async ({ page }) => {
  await loginToAar(page)

  // 地圖只表達得了位置；「這是哪一支部隊、當時剩多少」只有這張表答得出來。
  const roster = page.getByTestId('replay-roster')
  await expect(page.getByTestId('roster-row')).toHaveCount(5, { timeout: 20_000 })
  for (const desig of ['B1', 'B2', 'B3', 'ARTY', 'R1']) {
    await expect(roster).toContainText(desig)
  }
  await expect(roster).toContainText('BLUE')
  await expect(roster).toContainText('RED')
  // 種子單位都有初始座標 → 每一列都畫得到圖上（「無位置紀錄」一個都不該有）。
  await expect(roster).not.toContainText('無位置紀錄')
})

test('匯出 JSON：帶 Bearer 打到 API、真的下載得到可解析的帳本', async ({ page }) => {
  await loginToAar(page)

  // 這條同時蓋住兩個典型壞法：相對路徑打到 Nuxt 自己、以及瀏覽器導覽不帶 Authorization。
  // 兩種壞法都會下載到一份錯誤頁而不是帳本。
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('export-json').click(),
  ])
  expect(download.suggestedFilename()).toBe('aar-e2e-orders.json')
  const parsed: unknown = JSON.parse(await readFile(await download.path(), 'utf-8'))
  expect(Array.isArray(parsed), '匯出內容不是事件陣列').toBe(true)
})
