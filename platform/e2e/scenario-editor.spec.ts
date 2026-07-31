import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

/**
 * WP-G3：想定編輯器（`/scenario-editor`）。
 *
 * **這條在守什麼**：想定是整場演習的輸入。這一頁最貴的壞法不是「存不進去」（那會跳錯誤），
 * 而是「存進去了但**存錯了**」——名稱存了、單位沒跟著存；或存得進去卻**讀不回來**
 * （`?load=` 這條路徑一壞，作者永遠只能新建、不能續編，而畫面不會說任何話）。
 * 故這條走完整 roundtrip：畫面編輯 → POST /scenarios → 由 API 確認伺服器真的收到 →
 * 再用 `?load=<id>` 把它讀回編輯器，斷言欄位與 ORBAT 都還原。
 */
const CORE = 'http://localhost:8100'

interface SavedScenario {
  id: string
  name: string
  version: string
}

async function apiToken(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${CORE}/api/v1/auth/login`, {
    data: { username: 'commander', password: 'exercise' },
  })
  expect(res.ok(), '登入失敗').toBeTruthy()
  return (await res.json()).access_token as string
}

/** 整頁載入後一律等水合——Vue 接手前的輸入會被水合打回原值（症狀是「填了沒反應」）。 */
async function gotoHydrated(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
}

/** 已登入時再開 /login 會被導回 lobby（找不到帳號欄），故登入與開頁分成兩個函式。 */
async function openEditor(page: Page, query = ''): Promise<void> {
  await gotoHydrated(page, `/scenario-editor${query}`)
  await expect(page.getByTestId('scenario-editor')).toBeVisible({ timeout: 20_000 })
}

async function loginToEditor(page: Page, query = ''): Promise<void> {
  await gotoHydrated(page, '/login')
  await page.getByTestId('username').fill('commander')
  await page.getByTestId('password').fill('exercise')
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
  await openEditor(page, query)
}

test('編輯 → 存到伺服器 → 以 ?load= 讀回來：名稱、版本與 ORBAT 單位都還原', async ({
  page,
  request,
}) => {
  const stamp = Date.now()
  const name = `E2E 想定 ${stamp}`
  const designation = `E2E-步一營-${stamp}`

  await loginToEditor(page)

  // 新開的編輯器：兩個預設陣營（BLUE/RED）、零單位——空狀態要說得出「按 ＋ 新增」。
  await expect(page.getByTestId('orbat-empty')).toBeVisible()
  await expect(page.getByTestId('orbat-faction')).toHaveCount(2)

  await page.getByTestId('sc-name').fill(name)
  await page.getByTestId('sc-version').fill('2.0')

  // 在第一個陣營（BLUE）底下加一個單位並命名。
  await page.getByTestId('add-unit-faction').first().click()
  await expect(page.getByTestId('orbat-empty')).toHaveCount(0)
  await page.getByTestId('unit-designation').first().fill(designation)

  await page.getByTestId('sc-save').click()
  await expect(page.getByTestId('sc-save-status')).toContainText(`已存到伺服器：${name} v2.0`)

  // 伺服器端確認（不信畫面自己說的那句「已存」）。
  const token = await apiToken(request)
  const listRes = await request.get(`${CORE}/api/v1/scenarios`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(listRes.ok()).toBeTruthy()
  const saved = ((await listRes.json()) as SavedScenario[]).find((s) => s.name === name)
  expect(saved, `伺服器上找不到剛存的想定「${name}」`).toBeTruthy()

  // ——真正要守的：續編路徑。讀回來的必須是剛存的那一份，不是一個新的空白想定。
  await openEditor(page, `?load=${encodeURIComponent(saved!.id)}`)
  await expect(page.getByTestId('sc-load-error')).toHaveCount(0)
  await expect(page.getByTestId('sc-name')).toHaveValue(name, { timeout: 20_000 })
  await expect(page.getByTestId('sc-version')).toHaveValue('2.0')
  await expect(page.getByTestId('unit-designation')).toHaveCount(1)
  await expect(page.getByTestId('unit-designation').first()).toHaveValue(designation)

  // 收尾：刪掉這份測試想定，別讓 lobby 的想定下拉愈跑愈長。
  const del = await request.delete(`${CORE}/api/v1/scenarios/${saved!.id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(del.status()).toBe(204)
})

test('匯出／匯入 roundtrip：匯出的 JSON 貼回去，名稱與 ORBAT 一字不差地還原', async ({ page }) => {
  // 匯出/匯入是想定在機器之間搬運的唯一途徑（air-gapped）。
  // 壞法是靜默的：匯出時漏掉某個欄位 → 匯入端拿到一份「看起來一樣但少了東西」的想定。
  await loginToEditor(page)

  const stamp = Date.now()
  const name = `E2E 往返 ${stamp}`
  const designation = `E2E-砲兵連-${stamp}`
  await page.getByTestId('sc-name').fill(name)
  await page.getByTestId('add-unit-faction').first().click()
  await page.getByTestId('unit-designation').first().fill(designation)

  const exported = await page.getByTestId('export-text').inputValue()
  expect(exported).toContain(name)
  expect(exported).toContain(designation)

  // 把編輯器改壞，再用匯出的 JSON 蓋回去——還原不了就代表匯出漏了東西。
  await page.getByTestId('sc-name').fill('被覆寫掉的名稱')
  await page.getByTestId('remove-unit').first().click()
  await expect(page.getByTestId('orbat-empty')).toBeVisible()

  await page.getByTestId('import-text').fill(exported)
  await page.getByTestId('do-import').click()
  await expect(page.getByTestId('import-error')).toHaveCount(0)
  await expect(page.getByTestId('sc-name')).toHaveValue(name)
  await expect(page.getByTestId('unit-designation')).toHaveCount(1)
  await expect(page.getByTestId('unit-designation').first()).toHaveValue(designation)
})
