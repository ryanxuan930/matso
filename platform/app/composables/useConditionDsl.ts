// MSEL 觸發條件 DSL（前端鏡像 core/app/scenario/triggers.py 的 evaluate_condition）。
// type 與各欄位須與後端逐字對齊；契約先行——變更前先改後端與 contracts/msel.schema.json。

export type ConditionType =
  | 'time'
  | 'faction_eliminated'
  | 'strength_below'
  | 'unit_in_region'
  | 'all'
  | 'any'

/** tick ≥ at_tick 時成立。 */
export interface TimeCondition { type: 'time'; at_tick: number }
/** 該陣營戰力 ≤ 0 時成立。 */
export interface FactionEliminatedCondition { type: 'faction_eliminated'; faction: string }
/** 該陣營戰力 < value 時成立。 */
export interface StrengthBelowCondition { type: 'strength_below'; faction: string; value: number }
/** 該陣營任一單位位於 bbox=[minLng,minLat,maxLng,maxLat] 內時成立。 */
export interface UnitInRegionCondition {
  type: 'unit_in_region'
  faction: string
  bbox: [number, number, number, number]
}
/** 所有子條件皆成立（AND）。 */
export interface AllCondition { type: 'all'; of: Condition[] }
/** 任一子條件成立（OR）。 */
export interface AnyCondition { type: 'any'; of: Condition[] }

export type Condition =
  | TimeCondition
  | FactionEliminatedCondition
  | StrengthBelowCondition
  | UnitInRegionCondition
  | AllCondition
  | AnyCondition

/**
 * MSEL 注入的「動作」——**逐字對齊 `core/app/scenario/msel_actions.py` 的 `make_applier`**。
 *
 * ## 兩件會讓整個注入靜默失效的事（這也是這一段程式存在的理由）
 *
 * 1. **動作欄位在 `inject` 的最上層**，與 `event_type`/`payload`/`faction` 同層。
 *    套用層讀的是 `inject.get("action")`、`inject.get("unit_id")`……；塞進 `payload` 的話
 *    會落到 Ledger 的 `ai_decision` 裡（看起來很像有設定過），套用層卻一個都讀不到
 *    ——事件照發、世界不動，而且沒有任何錯誤。
 * 2. **只有 MSEL 這條路會套用動作**。白軍控制台的即時注入端點（`core/app/api/inject.py`）
 *    只把事件發進 Redis ring / WS，不經過 `make_applier`：即時注入寫 `action` 沒有效果。
 *    故表單分 `msel` / `live` 兩種型態，`live` 不給動作選項——給了就是騙人。
 */
export type InjectActionKind =
  | 'SPAWN_UNITS'
  | 'MODIFY_UNIT'
  | 'MESSAGE'
  | 'PAUSE'
  | 'WEATHER_OVERRIDE'

/** SPAWN_UNITS 的單位規格（對齊 `_spawn_units`：lat/lng 是 `float(spec["lat"])`，缺了直接炸）。 */
export interface SpawnUnitSpec {
  designation?: string
  unit_level?: string
  lat?: number
  lng?: number
  strength?: number
  attributes?: Record<string, unknown>
  equipment?: Array<Record<string, unknown>>
}

/** MSEL 注入動作：event_type 必填；payload/faction 選填（faction 省略＝廣播全體）。 */
export interface InjectAction {
  event_type: string
  payload?: Record<string, unknown>
  /** 受眾陣營；同時也是 SPAWN_UNITS 的生成陣營與 MESSAGE 的收件陣營（後端同一個鍵）。 */
  faction?: string
  action?: InjectActionKind
  // MODIFY_UNIT
  unit_id?: string
  strength?: number
  lat?: number
  lng?: number
  // MESSAGE
  to_seat?: string
  body?: string
  // PAUSE
  reason?: string
  // SPAWN_UNITS
  units?: SpawnUnitSpec[]
  // WEATHER_OVERRIDE
  effects?: Record<string, number | boolean>
  duration_ticks?: number
}

/** 各動作**專屬**的 inject 頂層鍵。`faction` 不在此列——它三種語義共用，屬基本欄位。 */
export const INJECT_ACTION_KEYS: Record<InjectActionKind, readonly string[]> = {
  SPAWN_UNITS: ['units'],
  MODIFY_UNIT: ['unit_id', 'strength', 'lat', 'lng'],
  MESSAGE: ['to_seat', 'body'],
  PAUSE: ['reason'],
  WEATHER_OVERRIDE: ['effects', 'duration_ticks'],
}

/** 與動作無關、任何型別都保留的鍵。 */
const INJECT_BASE_KEYS: readonly string[] = ['event_type', 'payload', 'faction']

export const INJECT_ACTION_LABELS: Record<InjectActionKind, string> = {
  SPAWN_UNITS: '增援生成（SPAWN_UNITS）',
  MODIFY_UNIT: '調整單位戰力/位置（MODIFY_UNIT）',
  MESSAGE: '發狀況信文（MESSAGE）',
  PAUSE: '暫停推演（PAUSE）',
  WEATHER_OVERRIDE: '天氣覆蓋（WEATHER_OVERRIDE）',
}

/**
 * 切換注入動作——**把前一個動作的欄位清掉**。
 *
 * 不清的話，把 MODIFY_UNIT 改成 MESSAGE 之後 `unit_id`/`strength` 還留在 inject 裡：
 * 匯出的想定檔多出一組沒人讀的欄位，下一個人打開它會以為那是設定的一部分。
 * 更糟的是改回來時看到的是**上一次的舊值**，而畫面上沒有任何跡象說那是舊的。
 */
export function setInjectAction(base: InjectAction, kind: InjectActionKind | ''): InjectAction {
  const keep = new Set<string>([...INJECT_BASE_KEYS, ...(kind ? INJECT_ACTION_KEYS[kind] : [])])
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(base)) {
    if (keep.has(k)) out[k] = v
  }
  if (kind) out.action = kind
  return out as unknown as InjectAction
}

export interface InjectIssue {
  /** error＝送出去也一定壞（後端會丟例外或整組忽略）；warn＝合法但幾乎不是使用者的本意。 */
  level: 'error' | 'warn'
  text: string
}

/**
 * 送出前的檢查——**每一條都對應後端一個具體的失敗或靜默忽略**。
 *
 * 為什麼要在前端再查一次：MSEL 的注入是在**演習進行中**被觸發的，錯誤那時才會以一筆
 * `MSEL_INJECT_FAILED` 的形式出現在事件流裡（而且只有 `reason` 的類別名可看）。
 * 一個在編輯時就看得到的紅字，跟一個在 D+2 才炸掉的增援，差別是整場演習。
 */
export function injectActionIssues(a: InjectAction): InjectIssue[] {
  const out: InjectIssue[] = []
  if (!a.event_type || !a.event_type.trim()) {
    out.push({ level: 'error', text: '事件型別必填（後端 event_type 至少要一個字）。' })
  }
  if (a.action === 'SPAWN_UNITS') {
    if (!a.faction) out.push({ level: 'error', text: '增援生成必須指定陣營，否則觸發時整條注入失敗。' })
    const units = a.units ?? []
    if (!units.length) out.push({ level: 'error', text: '增援生成至少要有一個單位。' })
    units.forEach((u, i) => {
      if (!Number.isFinite(u.lat) || !Number.isFinite(u.lng)) {
        out.push({ level: 'error', text: `第 ${i + 1} 個增援單位缺經緯度——後端讀不到會整條注入失敗。` })
      }
    })
  }
  if (a.action === 'MODIFY_UNIT') {
    if (!a.unit_id || !a.unit_id.trim()) {
      out.push({ level: 'error', text: '調整單位必須指定 unit_id。' })
    }
    const hasLat = Number.isFinite(a.lat)
    const hasLng = Number.isFinite(a.lng)
    if (hasLat !== hasLng) {
      // 後端是 `if "lat" in inject and "lng" in inject`——只給一個等於兩個都不套用。
      out.push({ level: 'error', text: '經緯度要嘛都填、要嘛都不填：只填一個後端會整組忽略。' })
    }
    if (!Number.isFinite(a.strength) && !hasLat && !hasLng) {
      out.push({ level: 'warn', text: '沒有任何要改的欄位——這條注入只會留下一筆紀錄，單位不會變。' })
    }
  }
  if (a.action === 'MESSAGE') {
    if (!a.faction) out.push({ level: 'error', text: '狀況信文必須指定收件陣營。' })
    if (!a.body || !a.body.trim()) out.push({ level: 'warn', text: '信文內容空白——收件席位會收到一封空信。' })
  }
  if (a.action === 'WEATHER_OVERRIDE' && !a.effects) {
    // 這是後端刻意的語義（見 msel_actions 的註解），不是錯——但一定要講出來。
    out.push({ level: 'warn', text: '未填任何效果＝「解除」既有天氣覆蓋，而不是套一份晴天。' })
  }
  return out
}

/** 有沒有致命問題（表單據此擋下送出）。 */
export function hasBlockingIssue(issues: InjectIssue[]): boolean {
  return issues.some((i) => i.level === 'error')
}

/** 依 type 產生預設 condition（faction 供需陣營的三種類型帶入初值）。 */
export function emptyCondition(type: ConditionType, faction = ''): Condition {
  switch (type) {
    case 'time':
      return { type: 'time', at_tick: 0 }
    case 'faction_eliminated':
      return { type: 'faction_eliminated', faction }
    case 'strength_below':
      return { type: 'strength_below', faction, value: 0 }
    case 'unit_in_region':
      return { type: 'unit_in_region', faction, bbox: [0, 0, 0, 0] }
    case 'all':
      return { type: 'all', of: [] }
    case 'any':
      return { type: 'any', of: [] }
  }
}
