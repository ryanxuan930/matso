/**
 * 跨面板共用的中文標籤表。
 *
 * ## 為什麼要有這個檔案
 *
 * 畫面上一直有裸英文代號漏出去：推演清單印 `REALTIME · ACTIVE`、首頁右上角印
 * `EXERCISE_DIRECTOR`、串流狀態印 `resyncing`、下令被拒的 toast 逐條印
 * `✗ line_of_sight`。使用者是統裁與參謀，不是讀 enum 的人。
 *
 * 這些代號分散在各個面板各自 `{{ x.status }}` 出來，沒有一個共用的地方可以補——
 * 於是每加一個新畫面就漏一次。本檔就是那個共用的地方。
 *
 * **紀律：查無對應一律原樣回傳**，不要回「未知」。原樣回傳至少讓人搜得到那個代號是什麼；
 * 回「未知」則把資訊整個抹掉，出問題時連追都沒得追。
 */

/** 對照表查詢的共同形狀：查無 → 原樣回傳（見模組說明）。 */
function lookup(table: Record<string, string>, raw: string | null | undefined): string {
  if (!raw) return ''
  return table[raw] ?? raw
}

// ---- WS 串流狀態（COP 頂列與戰況事件面板常駐顯示）----

export const STREAM_STATUS_LABELS: Record<string, string> = {
  idle: '待命',
  connecting: '連線中',
  live: '即時連線',
  resyncing: '重新同步中',
  closed: '已中斷',
}

export function streamStatusLabel(raw: string | null | undefined): string {
  return lookup(STREAM_STATUS_LABELS, raw)
}

// ---- 推演模式與狀態（推演清單每一列都印）----

export const SESSION_MODE_LABELS: Record<string, string> = {
  REALTIME: '即時',
  // ⚠ 這兩種模式**引擎尚未實作**——選了仍以即時制執行。標籤要說清楚，
  // 否則想定作者會以為選了就會照回合跑。
  WEGO: '同步回合（未實作）',
  IGO_UGO: '輪流回合（未實作）',
}

export function sessionModeLabel(raw: string | null | undefined): string {
  return lookup(SESSION_MODE_LABELS, raw)
}

export const SESSION_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '進行中',
  ENDED: '已結束',
  ARCHIVED: '已封存',
}

export function sessionStatusLabel(raw: string | null | undefined): string {
  return lookup(SESSION_STATUS_LABELS, raw)
}

// ---- 情報等級（通聯不良時的敵情粒度）----

export const INTEL_FIDELITY_LABELS: Record<string, string> = {
  DETECTED: '發現',
  CLASSIFIED: '判明',
  IDENTIFIED: '識別',
}

export function intelFidelityLabel(raw: string | null | undefined): string {
  return lookup(INTEL_FIDELITY_LABELS, raw)
}

// ---- 預檢項目 ----

/**
 * 下令預檢的項目名 → 中文。
 *
 * **這是本檔最要緊的一張表**：下令被拒是操作員最高頻的錯誤路徑，
 * 而過去 toast 與預檢清單逐條印的是 `✗ line_of_sight` 這種後端內部鍵名。
 * 參謀看到那個字串只能猜。
 *
 * 鍵取自 `core/app/orders/precheck.py` 實際會產生的 `PrecheckCheck.name`。
 * 後端新增檢查項時要回來補這裡——**查無會原樣印出英文**，那就是提醒。
 */
export const PRECHECK_LABELS: Record<string, string> = {
  physics: '物理前提',
  position: '座標',
  range: '射程',
  line_of_sight: '通視',
  trajectory: '彈道',
  reachability: '路徑可達性',
  target_exists: '目標存在',
  weapon: '武器',
  ammo: '彈藥',
  combined_fires: '聯合火力',
  indirect_weapon: '曲射武器',
  no_strike: '禁射區',
  roe: '交戰規則',
  roe_weapon: '武器管制',
  fire_approval: '火力支援核准',
  fratricide_warning: '誤傷警告',
  mission_params: '任務參數',
  engineer_qualified: '工兵資格',
  engineer_target: '作業標的',
  engineer_proximity: '作業距離',
}

export function precheckLabel(raw: string | null | undefined): string {
  return lookup(PRECHECK_LABELS, raw)
}

// ---- 使用者角色（首頁右上角、帳號管理、名冊共用）----

/**
 * 系統角色的中文。
 *
 * ⚠ 與 `useParticipants.ts` 的 `PARTICIPANT_ROLE_LABELS` **不是同一件事**：
 * 那一張帶著「（全知）」「（可操控）」這類指派時要看的括號註記，
 * 這一張是純稱謂——首頁右上角印「王小明（統裁（全知））」會很怪。
 */
export const USER_ROLE_LABELS: Record<string, string> = {
  EXERCISE_DIRECTOR: '演習總監',
  WHITE_CELL_STAFF: '白軍幕僚',
  COMMANDER: '指揮官',
  STAFF: '參謀',
  OBSERVER: '觀察員',
  ANALYST: '分析官',
  ADMIN: '系統管理',
}

export function userRoleLabel(raw: string | null | undefined): string {
  return lookup(USER_ROLE_LABELS, raw)
}

// ---- 機動 profile（行軍耗損設定、路徑試算共用）----

export const MOBILITY_LABELS: Record<string, string> = {
  FOOT: '徒步',
  WHEELED: '輪型',
  TRACKED: '履帶',
  BOAT: '舟艇',
  AIR: '空中',
}

export function mobilityProfileLabel(raw: string | null | undefined): string {
  return lookup(MOBILITY_LABELS, raw)
}

/**
 * 錯誤訊息的兜底文字。
 *
 * 過去各處寫 `?? 'UNKNOWN'`——畫面上會冒出英文 `UNKNOWN`，看起來像系統故障代碼，
 * 而它其實只是「這個例外沒帶訊息」。
 */
export const UNKNOWN_REASON = '原因不明'
