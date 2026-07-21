// White Cell 控制台 API（O7.4）——視角切換 / 時間控制 / 事件注入。限統裁角色。
import { apiFetch } from '~/composables/useApi'
import type { UnitView } from '~/composables/useOrders'

export type ControlAction = 'PAUSE' | 'RESUME' | 'ROLLBACK'

/** 以指定陣營視角取單位（as_faction 空＝全知 god view）。 */
export function unitsAsFaction(sessionId: string, asFaction: string | null): Promise<UnitView[]> {
  const q = asFaction ? `?as_faction=${encodeURIComponent(asFaction)}` : ''
  return apiFetch<UnitView[]>(`/sessions/${sessionId}/units${q}`)
}

/** 時間控制（PAUSE/RESUME/ROLLBACK）。 */
export function sessionControl(
  sessionId: string,
  action: ControlAction,
  targetTick?: number,
): Promise<{ seq: number }> {
  return apiFetch(`/sessions/${sessionId}/control`, {
    method: 'POST',
    body: { action, target_tick: targetTick ?? null },
  })
}

/** ad-hoc 事件注入。 */
export function injectEvent(
  sessionId: string,
  eventType: string,
  payload: Record<string, unknown> = {},
  faction: string | null = null,
): Promise<{ seq: number }> {
  return apiFetch(`/sessions/${sessionId}/inject`, {
    method: 'POST',
    body: { event_type: eventType, payload, faction },
  })
}
