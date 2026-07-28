import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type UnitView = components['schemas']['UnitView']
export type WeaponView = components['schemas']['WeaponView']
export type OrderResponse = components['schemas']['OrderResponse']
type OrderRequest = components['schemas']['OrderRequest']

// 指令類型 / 狀態 → 中文（UI 顯示；後端 enum 值不變）。
export const ORDER_TYPE_LABELS: Record<string, string> = { MOVE: '移動', ENGAGE: '交戰' }
export const ORDER_STATUS_LABELS: Record<string, string> = {
  PENDING: '等待中',
  VALIDATED: '已驗證',
  EXECUTING: '執行中',
  COMPLETED: '完成',
  REJECTED: '拒絕',
  CANCELLED: '已取消',
}
export function orderTypeLabel(t?: string): string {
  return (t && ORDER_TYPE_LABELS[t]) || t || ''
}
export function orderStatusLabel(s?: string): string {
  return (s && ORDER_STATUS_LABELS[s]) || s || ''
}

/** 取 session 的 faction-scoped 單位（下令對象）。 */
export function fetchUnits(sessionId: string): Promise<UnitView[]> {
  return apiFetch<UnitView[]>(`/sessions/${sessionId}/units`)
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
