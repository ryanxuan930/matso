// 推演參與者名冊（指派帳號×陣營×角色，決定操控/查看範圍）——純 API 包裝。
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type ParticipantRoster = components['schemas']['ParticipantRoster']
export type SessionParticipantView = components['schemas']['SessionParticipantView']
export type UserView = components['schemas']['UserView']

// 參與角色 → 中文（明示可否操控/查看）。
export const PARTICIPANT_ROLE_LABELS: Record<string, string> = {
  COMMANDER: '指揮官（可操控）',
  STAFF: '參謀（可操控）',
  OBSERVER: '觀察員（只查看）',
  ANALYST: '分析員（AAR）',
  WHITE_CELL_STAFF: '白軍（全知）',
  EXERCISE_DIRECTOR: '統裁（全知）',
}
export const ASSIGNABLE_ROLES = [
  'COMMANDER',
  'STAFF',
  'OBSERVER',
  'ANALYST',
  'WHITE_CELL_STAFF',
  'EXERCISE_DIRECTOR',
]

/**
 * 席位（WP-B5.1）——同陣營內的參謀分工，與「參與角色」正交。
 * **空字串／未指派＝沿用角色既有權限**（既有局零行為變更，見後端 app/seats）。
 */
export const SEAT_ROLE_LABELS: Record<string, string> = {
  COMMANDER: '指揮官（可下全部令）',
  S3_OPS: '作戰官 S3（機動/任務/姿態/隊形/工兵/偵蒐）',
  FSO_FIRES: '火力支援協調官 FSO（交戰/火力任務）',
  S2_INTEL: '情報官 S2（不可下令）',
  S4_LOG: '後勤官 S4（補給撥交）',
  OBSERVER: '觀察員（不可下令）',
}
export const ASSIGNABLE_SEATS = ['COMMANDER', 'S3_OPS', 'FSO_FIRES', 'S2_INTEL', 'S4_LOG', 'OBSERVER']

export function fetchRoster(sessionId: string): Promise<ParticipantRoster> {
  return apiFetch<ParticipantRoster>(`/sessions/${sessionId}/participants`)
}
export function fetchAllUsers(): Promise<UserView[]> {
  return apiFetch<UserView[]>('/users')
}
export function assignParticipant(
  sessionId: string,
  userId: string,
  faction: string,
  role: string,
  unitScope: string[] = [],
  seatRole: string | null = null,
): Promise<SessionParticipantView> {
  return apiFetch<SessionParticipantView>(`/sessions/${sessionId}/participants/${userId}`, {
    method: 'PUT',
    // seat_role 空字串一律送 null——「未指派席位」與「指派了某席位」是兩種語意，
    // 送空字串會被後端 pydantic 當成非法 enum 值擋掉。
    body: { faction, role, unit_scope: unitScope, seat_role: seatRole || null },
  })
}
export function removeParticipant(sessionId: string, userId: string): Promise<unknown> {
  return apiFetch<unknown>(`/sessions/${sessionId}/participants/${userId}`, { method: 'DELETE' })
}
