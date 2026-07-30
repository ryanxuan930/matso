import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type UnitView = components['schemas']['UnitView']
export type WeaponView = components['schemas']['WeaponView']
export type OrderResponse = components['schemas']['OrderResponse']
type OrderRequest = components['schemas']['OrderRequest']

/**
 * 指令類型 → 中文（UI 顯示；後端 enum 值不變）。
 *
 * **必須涵蓋 `OrderType` 的每一個值**（contracts/core_api.yaml）。漏一個的下場不是報錯，
 * 而是指令列直接把英文代號印出來：下了工兵令的參謀在面板上看到的是「ENGINEER」。
 * 過去只有四個（MOVE/ENGAGE/FIRE_MISSION/POSTURE），但下令下拉早就給了七種令型
 * ——那三種（MISSION/FORMATION/ENGINEER）一送出去就變成裸英文。
 * `core/tests/unit/test_order_labels_coverage.py` 會掃契約的 enum 斷言這裡不漏。
 */
export const ORDER_TYPE_LABELS: Record<string, string> = {
  MOVE: '移動',
  ENGAGE: '交戰',
  RECON: '偵察',
  RESUPPLY: '補給',
  POSTURE: '姿態',
  FIRE_MISSION: '火力任務',
  MISSION: '任務',
  FORMATION: '隊形',
  ENGINEER: '工兵作業',
}
export const ORDER_STATUS_LABELS: Record<string, string> = {
  PENDING: '等待中',
  VALIDATED: '已驗證',
  EXECUTING: '執行中',
  COMPLETED: '完成',
  REJECTED: '拒絕',
  CANCELLED: '已取消',
}
/** 任務型 → 中文（`OrderResponse.mission_type`；與下令面板的任務型下拉同一套用語）。 */
export const MISSION_TYPE_LABELS: Record<string, string> = {
  SEIZE: '奪佔',
  DEFEND: '防守',
  SCREEN: '掩護幕',
  MOVE_MARCH: '行軍',
}
/**
 * 任務階段 → 中文（`OrderResponse.mission_phase`，每 tick 由 `mission_runtime` 評估）。
 *
 * 沒有這一層時，任務令從頭到尾只顯示「執行中」——指揮官分不出部隊是還在機動、
 * 已經接敵、還是已鞏固完畢，而那正是任務級下令唯一值得看的資訊。
 */
export const MISSION_PHASE_LABELS: Record<string, string> = {
  PLANNED: '待命',
  MOVING: '機動中',
  ENGAGING: '接戰中',
  CONSOLIDATING: '鞏固中',
  HOLDING: '固守中',
  COMPLETE: '任務完成',
  FAILED: '任務失敗',
}
export function orderTypeLabel(t?: string): string {
  return (t && ORDER_TYPE_LABELS[t]) || t || ''
}
export function orderStatusLabel(s?: string): string {
  return (s && ORDER_STATUS_LABELS[s]) || s || ''
}
export function missionTypeLabel(t?: string | null): string {
  return (t && MISSION_TYPE_LABELS[t]) || t || ''
}
export function missionPhaseLabel(p?: string | null): string {
  return (p && MISSION_PHASE_LABELS[p]) || p || ''
}

/**
 * 取 session 的 faction-scoped 單位（下令對象）。
 * `asFaction`：**僅全知角色可用**——以該陣營視角取其單位（#90）；一般角色帶他陣營→後端 403。
 * 過濾一律在後端（紅線 #3：fog of war 不在前端做）。
 */
export function fetchUnits(sessionId: string, asFaction?: string | null): Promise<UnitView[]> {
  const q = asFaction ? `?as_faction=${encodeURIComponent(asFaction)}` : ''
  return apiFetch<UnitView[]>(`/sessions/${sessionId}/units${q}`)
}

/** 取單位可用武器（ENGAGE 選武器/彈種；資料驅動 baseStats）。他方單位後端回 403。 */
export function fetchWeapons(sessionId: string, unitId: string): Promise<WeaponView[]> {
  return apiFetch<WeaponView[]>(`/sessions/${sessionId}/units/${unitId}/weapons`)
}

/** 取 session 的指令（pending + 歷史）。 */
export function fetchOrders(sessionId: string): Promise<OrderResponse[]> {
  return apiFetch<OrderResponse[]>(`/sessions/${sessionId}/orders`)
}

/**
 * 下令。回 201 OrderResponse（含 precheck）；不可行後端回 422（apiFetch 拋 ApiError，
 * details.precheck 帶各項預檢結果）。
 */
export function submitOrder(sessionId: string, req: OrderRequest): Promise<OrderResponse> {
  return apiFetch<OrderResponse>(`/sessions/${sessionId}/orders`, {
    method: 'POST',
    body: req,
  })
}

export function cancelOrder(sessionId: string, orderId: string): Promise<OrderResponse> {
  return apiFetch<OrderResponse>(`/sessions/${sessionId}/orders/${orderId}`, { method: 'DELETE' })
}
