// C2 信文與申請-核覆（WP-B5.2）——純 API 包裝 + 顯示用標籤。
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type MessageView = components['schemas']['MessageView']
export type RequestView = components['schemas']['RequestView']
export type RequestQuota = components['schemas']['RequestQuota']
export type MessageKind = components['schemas']['MessageKind']
export type RequestKind = components['schemas']['RequestKind']

export interface RequestList {
  requests: RequestView[]
  quotas: RequestQuota[]
}

export const MESSAGE_KIND_LABELS: Record<string, string> = {
  FREE_TEXT: '一般信文',
  REQUEST: '申請',
  APPROVAL: '核覆',
  REPORT: '回報',
}
export const REQUEST_KIND_LABELS: Record<string, string> = {
  AIR_RECON: '空中偵察',
  FIRE_SUPPORT: '火力支援',
  RESUPPLY_VOUCHER: '補給憑單',
  // WP-C10.1 起就存在的種類，但標籤與送出路徑一直沒補——於是 UI 送不出一張合法的臨機火力申請。
  CALL_FOR_FIRE: '臨機火力（叫火力）',
}

/** 需要目標座標的申請種類。後端會擋（REQUEST_NO_OBSERVER），前端先問清楚比較不折磨人。 */
export const KINDS_NEEDING_TARGET = new Set(['CALL_FOR_FIRE'])
/** 申請單狀態。APPROVED 與 EXPENDED 分開＝「已核准」與「已用掉」是兩件事。 */
export const REQUEST_STATUS_LABELS: Record<string, string> = {
  PENDING: '待核覆',
  APPROVED: '已核准',
  DENIED: '已駁回',
  EXPENDED: '已用掉',
}

export const fetchMessages = (id: string) =>
  apiFetch<MessageView[]>(`/sessions/${id}/messages`)

export const sendMessage = (
  id: string,
  body: string,
  opts: { kind?: MessageKind; toSeat?: string | null; refId?: string | null } = {},
) =>
  apiFetch<MessageView>(`/sessions/${id}/messages`, {
    method: 'POST',
    body: {
      kind: opts.kind ?? 'FREE_TEXT',
      // 空字串一律送 null：「發給整個陣營」與「發給某席位」是兩種語意。
      to_seat: opts.toSeat || null,
      ref_id: opts.refId || null,
      body,
    },
  })

export const fetchRequests = (id: string) => apiFetch<RequestList>(`/sessions/${id}/requests`)

/**
 * 送出申請單。
 *
 * `params` **不能寫死成 `{}`**：`CALL_FOR_FIRE` 的後端要求 `target_lat`/`target_lng`
 * （沒有觀測就叫不動火力，WP-C10.1），寫死空物件等於從 UI 永遠送不出一張合法的申請。
 */
export const submitRequest = (
  id: string,
  kind: RequestKind,
  note: string,
  params: Record<string, unknown> = {},
) =>
  apiFetch<RequestView>(`/sessions/${id}/requests`, {
    method: 'POST',
    body: { kind, params, note },
  })

export const decideRequest = (id: string, rid: string, approve: boolean, note = '') =>
  apiFetch<RequestView>(`/sessions/${id}/requests/${rid}/decide`, {
    method: 'POST',
    body: { approve, note },
  })
