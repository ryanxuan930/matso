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

// ---------------------------------------------------------------------------
// 事件流：統裁看得比參謀多（B）
// ---------------------------------------------------------------------------

/**
 * WS envelope 的**白軍視角**型別。
 *
 * `stores/sessionStream.ts` 的 `Envelope` 只宣告 v/seq/tick/type/payload——但受眾標籤
 * （`faction` / `factions` / `exclusive`，見 `core/app/stream/faction_filter.py`）是真的在
 * 線上傳過來的，只是沒被宣告出來。統裁要追事件鏈就必須看得到「這則是誰看得到的」，
 * 故在此補一份宣告；**不改 store**（那是別的軌的檔案，而且這裡只讀不寫）。
 */
export interface StreamEnvelope {
  seq?: number
  tick?: number
  type?: string
  faction?: string | null
  factions?: string[]
  exclusive?: boolean
  payload?: Record<string, unknown>
}

/**
 * 事件所屬的推演 tick。
 *
 * ⚠ **tick 在 payload 裡不在頂層**：`state/broadcaster.py` 的 `build_event_envelope` 把
 * `tick` 寫進 payload；而 API 直發的事件（白軍注入、時間控制、C2 信文，走 `stream/publish.py`）
 * **兩邊都沒有 tick**。所以這裡回 null 是正常情況，不是解析失敗——呼叫端要顯示成「—」
 * 而不是 0（T0 會被讀成「開局那一刻發生的」，那是假的）。
 */
export function eventTick(env: StreamEnvelope): number | null {
  const t = env.payload?.tick ?? env.tick
  return typeof t === 'number' && Number.isFinite(t) ? t : null
}

/**
 * 受眾標籤 → 中文。統裁是全知角色，收得到全部；但「誰看得到」本身就是統裁要判斷的事
 * ——同一則交戰，藍軍看得到、黃軍看不到，講評時說錯就變成洩漏。
 */
export function eventAudience(env: StreamEnvelope): string {
  const list = env.factions
  if (Array.isArray(list)) {
    // `factions: []` 是 WP-C5 的「真實副本」——只有全知旁通收得到（見 faction_filter）。
    return list.length ? list.join('、') : '僅統裁'
  }
  const one = env.faction
  if (typeof one === 'string' && one) return one
  return '全體'
}

// ---------------------------------------------------------------------------
// 快照點的可讀性（C）
// ---------------------------------------------------------------------------

/**
 * 後端的時間字串 → epoch ms。
 *
 * ⚠⚠ **後端送的是「不帶時區的 UTC」**：`created_at.isoformat()` 對一個 naive datetime
 * 產出 `2026-07-30T23:55:53.589000`——沒有 `Z`、沒有偏移。而 JS 對這種格式的規定是
 * **當成本地時間**（ES 的 date-time form 無偏移即 local）。於是在 UTC+8 的機器上，
 * 一個「4 分鐘前」的快照會被算成「8 小時 3 分前」，而畫面上完全看不出來是解析錯了
 * ——這正是本卡要把時間變成主要選擇依據時最不能出錯的地方（實測見回報）。
 *
 * 已帶時區的字串（`Z` 或 `±HH:MM`）照原樣尊重，不重複補。
 */
export function parseSimTime(raw: string): number | null {
  if (!raw) return null
  const hasZone = /(?:[Zz]|[+-]\d{2}:?\d{2})$/.test(raw)
  const t = Date.parse(hasZone ? raw : `${raw}Z`)
  return Number.isFinite(t) ? t : null
}

/**
 * 後端時間字串 → 帶明確時區的 ISO。
 * 給那些自己呼叫 `Date.parse` 的共用元件用（如系統時間列），免得它們踩同一個坑。
 */
export function simTimeIso(raw: string | null | undefined): string | null {
  const t = raw ? parseSimTime(raw) : null
  return t === null ? null : new Date(t).toISOString()
}

/** 時間字串 → HH:MM:SS（觀看者的本地時間）。壞值回空字串（不猜）。 */
export function clockOf(iso: string): string {
  const t = parseSimTime(iso)
  if (t === null) return ''
  return new Date(t).toLocaleTimeString('zh-TW', { hour12: false })
}

/** 相對現在多久之前（牆鐘）。未來 / 壞值 → 空字串。 */
export function ageOf(iso: string, nowMs: number): string {
  const t = parseSimTime(iso)
  if (t === null || !nowMs) return ''
  const sec = Math.floor((nowMs - t) / 1000)
  if (sec < 0) return ''
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分鐘前`
  const hr = Math.floor(min / 60)
  return `${hr} 小時 ${min % 60} 分前`
}

/**
 * 快照點下拉的一行字。
 *
 * 過去是 `tick {n} · seq {n} · {hash 前 8 碼}`。雜湊前 8 碼是給人**核對**用的
 * （兩份紀錄是不是同一個狀態），不是給人**選擇**用的——統裁在回溯時要回答的問題是
 * 「回到什麼時候」，`3f9a1c2e` 不回答那個問題。
 *
 * 所以這裡把**牆鐘時間 + 多久以前**放到前面（那是統裁腦中真正的座標系：
 * 「回到剛才那場交戰之前」＝「回到大約十分鐘前」），tick 留著（回滾目標的實際單位），
 * 雜湊留著但明講是校驗碼。`atTick` 是**已知的**該 tick 事件敘述——不知道就不寫。
 *
 * ⚠ 這裡沒有「推演時間」（模擬世界的日期時間）：`/checkpoints` 只回 tick/seq/hash/created_at，
 * 換算需要該局的 `tick_rate_ms` 與 `world_start_time`，兩者都沒有 API 供應（見回報）。
 * 寧可不顯示，也不要用前端猜的 tick 長度去算一個看起來很像真的時間。
 */
export function checkpointLabel(
  cp: CheckpointPoint,
  nowMs: number,
  atTick?: string,
): string {
  const clock = clockOf(cp.created_at)
  const age = ageOf(cp.created_at, nowMs)
  const when = clock ? `${clock}${age ? `（${age}）` : ''}` : '存錄時間不明'
  const what = atTick ? ` · 當時：${atTick}` : ''
  return `T${cp.tick} · ${when}${what} · 校驗碼 ${cp.state_hash.slice(0, 8)}`
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
