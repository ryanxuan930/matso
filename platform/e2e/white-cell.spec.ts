import { test, expect, type Page } from '@playwright/test'

/**
 * WP-G3：白軍控制台（`/session/{id}/white-cell`）原本零 e2e 覆蓋。
 *
 * **這幾條在守什麼**——統裁在這一頁做的事壞掉時，症狀全是無聲的：
 *   1. 視角切換若沒真的過濾，統裁以為自己在看「藍軍看得到什麼」，其實看的是全知畫面
 *      （於是把敵情當成藍軍已知的情資去導演）。過濾只能在後端（紅線 #3），
 *      所以這條斷言的是**單位筆數真的變少、敵軍番號真的不見了**，不是選單能不能選。
 *   2. 按了暫停但別的席位不知道 → 各參謀只會以為系統掛了。故這條跨兩個分頁驗：
 *      白軍按暫停 → **COP 那一頁**冒出暫停橫幅；續行 → 橫幅消失。
 *   3. 沒開跑的局沒有快照點：回溯目標必須剛好是既有快照 tick，選單若給得出東西
 *      就是在誘導統裁送出必然失敗（或更糟：回滾到 tick 0）的請求。
 *
 * ⚠ **本頁在 e2e 下有一個時序陷阱（不是本卡要修的東西，但會影響測試怎麼寫）**：
 * playwright.config 刻意把 access TTL 設成 3 秒（為了測 refresh）。本頁 `onMounted`
 * 會**併發**發出五個 API 呼叫；若頁面載入超過 3 秒，它們會同時撞到過期、同時拿
 * **同一枚** refresh token 去換發，而後端 refresh token 是輪替的（換一次舊的立即失效）
 * → 只有一個贏，其餘被判「token 已過期」，畫面上就是「一個單位都讀不到」。
 * 對策是**先把路由編譯暖起來、再登入、再開頁**（見 `openWhiteCell`），讓掛載發生在
 * token 還新鮮的時候。**不放寬斷言**：改成「0 筆也算過」的話，後端整個掛掉時這條也會是綠的。
 */
const WC_PATH = '/session/e2e-orders/white-cell'
const COP_PATH = '/session/e2e-orders/cop'

async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

/** 先載一次目標路由讓 dev server 完成編譯（未登入會被導回 /login，正合我意）。 */
async function warmRoute(page: Page, path: string): Promise<void> {
  await page.goto(path).catch(() => undefined)
}

async function login(page: Page): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
}

/** 開白軍控制台，並確保**單位真的載進來了**才回（重試上限 2 次，仍讀不到就讓它紅）。 */
async function openWhiteCell(page: Page): Promise<void> {
  await warmRoute(page, WC_PATH)
  for (let attempt = 0; attempt < 2; attempt++) {
    await login(page)
    await gotoHydrated(page, WC_PATH)
    await expect(page.getByTestId('white-cell-console')).toBeVisible()
    try {
      await expect(page.getByTestId('wc-unit-item').first()).toBeVisible({ timeout: 8_000 })
      return
    } catch {
      // 撞到上述 refresh 競態；重登再開一次（路由已編譯，這次會快很多）。
    }
  }
  await expect(page.getByTestId('wc-unit-item').first()).toBeVisible()
}

test('視角切換：全知 5 個單位，切 BLUE 視角剩 4 個且敵軍 R1 消失', async ({ page }) => {
  await openWhiteCell(page)

  // 種子（SEED_SESSION）＝ B1/B2/B3/ARTY（BLUE）+ R1（RED）＝ 5。
  await expect(page.getByTestId('wc-unit-item')).toHaveCount(5)
  await expect(page.getByTestId('unit-count')).toHaveText('5 單位')
  await expect(page.getByTestId('wc-unit-list')).toContainText('R1')

  // BLUE 視角：R1 不在 visible_factions 內 → **後端根本不回**這筆。
  // 這裡刻意斷言筆數與番號兩件事：只驗 count 的話，前端若改成「畫面上藏起來」也會綠。
  await page.getByTestId('viewpoint').selectOption('BLUE')
  await expect(page.getByTestId('wc-unit-item')).toHaveCount(4)
  await expect(page.getByTestId('unit-count')).toHaveText('4 單位')
  await expect(page.getByTestId('wc-unit-list')).not.toContainText('R1')
  await expect(page.getByTestId('wc-unit-list')).toContainText('B1')
  await expect(page.getByTestId('wc-unit-list')).toContainText('ARTY')
})

test('時間控制：白軍按暫停 → COP 出現暫停橫幅；續行 → 橫幅消失', async ({ page, context }) => {
  await warmRoute(page, COP_PATH)
  await warmRoute(page, WC_PATH)
  await login(page)

  // COP 先連上串流再操作——暫停橫幅只認**本連線期間**收到的 SESSION_CONTROL
  // （後端對新客戶端不補送 ring，見 cop.vue 的說明），先按再開會什麼都看不到。
  await gotoHydrated(page, COP_PATH)
  await expect(page.getByTestId('ws-status')).toContainText('即時連線', { timeout: 20_000 })

  const wc = await context.newPage() // 同 context ⇒ 共用登入 cookie
  await gotoHydrated(wc, WC_PATH)
  await expect(wc.getByTestId('wc-stream-status')).toContainText('即時連線', { timeout: 20_000 })

  await wc.getByTestId('pause').click()
  await expect(wc.getByTestId('wc-status')).toContainText('已送出 PAUSE')
  // 白軍自己的事件流：走 Redis → broadcaster → WS → 中文敘述器（與 COP 同一支）。
  await expect(wc.getByTestId('wc-event-list')).toContainText('白軍時間控制', { timeout: 15_000 })
  // 真正要守的是這一行：**別席位真的知道時鐘停了**。
  await expect(page.getByTestId('pause-banner')).toContainText('推演已暫停（白軍）', {
    timeout: 15_000,
  })

  await wc.getByTestId('resume').click()
  await expect(wc.getByTestId('wc-status')).toContainText('已送出 RESUME')
  await expect(page.getByTestId('pause-banner')).toHaveCount(0, { timeout: 15_000 })
  await wc.close()
})

test('空狀態：未開跑的局無快照點、無待命注入，回溯鈕不可按', async ({ page }) => {
  // 覆蓋率的實話：e2e 種子局從未起跑，故快照與 MSEL 本來就沒有內容。
  // 這條驗的是「沒有內容時說得清楚且不給誤按」，不是硬造資料。
  // ⚠ 先確認單位載得到（openWhiteCell 保證）——否則整頁 API 全掛時「什麼都沒有」
  //   也會讓下面三條通過，那就是一條假綠燈。
  await openWhiteCell(page)

  await expect(page.getByTestId('rollback-tick')).toContainText('（尚無快照點）')
  await expect(page.getByTestId('rollback')).toBeDisabled()
  await expect(page.getByTestId('wc-msel-pending')).toContainText('（無待命注入')
})
