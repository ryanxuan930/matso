// 火力計畫（WP-C10.3）——純 API 包裝 + 顯示用標籤。
//
// **不做任何過濾**：計畫的陣營可見性在後端判（紅線 3），這裡拿到什麼就顯示什麼。
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type FirePlanView = components['schemas']['FirePlanView']
export type FirePlanTargetView = components['schemas']['FirePlanTargetView']
export type FireSchedule = components['schemas']['FireSchedule']

export const SCHEDULE_LABELS: Record<string, string> = {
  AT_TICK: '定時',
  ON_CALL: '待命',
}

/** `FIRED` 只代表「令送出去了」，不代表打中——所以標籤是「已下令」不是「已命中」。 */
export const TARGET_STATUS_LABELS: Record<string, string> = {
  PENDING: '待命',
  FIRED: '已下令',
  FAILED: '未能執行',
  SKIPPED: '已略過',
}

export const fetchFirePlans = (sessionId: string) =>
  apiFetch<FirePlanView[]>(`/sessions/${sessionId}/fire-plans`)

export interface NewFireTarget {
  label?: string
  target_lat: number
  target_lng: number
  rounds: number
  shooter_unit_id: string
  schedule: FireSchedule
  at_tick?: number | null
  fire_request_id?: string | null
}

export const createFirePlan = (sessionId: string, name: string, targets: NewFireTarget[]) =>
  apiFetch<FirePlanView>(`/sessions/${sessionId}/fire-plans`, {
    method: 'POST',
    body: { name, targets },
  })

export const deleteFirePlan = (sessionId: string, planId: string) =>
  apiFetch<unknown>(`/sessions/${sessionId}/fire-plans/${planId}`, { method: 'DELETE' })

export const fireFirePlanTarget = (sessionId: string, planId: string, targetId: string) =>
  apiFetch<FirePlanTargetView>(
    `/sessions/${sessionId}/fire-plans/${planId}/targets/${targetId}/fire`,
    { method: 'POST' },
  )
