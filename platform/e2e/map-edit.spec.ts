import { test, expect, type Page } from '@playwright/test'

/**
 * WP-G3：COP 的地圖編輯／整形（`MapEditorPanel` + `useMapEditor`）。
 *
 * **與既有兩支的分工**：
 *   - `map.spec.ts` 守地圖**基座**（WebGL 初始化、縮放平移、hex 圖層、離線底圖）；
 *   - `ctxmenu.spec.ts` 守右鍵選單**出得來**（選我方單位 → 右鍵 → 有「移動到這裡」）；
 *   - 本支補**尚未涵蓋的那一半**：畫一個標註出來 → 存到後端 → 選取 → 改屬性 → 存 →
 *     重新整理仍在 → 刪得掉。也就是「標繪」這條資料鏈本身。
 *
 * **這條在守什麼**：地圖標註是白軍與各軍共用的作戰圖語言（禁射區、障礙、控制措施）。
 * 它的招牌壞法是「畫出來了、清單也有一列，但沒有 POST 出去」——重整之後整張圖白掉，
 * 而畫的當下毫無異狀。故這裡每一步的最終斷言都在 **reload 之後**。
 *
 * 另外守一條真的踩過的坑：整形預設是**鎖定**的（避免誤觸拖歪既有標繪），
 * 面板要說得出「為什麼拖不動」並給得出解鎖鈕。
 */
async function loginToCop(page: Page): Promise<void> {
  await page.goto('/login')
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await page.goto('/session/e2e-orders/cop')
  await waitCopReady(page)
}

/** 地圖初始化完成即代表 Vue 已接手（MapLibre 是 client-only 掛載的）。 */
async function waitCopReady(page: Page): Promise<void> {
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await expect(page.getByTestId('map-canvas')).toHaveAttribute('data-map-loaded', 'true', {
    timeout: 20_000,
  })
}

/**
 * 「地圖編輯」小工具預設關閉（defaultWidgets），需由工具選單開啟。兩個細節：
 *   1. **圖層小工具要先收掉**——它預設停靠左欄，而地圖編輯器是浮在 left:3.5rem 的視窗，
 *      兩者重疊時左欄會攔截點擊（症狀是 click 逾時，不是「找不到元素」，很難認）。
 *   2. 選單的 backdrop 會擋住地圖點擊，開完必須關掉選單。
 * 小工具開關存在 localStorage，重整後會保留 → 這個函式做成**冪等**的，重整後再呼叫不會反向切換。
 */
async function openMapEditor(page: Page): Promise<void> {
  const editor = page.getByTestId('map-editor')
  const layersOpen = (await page.getByTestId('toggle-hex').count()) > 0
  const editorClosed = (await editor.count()) === 0
  if (layersOpen || editorClosed) {
    await page.getByTestId('nav-widgets').click()
    await expect(page.getByTestId('widget-menu')).toBeVisible()
    if (layersOpen) await page.getByTestId('widget-toggle-layers').click()
    if (editorClosed) await page.getByTestId('widget-toggle-mapedit').click()
    await page.locator('.wm-backdrop').click()
    await expect(page.getByTestId('widget-menu')).toBeHidden()
  }
  await expect(editor).toBeVisible()
  await expect(page.getByTestId('toggle-hex')).toHaveCount(0)
}

test('標繪：畫點 → 改名 → 重新整理仍在 → 刪得掉', async ({ page }) => {
  await loginToCop(page)
  await openMapEditor(page)

  const before = await page.getByTestId('feature-row').count()
  const label = `E2E 標繪 ${Date.now()}`
  const renamed = `${label}（已改名）`

  // 屬性要**在按下形狀鈕之前**填——按下之後整排屬性欄會被「完成／取消」取代。
  await page.getByTestId('draw-kind').selectOption('ANNOTATION')
  await page.getByTestId('draw-label').fill(label)
  await page.getByTestId('draw-point').click()
  // x 要避開左側的編輯器面板（left 3.5rem、寬 14rem）與左側停靠欄。
  await page.getByTestId('map-canvas').click({ position: { x: 600, y: 300 } })

  const row = page.getByTestId('feature-row').filter({ hasText: label })
  await expect(row).toHaveCount(1, { timeout: 15_000 })
  await expect(page.getByTestId('feature-row')).toHaveCount(before + 1)
  // #92 歸屬徽章：統裁畫的是「共同」層（全體可見），不是掛在某一軍名下。
  await expect(row.getByTestId('feature-owner')).toHaveText('共同')

  // 選取 → 屬性編輯出現；整形預設鎖定，且要說得出為什麼、給得出解鎖鈕。
  await row.click()
  await expect(page.getByTestId('feature-edit')).toBeVisible()
  await expect(page.getByTestId('reshape-locked')).toContainText('形狀已鎖定')
  await expect(page.getByTestId('reshape-hint')).toHaveCount(0)
  await page.getByTestId('reshape-unlock').click()
  // 點狀標註的操作說明與線/面不同（點只能整個拖走，沒有控制點可拉）。
  await expect(page.getByTestId('reshape-hint')).toContainText('直接拖曳圖示可移動位置')
  await expect(page.getByTestId('reshape-locked')).toHaveCount(0)

  // 改名 → 存 → 清單那一列真的換了字（不是只有輸入框裡的字變了）。
  await page.getByTestId('edit-feat-label').fill(renamed)
  await page.getByTestId('save-feat-edit').click()
  await expect(page.getByTestId('feature-row').filter({ hasText: renamed })).toHaveCount(1, {
    timeout: 15_000,
  })

  // ——真正的斷言：重新整理之後它還在，而且是改過名的那一份（＝PATCH 真的到了後端）。
  await page.reload()
  await waitCopReady(page)
  await openMapEditor(page)
  const persisted = page.getByTestId('feature-row').filter({ hasText: renamed })
  await expect(persisted).toHaveCount(1, { timeout: 15_000 })

  // 刪除 → 清單少一列，重新整理後也不會復活。
  await persisted.getByTestId('feature-delete').click()
  await expect(page.getByTestId('feature-row').filter({ hasText: renamed })).toHaveCount(0, {
    timeout: 15_000,
  })
  await expect(page.getByTestId('feature-row')).toHaveCount(before)
})

test('繪製中可取消：按取消後回到工具列，且不會留下半成品', async ({ page }) => {
  await loginToCop(page)
  await openMapEditor(page)
  const before = await page.getByTestId('feature-row').count()

  await page.getByTestId('draw-polygon').click()
  await expect(page.getByTestId('draw-cancel')).toBeVisible()
  await page.getByTestId('map-canvas').click({ position: { x: 600, y: 260 } })
  await page.getByTestId('map-canvas').click({ position: { x: 660, y: 320 } })

  // 只有兩個控制點的面在後端會被當成空集合（火力裁決完全讀不到），所以這裡取消掉。
  await page.getByTestId('draw-cancel').click()
  await expect(page.getByTestId('draw-polygon')).toBeVisible()
  await expect(page.getByTestId('feature-row')).toHaveCount(before)
})
