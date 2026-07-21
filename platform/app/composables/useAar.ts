// AAR 儀表板 API（O8）——重播/統計/敘事/匯出。
import { apiFetch } from '~/composables/useApi'

export interface AarReplay {
  frames: Array<{ tick: number; event_types: string[] }>
  bookmarks: Array<{ seq: number; tick: number; label: string }>
  total_events: number
  max_tick: number
}
export interface AarStats {
  total_events: number
  engagements: number
  hit_rate: number
  total_damage: number
  guardrail_blocks: number
  damage_by_faction: Record<string, number>
  event_counts: Record<string, number>
}
export interface AarReport {
  summary: string
  paragraphs: Array<{ text: string; cited_seqs: number[] }>
  lessons: string[]
  citations: { valid: boolean; invalid_seqs: number[] }
}

export const aarReplay = (id: string) => apiFetch<AarReplay>(`/sessions/${id}/aar/replay`)
export const aarStats = (id: string) => apiFetch<AarStats>(`/sessions/${id}/aar/stats`)
export const aarReport = (id: string) => apiFetch<AarReport>(`/sessions/${id}/aar/report`)
export function aarExportUrl(id: string, fmt: 'json' | 'csv', anonymize: boolean): string {
  const base = useRuntimeConfig().public.apiBase as string
  return `${base}/api/v1/sessions/${id}/aar/export?fmt=${fmt}&anonymize=${anonymize}`
}
