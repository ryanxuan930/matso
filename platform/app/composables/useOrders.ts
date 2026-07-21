import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type UnitView = components['schemas']['UnitView']
export type OrderResponse = components['schemas']['OrderResponse']
type OrderRequest = components['schemas']['OrderRequest']

/** 取 session 的 faction-scoped 單位（下令對象）。 */
export function fetchUnits(sessionId: string): Promise<UnitView[]> {
  return apiFetch<UnitView[]>(`/sessions/${sessionId}/units`)
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
