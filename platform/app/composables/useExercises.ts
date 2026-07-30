/**
 * 演習專案（WP-B1/B4）的 API 與標籤。
 *
 * 慣例照 `useParticipants.ts`：**匯出的是一組純函式 + 標籤表**，不是回傳 ref 的 `use*()` hook。
 * 這個 repo 的資料抓取都在頁面自己的 ref 裡，另立一套狀態容器只會多一個真相來源。
 *
 * **演習層是導演工具**：後端只開給白軍/統裁/管理（非全知者一律 404）。前端的角色判斷
 * 只是把入口藏起來，權威永遠在後端（紅線 3）。
 */
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type ExerciseView = components['schemas']['ExerciseView']
export type ExercisePhase = components['schemas']['ExercisePhase']
export type SessionRole = components['schemas']['SessionRole']
export type ExerciseAuditEntry = components['schemas']['ExerciseAuditEntry']
export type SealView = components['schemas']['SealView']
export type DestroyResult = components['schemas']['DestroyResult']

/** 階段標籤 + 一句話說明（徽章的 tooltip）。 */
export const EXERCISE_PHASE_LABELS: Record<string, { text: string; hint: string }> = {
  PREP: { text: '整備', hint: '整備會議、想定發佈、系統飽和測試' },
  REHEARSAL: { text: '預推', hint: '預推演練；參數於此期間簽證鎖定' },
  EXECUTION: { text: '正式實施', hint: '正式演習進行中——參數凍結' },
  REVIEW: { text: '檢討', hint: '檢討會；參數解鎖' },
  ARCHIVED: { text: '已撤收', hint: '撤收建檔完成；可執行銷毀模式' },
}

export const SESSION_ROLE_LABELS: Record<string, string> = {
  REHEARSAL: '預推',
  MAIN: '正式',
  ANALYSIS: '分析',
}

/** 階段序——推進只能沿序走一階，UI 據此決定「下一階」按鈕。 */
export const PHASE_ORDER: ExercisePhase[] = [
  'PREP',
  'REHEARSAL',
  'EXECUTION',
  'REVIEW',
  'ARCHIVED',
]

export function phaseLabel(p?: string): string {
  return (p && EXERCISE_PHASE_LABELS[p]?.text) || p || '—'
}

export function nextPhase(p: ExercisePhase): ExercisePhase | null {
  const i = PHASE_ORDER.indexOf(p)
  return i >= 0 && i + 1 < PHASE_ORDER.length ? (PHASE_ORDER[i + 1] as ExercisePhase) : null
}

export function fetchExercises(): Promise<ExerciseView[]> {
  return apiFetch<ExerciseView[]>('/exercises')
}

export function createExercise(name: string): Promise<ExerciseView> {
  return apiFetch<ExerciseView>('/exercises', { method: 'POST', body: { name } })
}

export function deleteExercise(id: string) {
  return apiFetch(`/exercises/${id}`, { method: 'DELETE' })
}

export function advancePhase(id: string, phase: ExercisePhase, note?: string) {
  return apiFetch<ExerciseView>(`/exercises/${id}/phase`, {
    method: 'PATCH',
    body: { phase, note: note || null },
  })
}

export function tickChecklist(id: string, key: string, done: boolean) {
  return apiFetch<ExerciseView>(`/exercises/${id}/checklist/${encodeURIComponent(key)}`, {
    method: 'PATCH',
    body: { done },
  })
}

export function attachSession(id: string, sessionId: string, role: SessionRole | null) {
  return apiFetch<ExerciseView>(`/exercises/${id}/sessions`, {
    method: 'POST',
    // 空字串要送 null（同 useParticipants 的 seat 陷阱）——後端的 enum 不收 ""。
    body: { session_id: sessionId, session_role: role || null },
  })
}

export function detachSession(id: string, sessionId: string) {
  return apiFetch<ExerciseView>(`/exercises/${id}/sessions/${sessionId}`, { method: 'DELETE' })
}

export function fetchAudit(id: string): Promise<ExerciseAuditEntry[]> {
  return apiFetch<ExerciseAuditEntry[]>(`/exercises/${id}/audit`)
}

/**
 * 歸檔封包下載。
 *
 * **不可用 `<a href>` 直連端點**——這裡踩過兩個坑，`useAar.ts` 的 `aarExportDownload`
 * 已經為 AAR 匯出踩過同一組並留下註解：
 *   1. 相對路徑 `/api/v1/...` 會打到 Nuxt 自己（:3000），API 在另一個 origin；
 *   2. 瀏覽器導覽不帶 `Authorization` 標頭 → 401「缺少 Token」。
 * 故一律走 `apiFetch`（帶 Bearer、會自動續 token）取回內容，再以 Blob 觸發下載。
 *
 * 副作用提醒：後端在此端點寫 `BUNDLE_EXPORTED` 稽核——呼叫端下載後要重抓稽核軌跡，
 * 否則「誰把整場演習帶走了」那一筆要重新展開才看得到。
 */
export async function downloadBundle(id: string): Promise<void> {
  const data = await apiFetch<unknown>(`/exercises/${id}/bundle`)
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  const blob = new Blob([text], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `exercise-${id}.json`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/**
 * 銷毀模式：硬刪本演習所有推演局的資料（不可逆）。
 *
 * `confirmName` **必須與演習名稱逐字相符**（後端 `DestroyExerciseDataRequest.confirm_name`）——
 * 這是後端刻意設計的第三道閘門，前端不得代填、不得放寬（trim 也不行：後端是 `!=` 直接比對，
 * 前端先 trim 只會讓按鈕可按、送出後才被拒）。
 */
export function destroyExerciseData(id: string, confirmName: string): Promise<DestroyResult> {
  return apiFetch<DestroyResult>(`/exercises/${id}/destroy`, {
    method: 'POST',
    body: { confirm_name: confirmName },
  })
}

/**
 * 牆鐘時間顯示（`2026-07-30 14:03:11`）。
 *
 * 演習層的時間戳全是**真實牆鐘**（稽核 `at`、簽證 `sealed_at`、勾稽 `done_at`、
 * 階段 `phase_changed_at`），與模擬時間是兩條軸，不可混用格式。
 */
export function fmtWallClock(iso?: string | null): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : '—'
}

/**
 * 稽核/簽證/勾稽的「誰」。
 *
 * 後端這些欄位一律只回 user id。查得到帳號就顯示帳號名——稽核軌跡最主要的問題是
 * 「誰做的」，顯示一串 uuid 等於答不出來。查不到（帳號已刪）才退回 id 前 8 碼，
 * 並以 `id:` 前綴標明那是識別碼不是名字。
 */
export function actorLabel(id: string | null | undefined, names: Record<string, string>): string {
  if (!id) return '—'
  return names[id] ?? `id:${id.slice(0, 8)}`
}

export function fetchSeal(id: string): Promise<SealView | null> {
  return apiFetch<SealView | null>(`/exercises/${id}/seal`)
}

export function sealParams(id: string): Promise<SealView> {
  return apiFetch<SealView>(`/exercises/${id}/seal`, { method: 'POST' })
}

export function unsealParams(id: string) {
  return apiFetch(`/exercises/${id}/seal`, { method: 'DELETE' })
}

/** 稽核 action → 中文。未知值原樣顯示（後端加了新動作也不會變成空白）。 */
export const AUDIT_ACTION_LABELS: Record<string, string> = {
  EXERCISE_CREATED: '建立演習',
  PHASE_ADVANCED: '階段推進',
  CHECKLIST_TICKED: '整備勾稽',
  SESSION_ATTACHED: '掛入推演局',
  SESSION_DETACHED: '卸下推演局',
  BUNDLE_EXPORTED: '匯出歸檔封包',
  DATA_DESTROYED: '銷毀推演資料',
  PARAMS_SEALED: '參數簽證',
  PARAMS_UNSEALED: '解除簽證',
}
