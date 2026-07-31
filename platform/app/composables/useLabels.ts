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

// ---- 資料表名（演習銷毀模式的刪除筆數回報）----

/**
 * 後端 ORM 表名 → 中文資料類別。
 *
 * 銷毀模式回的 `rows_deleted` 鍵是**模型類別名**（`app/lobby/purge.py` 由 mapper registry
 * 自省導出），過去原樣攤在畫面上：統裁看到的是「Message 12 / IntelContact 305」。
 * 銷毀是不可逆操作，「到底刪掉了什麼」必須讀得懂才有意義。
 *
 * 後端那份清單是**自省**的（新增 session 範圍的表會自動入列），所以這裡註定會缺項——
 * 缺項就原樣印英文表名（見模組說明），那正是「該回來補一列」的提示。
 */
export const DATA_TABLE_LABELS: Record<string, string> = {
  WargameSession: '推演局',
  TacticalUnit: '單位',
  EquipmentInstance: '編裝',
  MapFeature: '地圖標註',
  TacticalEventLog: '事件帳本',
  Order: '指令',
  IntelContact: '情報接觸',
  Message: 'C2 信文',
  Request: '申請單',
  FirePlan: '火力計畫',
  FirePlanTarget: '火力計畫目標',
  SimCheckpoint: '模擬檢查點',
  AIInvocationLog: 'AI 呼叫紀錄',
  AARReport: '戰後檢討報告',
  SessionParticipant: '參與者名冊',
}

export function dataTableLabel(raw: string | null | undefined): string {
  return lookup(DATA_TABLE_LABELS, raw)
}

// ---- 機動能力（行軍耗損設定、路徑試算共用）----

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

// ---- 補給類別與斷補（WP-C7；單位卡與補給點編輯共用）----

/**
 * 北約補給類別編號 → 繁中。用字取自 `core/app/adjudication/supply.py` 的類別表，
 * **不另創譯名**。
 *
 * ⚠ 與 `useWeaponVocab.SUPPLY_CLASS_LABELS`（AMMO/FUEL/WATER_FOOD/BATTERY）**不是同一組**：
 * 那是軍械庫 LOGISTICS 裝備範本的載運艙格鍵，這裡是單位/補給點身上的存量帳。
 * 兩者命名不一致是 WP-C7.1 明列的既有欠帳（要動契約），不要在前端偷偷把它們對起來。
 */
export const NATO_SUPPLY_CLASS_LABELS: Record<string, string> = {
  I: '口糧／水',
  III: '油料',
  V: '彈藥',
  IX: '維修件',
}
/** 顯示順序＝北約編號順序（與後端 `SupplyClass` 的宣告順序一致，非字典序）。 */
export const NATO_SUPPLY_CLASSES = ['I', 'III', 'V', 'IX']

export function supplyClassLabel(raw: string | null | undefined): string {
  return lookup(NATO_SUPPLY_CLASS_LABELS, raw)
}

/**
 * 斷補效能階梯——**這是 `core/app/adjudication/supply.py` 的 `STARVATION_STEPS` 的鏡像**。
 *
 * 為什麼要在前端複製一份：`starved_days` 是 STATE_DIFF 推來的活值，但效能倍率不是
 * ——後端只送天數。要在單位卡上把「斷補 3 日」翻成指揮官真正要知道的
 * 「這支部隊現在只發揮五成」，就得有這條階梯。
 *
 * 兩份會漂，所以 `core/tests/unit/test_supply_point_api.py` 有一條測試逐項比對
 * 這個常數與後端的 `STARVATION_STEPS`；改了後端沒改這裡（或反之）會直接紅。
 * **格式不可亂動**（測試以 `[天數, 倍率]` 的字面陣列解析）。
 */
export const STARVATION_STEPS: [number, number][] = [
  [0, 1.0],
  [1, 0.9],
  [2, 0.75],
  [3, 0.5],
  [5, 0.25],
]

/** 斷補 N 個模擬日後的效能倍率（階梯，不是連續衰減）。 */
export function starvationModifier(days: number): number {
  let result = 1
  for (const [threshold, modifier] of STARVATION_STEPS) {
    if (days >= threshold) result = modifier
  }
  return result
}

/**
 * 錯誤訊息的兜底文字。
 *
 * 過去各處寫 `?? 'UNKNOWN'`——畫面上會冒出英文 `UNKNOWN`，看起來像系統故障代碼，
 * 而它其實只是「這個例外沒帶訊息」。
 */
export const UNKNOWN_REASON = '原因不明'
