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

/** 引用查核攤平結果（見 `auditCitations`）。 */
export interface CitationAudit {
  /** 被判定為捏造（帳本查無此 seq）的引用。 */
  invalid: Set<number>
  /** 同上，去重且升冪——畫面直接列出來用，避免在樣板裡每次重繪都排一次序。 */
  invalidSorted: number[]
  /** 段落索引 → 該段被捏造的 seq；只收真的有問題的段落。 */
  byParagraph: Map<number, number[]>
  /** 後端說捏造、卻沒有任何段落引用它的 seq。 */
  orphans: number[]
  /** 相異的捏造引用數（同一 seq 被引用兩次只算一筆）。 */
  total: number
}

/**
 * 把 `citations.invalid_seqs` 攤成「哪一段、哪幾條」。
 *
 * 過去畫面只用這個陣列的真假值印「（引用查核：有捏造）」六個字——統裁得到一份被標記為
 * 不可信、卻無從查起的報告，等於整份都不能用。捏造的是**個別引用**，句子本身未必錯，
 * 分辨得出來才知道哪一段要重寫、哪一段仍可採信。
 *
 * `orphans` 存在的理由：後端的查核以段落引用為輸入（`aar/narrative.verify_citations`），
 * 正常情況不會有孤兒；真的出現就是前後端對不上，必須顯示出來而不是默默吞掉。
 */
export function auditCitations(report: AarReport | null): CitationAudit {
  const invalid = new Set(report?.citations?.invalid_seqs ?? [])
  const byParagraph = new Map<number, number[]>()
  const cited = new Set<number>()
  report?.paragraphs.forEach((p, i) => {
    const bad: number[] = []
    for (const s of p.cited_seqs) {
      cited.add(s)
      if (invalid.has(s) && !bad.includes(s)) bad.push(s)
    }
    if (bad.length) byParagraph.set(i, bad)
  })
  const invalidSorted = [...invalid].sort((a, b) => a - b)
  return {
    invalid,
    invalidSorted,
    byParagraph,
    orphans: invalidSorted.filter((s) => !cited.has(s)),
    total: invalid.size,
  }
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
