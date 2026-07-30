import { readFile } from 'node:fs/promises'
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

// 演習面板（WP-B1c）：銷毀入口、歸檔封包下載、稽核留痕。
//
// **一律走真後端**（playwright.config 的 e2e core）——這一批要抓的病全是
// 「後端有回、前端沒接」與「前端自己接錯 origin/漏帶 Bearer」，
// 用 mock 打樁只會把三條都驗成綠的。

/** playwright.config 的 CORE_PORT——API 與 Nuxt 不同 origin，正是 E3 那個坑的根源。 */
const CORE = 'http://localhost:8100'

async function apiToken(
  request: APIRequestContext,
  username = 'commander',
  password = 'exercise',
): Promise<string> {
  const res = await request.post(`${CORE}/api/v1/auth/login`, { data: { username, password } })
  expect(res.ok(), `登入失敗：${username}`).toBeTruthy()
  return (await res.json()).access_token as string
}

async function api<T>(
  request: APIRequestContext,
  token: string,
  method: 'get' | 'post' | 'patch',
  path: string,
  data?: unknown,
): Promise<T> {
  const res = await request[method](`${CORE}/api/v1${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    ...(data === undefined ? {} : { data }),
  })
  expect(res.ok(), `${method.toUpperCase()} ${path} → ${res.status()} ${await res.text()}`).toBeTruthy()
  return (await res.json()) as T
}

async function loginUi(page: Page, username = 'commander', password = 'exercise'): Promise<void> {
  await page.goto('/login')
  await expect(page.locator('[data-hydrated="true"]')).toBeAttached()
  await page.getByTestId('username').fill(username)
  await page.getByTestId('password').fill(password)
  await page.getByTestId('login-submit').click()
  await expect(page).toHaveURL(/\/lobby$/)
}

/** 切到演習分頁並展開指定名稱的演習卡。 */
async function openExercise(page: Page, name: string): Promise<void> {
  await page.getByTestId('tab-exercises').click()
  const card = page.getByTestId('exercise-item').filter({ hasText: name })
  await expect(card).toBeVisible()
  await card.locator('.ex-hd').click()
  await expect(card.getByTestId('exercise-audit')).toBeVisible()
}

test('稽核軌跡顯示「誰做的」，勾稽/簽證/階段都帶時間與經手人', async ({ page }) => {
  // 抓的病：ExerciseAuditEntry.actor_id / ExerciseChecklistItem.done_at,done_by /
  // SealView.sealed_at,sealed_by,current_hash / ExerciseView.phase_changed_at
  // ——後端全都有回，前端一個都沒讀，於是稽核軌跡答不出稽核最主要的問題（誰）。
  await loginUi(page)
  const name = `稽核留痕演習 ${Date.now()}`
  await page.getByTestId('tab-exercises').click()
  await page.getByTestId('new-exercise-name').fill(name)
  await page.getByTestId('create-exercise').click()

  const card = page.getByTestId('exercise-item').filter({ hasText: name })
  await expect(card).toBeVisible()
  await card.locator('.ex-hd').click()

  // ExerciseView.phase_changed_at：新建的演習還沒推過階段（顯示 —），但建立者要看得到。
  await expect(card.getByTestId('exercise-meta')).toContainText('commander')
  await expect(card.getByTestId('phase-changed-at')).toContainText('進入「整備」')

  // ExerciseAuditEntry.actor_id：建立事件必須指名 commander，而不是只有時間 + 動作。
  await expect(card.getByTestId('exercise-audit')).toContainText('建立演習')
  await expect(card.getByTestId('exercise-audit')).toContainText('commander')

  // ExerciseChecklistItem.done_at / done_by：勾完要看得出誰在何時勾的。
  await card.getByTestId('checklist-prep_meeting_1').check()
  const done = card.getByTestId('checklist-done-prep_meeting_1')
  await expect(done).toContainText('commander')
  await expect(done).toContainText(new Date().getFullYear().toString())

  // SealView.sealed_at / sealed_by / current_hash：不符時只有簽證雜湊前 12 碼，
  // 「誰簽的、何時簽的、現在是多少」全看不到 → 出事時無從判斷是誰改了什麼。
  await card.getByTestId('seal-params').click()
  await expect(card.getByTestId('seal-detail')).toContainText('commander')
  await expect(card.getByTestId('seal-current-hash')).toBeVisible()

  // 銷毀入口**不得**在非 ARCHIVED 階段出現（這張卡還在整備）。
  await expect(card.getByTestId('exercise-destroy')).toHaveCount(0)
  await expect(card.getByTestId('destroy-open')).toHaveCount(0)

  // 收尾：解除簽證，別把全域參數鎖留給後面的測試。
  await card.getByTestId('unseal-params').click()
  await expect(card.getByTestId('seal-params')).toBeVisible()
})

test('歸檔封包以帶 Bearer 的請求下載，後端真的收到（稽核多一筆匯出）', async ({ page }) => {
  // 抓的病：舊做法是 <a href="/api/v1/exercises/{id}/bundle" target="_blank">，
  // (1) 相對路徑打到 Nuxt 自己（:3100）而不是 API；(2) 瀏覽器導覽不帶 Authorization → 401。
  // 兩種壞法都會讓後端收不到請求，於是**稽核不會多出 BUNDLE_EXPORTED 那一筆**——
  // 這條斷言同時蓋住兩個坑，而且不是「前端自己畫一個下載成功」。
  await loginUi(page)
  const name = `封包下載演習 ${Date.now()}`
  await page.getByTestId('tab-exercises').click()
  await page.getByTestId('new-exercise-name').fill(name)
  await page.getByTestId('create-exercise').click()
  await openExercise(page, name)

  const card = page.getByTestId('exercise-item').filter({ hasText: name })
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    card.getByTestId('download-bundle').click(),
  ])
  expect(download.suggestedFilename()).toMatch(/^exercise-.+\.json$/)

  // 內容要是真的封包，不是錯誤頁或空白。
  const bundle = JSON.parse(await readFile(await download.path(), 'utf-8'))
  expect(bundle).toHaveProperty('content_hash')
  expect(bundle).toHaveProperty('sessions')

  await expect(card.getByTestId('exercise-audit')).toContainText('匯出歸檔封包')
})

test('已撤收後 ADMIN 才有銷毀入口，且必須逐字輸入演習名稱', async ({ page, request }) => {
  // 抓的病：階段說明寫著「可執行銷毀模式」、稽核標籤也備好了 DATA_DESTROYED、
  // 後端 POST /exercises/{id}/destroy 早就實作，但 destroy/confirm_name 在前端零命中——
  // 推到已撤收後整個面板找不到入口。
  const director = await apiToken(request)

  // 銷毀限 ADMIN（後端第一道閘門；白軍/統裁都不行），故另備一個 ADMIN 帳號。
  // 已存在（前一次跑剩下）就沿用。
  const admin = { username: 'e2e-destroyer', password: 'destroy-me-1234' }
  const created = await request.post(`${CORE}/api/v1/users`, {
    headers: { Authorization: `Bearer ${director}` },
    data: { ...admin, role: 'ADMIN' },
  })
  expect([201, 409]).toContain(created.status())

  // 推階段/勾稽限白軍（ADMIN 刻意排除），故由統裁把演習推到 ARCHIVED。
  const name = `銷毀模式演習 ${Date.now()}`
  const ex = await api<{ id: string }>(request, director, 'post', '/exercises', { name })
  // 掛一局進來，才驗得出 DestroyResult 真的刪到東西（不是回一個 0 局的空殼）。
  const session = await api<{ id: string }>(request, director, 'post', '/sessions', {
    name: `銷毀模式陪葬局 ${Date.now()}`,
  })
  await api(request, director, 'post', `/exercises/${ex.id}/sessions`, {
    session_id: session.id,
    session_role: 'REHEARSAL',
  })
  for (const key of ['prep_meeting_1', 'prep_meeting_2', 'prep_meeting_3', 'scenario_published']) {
    await api(request, director, 'patch', `/exercises/${ex.id}/checklist/${key}`, { done: true })
  }
  await api(request, director, 'patch', `/exercises/${ex.id}/phase`, { phase: 'REHEARSAL' })
  for (const key of ['rehearsal_done', 'params_sealed']) {
    await api(request, director, 'patch', `/exercises/${ex.id}/checklist/${key}`, { done: true })
  }
  for (const phase of ['EXECUTION', 'REVIEW', 'ARCHIVED']) {
    await api(request, director, 'patch', `/exercises/${ex.id}/phase`, { phase })
  }

  await loginUi(page, admin.username, admin.password)
  await openExercise(page, name)
  const card = page.getByTestId('exercise-item').filter({ hasText: name })

  await card.getByTestId('destroy-open').click()
  // 名稱不符 → 送不出去。二次確認若只是「再按一次是」，那不是確認。
  await card.getByTestId('destroy-confirm-name').fill(`${name} `)
  await expect(card.getByTestId('destroy-submit')).toBeDisabled()

  await card.getByTestId('destroy-confirm-name').fill(name)
  await expect(card.getByTestId('destroy-submit')).toBeEnabled()
  await card.getByTestId('destroy-submit').click()

  // DestroyResult.*：刪了幾局、清了幾個活狀態鍵、各表幾筆——不可逆的操作要當場交代刪了什麼。
  const result = card.getByTestId('destroy-result')
  await expect(result).toContainText('已銷毀 1 局')
  await expect(result).toContainText('WargameSession')

  // 演習專案與稽核軌跡刻意保留：「這場演習存在過、被誰在何時銷毀」正是稽核要留的東西。
  await expect(card.getByTestId('exercise-audit')).toContainText('銷毀推演資料')
  await expect(card.getByTestId('exercise-audit')).toContainText(admin.username)
})
