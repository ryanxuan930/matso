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
}
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

export const submitRequest = (id: string, kind: RequestKind, note: string) =>
  apiFetch<RequestView>(`/sessions/${id}/requests`, {
    method: 'POST',
    body: { kind, params: {}, note },
  })

export const decideRequest = (id: string, rid: string, approve: boolean, note = '') =>
  apiFetch<RequestView>(`/sessions/${id}/requests/${rid}/decide`, {
    method: 'POST',
    body: { approve, note },
  })
