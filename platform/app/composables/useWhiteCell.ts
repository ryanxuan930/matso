// White Cell 控制台 API（O7.4）——視角切換 / 時間控制 / 事件注入。限統裁角色。
import { apiFetch } from '~/composables/useApi'
import type { UnitView } from '~/composables/useOrders'

export type ControlAction = 'PAUSE' | 'RESUME' | 'ROLLBACK'

/** 以指定陣營視角取單位（as_faction 空＝全知 god view）。 */
export function unitsAsFaction(sessionId: string, asFaction: string | null): Promise<UnitView[]> {
  const q = asFaction ? `?as_faction=${encodeURIComponent(asFaction)}` : ''
  return apiFetch<UnitView[]>(`/sessions/${sessionId}/units${q}`)
}

/** 可回滾的快照點（WP-E1）。**回滾目標必須剛好是其中一個 tick**，否則後端回
 * `ROLLBACK_TARGET_NOT_FOUND`——所以這個清單不是裝飾，是唯一能讓白軍選對值的東西。 */
export interface CheckpointPoint {
  tick: number
  ledger_seq: number
  state_hash: string
  created_at: string
}

export function fetchCheckpoints(sessionId: string): Promise<CheckpointPoint[]> {
  return apiFetch<CheckpointPoint[]>(`/sessions/${sessionId}/checkpoints`)
}

export interface ControlResult {
  seq: number
  /** 實際被接受的回滾點。後端回滾完會把該局留在暫停狀態，需白軍自行續行。 */
  rollback_requested_tick?: number | null
}

/** 時間控制（PAUSE/RESUME/ROLLBACK）。 */
export function sessionControl(
  sessionId: string,
  action: ControlAction,
  targetTick?: number,
): Promise<ControlResult> {
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
