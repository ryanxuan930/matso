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

/** 階段標籤 + 一句話說明（徽章的 tooltip）。 */
export const EXERCISE_PHASE_LABELS: Record<string, { text: string; hint: string }> = {
  PREP: { text: '整備', hint: '整備會議、想定發佈、系統飽和測試' },
  REHEARSAL: { text: '預推', hint: '預推演練；參數於此期間簽證鎖定（WP-B4）' },
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
