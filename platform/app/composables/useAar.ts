// AAR 儀表板 API（O8）——重播/統計/敘事/匯出。
import { apiFetch } from '~/composables/useApi'

export interface AarReplay {
  frames: Array<{ tick: number; event_types: string[] }>
  bookmarks: Array<{ seq: number; tick: number; label: string }>
  total_events: number
  max_tick: number
}
/** 地圖重播（WP-D6.1）：靜態底本 + 逐 tick 差異。 */
export interface AarReplayUnit {
  id: string
  designation?: string
  faction: string
  unit_level?: string
  is_fixed?: boolean
  /** 滿編戰力；null 時後端不導出效能%（只給 strength）。 */
  authorized_strength?: number | null
  /** tick 0 基準位置——**近似值**：帳本沒有部署事件，白軍地圖狀態編輯也不落帳。
   *  取該單位最早一筆有座標的事件；從沒動過的單位取 DB 現值（精確）。 */
  base_lat?: number | null
  base_lng?: number | null
  base_health: number
}
export interface AarReplayChange {
  unit_id: string
  lat?: number
  lng?: number
  /** 效能%（0–100）。與 strength **量綱不同不可互換**。 */
  health?: number
  /** 戰力點（人員/平台數量級）。 */
  strength?: number
}
export interface AarReplayStates {
  units: AarReplayUnit[]
  frames: Array<{ tick: number; event_types?: string[]; changes: AarReplayChange[] }>
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
export const aarReplayStates = (id: string) =>
  apiFetch<AarReplayStates>(`/sessions/${id}/aar/replay/states`)
export const aarStats = (id: string) => apiFetch<AarStats>(`/sessions/${id}/aar/stats`)
export const aarReport = (id: string) => apiFetch<AarReport>(`/sessions/${id}/aar/report`)
/**
 * AAR 匯出下載（#10）——以帶 Bearer 的 apiFetch 取回內容（自動續 token），再以 Blob 觸發下載。
 * 舊做法用 <a href> 直連 API 端點，瀏覽器導覽不帶 Authorization 標頭 → 401「缺少 Token」。
 */
export async function aarExportDownload(
  id: string,
  fmt: 'json' | 'csv',
  anonymize: boolean,
): Promise<void> {
  const data = await apiFetch<unknown>(
    `/sessions/${id}/aar/export?fmt=${fmt}&anonymize=${anonymize}`,
  )
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  const mime = fmt === 'json' ? 'application/json' : 'text/csv;charset=utf-8'
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `aar-${id}${anonymize ? '-anon' : ''}.${fmt}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
