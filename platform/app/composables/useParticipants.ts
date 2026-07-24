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
): Promise<SessionParticipantView> {
  return apiFetch<SessionParticipantView>(`/sessions/${sessionId}/participants/${userId}`, {
    method: 'PUT',
    body: { faction, role, unit_scope: unitScope },
  })
}
export function removeParticipant(sessionId: string, userId: string): Promise<unknown> {
  return apiFetch<unknown>(`/sessions/${sessionId}/participants/${userId}`, { method: 'DELETE' })
}
