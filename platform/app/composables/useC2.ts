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

/**
 * 標示已讀的回應。**契約尚未收錄 `POST /sessions/{id}/messages/read`**（本輪 contracts/
 * 由主 agent 統一維護），所以型別暫時手寫在這裡而非由 `~/types/api` 生成。
 * 契約補上後，這個 interface 應該換成 `components['schemas']['MarkReadResult']`。
 *
 * `marked` 是**實際被標到的信文 id**——後端會跳過已讀過的、自己寄的、不是寄給我的，
 * 呼叫端據此分辨「按了沒生效」與「成功」。
 */
export interface MarkReadResult {
  marked: string[]
  read_at: string | null
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
  opts: {
    kind?: MessageKind
    toSeat?: string | null
    /** 收件陣營；空＝自己的陣營。**跨陣營只有白軍/統裁送得出去**（後端擋）。 */
    toFaction?: string | null
    refId?: string | null
  } = {},
) => {
  const payload: Record<string, unknown> = {
    kind: opts.kind ?? 'FREE_TEXT',
    // 空字串一律送 null：「發給整個陣營」與「發給某席位」是兩種語意。
    to_seat: opts.toSeat || null,
    ref_id: opts.refId || null,
    body,
  }
  // 只有跨陣營時才帶 to_faction（契約型別是 string，不接受 null）——
  // 不帶＝維持「發給自己陣營」的既有語意。
  if (opts.toFaction) payload.to_faction = opts.toFaction
  return apiFetch<MessageView>(`/sessions/${id}/messages`, { method: 'POST', body: payload })
}

/**
 * 標示已讀。`ids` 省略＝把所有「寄給我且未讀」的信文一次標掉。
 *
 * 在此之前前端**完全沒有已讀的呼叫端**——後端欄位與 DB 欄位都在，就是沒人寫得進去，
 * 所以信文匣裡每一封永遠是未讀。
 */
export const markMessagesRead = (id: string, ids?: string[]) =>
  apiFetch<MarkReadResult>(`/sessions/${id}/messages/read`, {
    method: 'POST',
    body: { message_ids: ids ?? null },
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
