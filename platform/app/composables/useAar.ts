// AAR 儀表板 API（O8）——重播/統計/敘事/匯出。
import { apiFetch } from '~/composables/useApi'
import type { components } from '~/types/api'

/**
 * 任務時間軸。**用契約生成的型別，不再手抄一份**。
 *
 * ⚠ 本檔其餘的 AAR 型別（`AarReplay`／`AarStats`／`AarReport`…）都還是手寫 interface
 * ——那是 UI 盤點 P4 點名的漂移：後端改個欄位名，畫面靜默變空白而所有閘門都是綠的。
 * 這一條是把它們搬回契約的第一個。
 */
export type MissionTimeline = components['schemas']['MissionTimeline']
export type MissionLeg = components['schemas']['MissionLeg']

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
  /** 交戰事件總數（個體＋聚合，含被拒的）。 */
  engagements: number
  /** 個體交戰的裁決次數（含被拒）——「下令交火幾次」。 */
  attempts: number
  /**
   * 其中真的射出去的次數（`attempts` 扣掉 REJECTED）。**這是命中率的分母**：
   * 超射程／無彈／無視線／ROE 不准打都是一發未發，拿去稀釋火力效益毫無意義。
   */
  engagements_fired: number
  hits: number
  /** hits ÷ engagements_fired。 */
  hit_rate: number
  total_damage: number
  guardrail_blocks: number
  damage_by_faction: Record<string, number>
  event_counts: Record<string, number>
  /**
   * 統計口徑版本（WP-D6.2 起）。舊封存包沒有這一欄＝v1，其命中率只認單發路徑、
   * 分母含被拒交戰、聚合戰損整包記在守方——**與 v2 的數字不可直接相比**。
   *
   * 契約標為 required，這裡**刻意放寬成 optional**：欄位缺席正是要示警的那個情況
   * （前端更新了、後端容器還是舊的）。把它宣告成必有，等於讓型別替後端背書。
   */
  stats_version?: number
}
/** 本前端所預期的統計口徑；與後端回的 `stats_version` 不符時畫面要講出來。 */
export const AAR_STATS_VERSION = 2

/**
 * 未射出的交戰次數（下令數 − 實射數）。
 *
 * 「交火 40 次、命中率 30%」這句話在戰術檢討上是空的——分母是下令次數還是實射次數，
 * 差別可以到好幾倍（超射程與無彈在真實推演裡佔比很高）。三個數字分開列，
 * 指揮官才看得出問題出在「打不到」還是「打不準」，那是兩種完全不同的處置。
 */
export function aarRejectedCount(stats: AarStats | null): number {
  if (!stats) return 0
  return Math.max(0, stats.attempts - stats.engagements_fired)
}

/** 命中率文字。一發未發時不顯示「0%」——那會被讀成「打了但全沒中」。 */
export function aarHitRateLabel(stats: AarStats | null): string {
  if (!stats || !stats.engagements_fired) return '—（無一發射出）'
  return `${(stats.hit_rate * 100).toFixed(0)}%`
}

/**
 * 口徑不符警告（空字串＝口徑相符，不必示警）。
 *
 * 舊局的統計是用 v1 算的（分子只認單發路徑、分母含被拒交戰、聚合戰損整包記在守方），
 * 跟現在的數字擺在一起比會得到錯誤結論。封存包已經把舊數字寫進歷史演習了，
 * 補算不回來，只能讓「不可比」這件事在畫面上講清楚。
 */
export function aarStatsVersionNote(stats: AarStats | null): string {
  const v = stats?.stats_version
  if (v === AAR_STATS_VERSION) return ''
  if (v == null) return `此局統計以舊口徑（v1）產生，與 v${AAR_STATS_VERSION} 的數字不可直接相比。`
  return `此局統計口徑為 v${v}，本頁預期 v${AAR_STATS_VERSION}——數字不可直接相比。`
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
/**
 * 任務時間軸（WP-A2）——每道任務走過哪些階段、各花了多久。
 *
 * **67 條業務端點裡唯一一條完全沒接的**：curl 就有真資料，畫面上零蹤影。
 * 任務級下令是這個系統最貴的功能，而「執行得好不好」過去沒有任何量化畫面。
 */
export const aarMissions = (id: string) =>
  apiFetch<MissionTimeline[]>(`/sessions/${id}/aar/missions`)
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
