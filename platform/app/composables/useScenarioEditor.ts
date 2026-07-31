// 想定編輯器模型 + 匯出/匯入（O7.3，SPEC §11.2）——純函數，roundtrip 可測。
// 匯出為 JSON（yaml.safe_load 可讀，與後端 loader 相容）；後端 dump/load roundtrip 另有 Python 測試。
import type { Condition, InjectAction } from '~/composables/useConditionDsl'

export type RelationValue = 'ALLIED' | 'NEUTRAL' | 'HOSTILE'
export type UnitLevel =
  | 'THEATER'
  | 'ARMY_GROUP'
  | 'ARMY'
  | 'CORPS'
  | 'DIVISION'
  | 'BRIGADE'
  | 'REGIMENT'
  | 'BATTALION'
  | 'COMPANY'
  | 'PLATOON'
  | 'SECTION'
  | 'SQUAD'
  | 'FIRETEAM'
  | 'INDIVIDUAL'

export interface EditorFaction { id: string; displayName?: string; color?: string }
export interface EditorUnit {
  faction: string
  designation: string
  unitLevel: UnitLevel
  lat?: number
  lng?: number
  parent?: string
  fixed?: boolean // 固定單位（指揮部等）：不接受 MOVE 令、不會被派去移動或機動交戰
  branch?: string // 兵科：決定地圖符號的圖示（步兵斜線/裝甲橢圓/砲兵圓點…）。UNKNOWN＝通用框
  /**
   * 編裝（WP-B6）。**這是「編輯器能不能獨立產出一份能打的想定」的分水嶺**——
   * 沒有編裝的單位打不了仗（`ENGAGE` 找不到武器、`precheck` 直接不可行）。
   *
   * `template` 參照 `EquipmentTemplate.name`（不是 id）。打錯字要到**開局**才報錯，
   * 所以 UI 一律從軍械庫清單挑，不讓人手打。
   * **省略整個欄位 ≠ 空陣列**：省略＝沿用開局旗標 `seed_default_equipment` 的預設配發，
   * 空陣列＝這支單位刻意什麼都不帶。兩者在 export 時要分得開。
   */
  equipment?: EditorEquipment[]
  /**
   * 本檔未建模的單位欄位（`equipment`、`attributes`、`authorized_strength`…）。
   * **匯入時原樣收起、匯出時原樣攤回**——理由同 `ScenarioModel.passthrough`：
   * 編輯器只管得到 7 個欄位，而 `orbat.schema.json` 的單位遠不只 7 個。
   * 不收的話，一支帶著完整編裝與補給宣告的單位在編輯器裡開一次再存回去，
   * 就只剩番號與座標——而畫面上完全看不出東西掉了。
   */
  passthrough?: Record<string, unknown>
}

/** 編輯器**明確建模**的單位鍵（snake_case，＝ bundle 裡的形狀）。其餘走單位的 `passthrough`。 */
const MODELLED_UNIT_KEYS = new Set([
  'designation',
  'unit_level',
  'lat',
  'lng',
  'parent',
  'fixed',
  'branch',
  'equipment',
])
/** 一件編裝。`ammo` 省略＝用範本預設攜行量。 */
export interface EditorEquipment { template: string; quantity?: number; ammo?: number }
export interface EditorRelation { a: string; b: string; relation: RelationValue }

/**
 * 一條武器禁令（`roe.schema.json` 的 `weapon_restrictions[]`）。
 *
 * `reason` **必填**——schema 的說明寫著「AAR 要能回答『為什麼這場不准用飛彈』；
 * 無理由的限制在事後檢討時無法評量」。編輯器照這條走：沒填理由就擋下存檔。
 * `faction` 省略＝全陣營適用。`forbid_categories` 與 `forbid_templates` 至少要有一項。
 */
export interface EditorWeaponRestriction {
  faction?: string
  forbid_categories?: string[]
  forbid_templates?: string[]
  reason: string
}
/** 想定的交戰規則（bundle 的 `roe` 區段）。 */
export interface EditorRoe {
  version?: string
  default_fire_policy?: Record<string, string>
  weapon_restrictions?: EditorWeaponRestriction[]
}
export const FIRE_POLICIES: string[] = ['FREE', 'SMALL_ARMS_ONLY', 'ANTI_ARMOR_HOLD']
export const FORBIDDABLE_CATEGORIES: string[] = ['KINETIC', 'MISSILE', 'ARTILLERY', 'DRONE']
export interface EditorMsel { id: string; once: boolean; trigger: Condition; inject: InjectAction }
export interface EditorVictory { faction: string; condition: Condition }

/**
 * 申請單配額（WP-B5.2）。**未列＝不限**——所以「不限」在模型裡是 `undefined` 而不是 0。
 * 0 的意思是「一張都不准申請」；把兩者混在一起，C2 面板會把「沒設定」畫成「額度用罄」。
 */
export interface EditorRequestQuotas {
  AIR_RECON?: number
  FIRE_SUPPORT?: number
  RESUPPLY_VOUCHER?: number
}
/**
 * 可配額的申請種類。**刻意只有三種**：`CALL_FOR_FIRE` 雖然也是 RequestKind，
 * 但 scenario.schema.json 的 `request_quotas` 是 `additionalProperties: false` 且沒開放它，
 * 編輯器多給一格就會產出 loader 直接拒載的想定。
 */
export const REQUEST_QUOTA_KINDS = ['AIR_RECON', 'FIRE_SUPPORT', 'RESUPPLY_VOUCHER'] as const
export type RequestQuotaKind = (typeof REQUEST_QUOTA_KINDS)[number]

/** 晝夜宣告（WP-C4a）。時刻皆為「當日分鐘數」（0＝00:00，1439＝23:59）。 */
export interface EditorDayNight {
  sunriseMin: number
  sunsetMin: number
  /** 開演時刻（tick 0 對應的當日分鐘）。未宣告＝午夜開演。 */
  startMin?: number
}
/** 啟用晝夜時的預設：06:00 日出、18:00 日落（與一般夜暗判定的直覺一致，作者可再調）。 */
export const DAY_NIGHT_DEFAULTS: Pick<EditorDayNight, 'sunriseMin' | 'sunsetMin'> = {
  sunriseMin: 6 * 60,
  sunsetMin: 18 * 60,
}

/** 陣地變換（WP-C10.5）。三個參數留 undefined ＝ 沿用後端預設（見 SURVIVABILITY_DEFAULTS）。 */
export interface EditorSurvivabilityMove {
  enabled: boolean
  missionsBeforeMove?: number
  minKm?: number
  maxKm?: number
}
/**
 * **必須與 `core/app/fires/survivability.py` 的 `_DEFAULT_*` 一致。**
 * 這裡是 UI 勾選「啟用」時要寫進想定的初值；兩邊漂掉的話，想定作者以為自己維持預設，
 * 實際上寫下的是另一組數字（而且不會有任何錯誤訊息）。
 */
export const SURVIVABILITY_DEFAULTS = { missionsBeforeMove: 3, minKm: 1, maxKm: 2 } as const

export interface ScenarioModel {
  name: string
  version: string
  description?: string
  bbox: [number, number, number, number]
  mode: 'REALTIME' | 'WEGO' | 'IGO_UGO'
  tickRateMs: number
  hexResolution?: number
  aggregateAdjudicationLevel?: 'BATTALION' | 'BRIGADE' | 'DIVISION'
  factions: EditorFaction[]
  relations: EditorRelation[]
  units: EditorUnit[]
  msel: EditorMsel[]
  victoryConditions: EditorVictory[]
  // ↓ 以下五項是 scenario.schema.json 的頂層設定。**過去只有 passthrough 保住它們不遺失，
  // 卻沒有任何介面設得到**——想開誤傷裁決或給空偵配額，只能去「匯入 JSON」文字框手貼。
  /** 申請單配額（整局總量，不是每日）。undefined ＝ 三種申請皆不限。 */
  requestQuotas?: EditorRequestQuotas
  /** 晝夜。undefined ＝ 未宣告 ＝ 整場白天（光照係數恆 1.0）。 */
  dayNight?: EditorDayNight
  /** 友軍誤傷裁決。undefined/false ＝ 對友軍/盟軍的 ENGAGE 一律被 ROE 拒絕。 */
  allowFratricide?: boolean
  /** 曲射火協。undefined/false ＝ ARTILLERY/MISSILE 開火不須掛核准的火力支援申請單。 */
  indirectFireRequiresApproval?: boolean
  /** 陣地變換。undefined ＝ 停用。 */
  survivabilityMove?: EditorSurvivabilityMove
  // WP-B6：編輯器不編輯禁射區（那是 COP 地圖編輯器的事，WP-A3），但**必須原樣帶著**。
  // 過去這裡沒有這個欄位 → 用編輯器開一個有保護區的想定再存回去，禁射區整段消失，
  // 而且不會有任何錯誤訊息。安全機制的沉默失效。
  noStrikeZones?: Array<Record<string, unknown>>
  /**
   * **編輯器不認得的 scenario 鍵，原樣帶著。**
   *
   * ⚠ 同一個 bug 已經咬過三次：後端的 `scenario_to_dict` 手寫白名單、
   * 後端的 `clone_session` 漏七個欄、以及這裡。每次的修法都是「再列一個欄位」，
   * 於是下一個新設定又會被下一個人忘記。
   *
   * 所以這次改成**結構性**的：import 時把所有沒被模型吃掉的鍵收進這裡，
   * export 時先攤開它再覆蓋明確欄位。**任何未來的想定設定都會自動存活**，
   * 不需要有人記得回來改。
   *
   * ⚠ 但 passthrough 只保證**不遺失**，不等於**設得到**：`request_quotas`、
   * `day_night`、`allow_fratricide`、`indirect_fire_requires_approval`、
   * `survivability_move` 一度全靠這裡活著，卻沒有任何介面能設定它們（E6）。
   * 現在五項都已明確建模並列入 `MODELLED_SCENARIO_KEYS`——**新增建模欄位時務必同步加進去**，
   * 否則舊值會留在 passthrough，使用者在 UI 清空該設定時被舊值靜默復活。
   */
  passthrough?: Record<string, unknown>
  /**
   * **bundle 頂層**未建模的區段，原樣帶著（目前是 `roe` 與 `overrides`）。
   *
   * ⚠ 與上面那個 `passthrough` 是兩件事，別合併：那個救的是 `scenario` **裡面**的鍵，
   * 這個救的是 `scenario` 的**兄弟**。`roe`（陣營交戰規則，例如「本局禁用 MLRS」）
   * 與 `overrides`（機動覆寫矩陣）都是 bundle 的頂層區段，
   * 於是「scenario 內部的未知鍵會自動存活」這個保證對它們完全不適用——
   * 用編輯器開一個有 ROE 禁令的想定再存回去，**禁令整段消失且沒有任何錯誤訊息**。
   *
   * 後端的 `api/scenarios.ScenarioBundle` 已經修好會收這兩段（它一度也在丟），
   * 洞剩在這裡：前端根本沒把它們放進送出去的 bundle。
   */
  bundlePassthrough?: Record<string, unknown>
  /**
   * 交戰規則（bundle 的 `roe` 區段）。**未宣告＝這一局沒有 ROE 限制**，
   * 與「宣告了但清空」不同——後者是作者刻意把限制拿掉，AAR 讀得出差別。
   */
  roe?: EditorRoe
}

/** bundle 頂層由編輯器**明確處理**的區段。其餘走 `bundlePassthrough`。 */
const MODELLED_BUNDLE_KEYS = new Set(['scenario', 'orbat', 'msel', 'roe'])

/** 編輯器**明確建模**的 scenario 鍵。其餘一律走 `passthrough`。 */
const MODELLED_SCENARIO_KEYS = new Set([
  'name', 'version', 'description', 'bbox', 'mode', 'tick_rate_ms',
  'hex_resolution', 'aggregate_adjudication_level', 'factions', 'relations',
  'victory_conditions', 'no_strike_zones', 'files',
  // E6：這五項現在有 UI 了，所以必須離開 passthrough——否則 UI 關掉某項設定時，
  // passthrough 裡的舊值會在匯出時被攤開，把使用者剛關掉的設定原封不動寫回去。
  'request_quotas', 'day_night', 'allow_fratricide',
  'indirect_fire_requires_approval', 'survivability_move',
])

export function emptyScenario(): ScenarioModel {
  return {
    name: 'New Scenario',
    version: '1.0',
    // 花蓮一帶的預設戰場範圍。**只是起手值**——編輯器的「戰場範圍」欄位可改（E7）；
    // 在補上欄位之前，用編輯器新建的想定戰場永遠是這四個數字，要換戰場只能去改 JSON。
    bbox: [120.9, 23.6, 121.4, 23.9],
    mode: 'REALTIME',
    tickRateMs: 60000,  // 1 tick ＝ 1 分模擬時間（此值會決定執行期節奏）
    factions: [{ id: 'BLUE', color: '#3b7dd8' }, { id: 'RED', color: '#d83b3b' }],
    relations: [{ a: 'BLUE', b: 'RED', relation: 'HOSTILE' }],
    units: [],
    msel: [],
    // 想定規格要求至少一條勝負條件（victory_conditions minItems=1）；預設「藍軍於紅軍被殲滅時獲勝」，
    // 使新想定可直接存檔，使用者再依需求增修。
    victoryConditions: [{ faction: 'BLUE', condition: { type: 'faction_eliminated', faction: 'RED' } }],
  }
}

/** 整數欄位的匯出守則：非數值/NaN 一律當「沒填」（絕不寫 null/NaN 進想定，那會被 loader 拒載）。 */
function intOrUndefined(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? Math.trunc(v) : undefined
}

/**
 * 只寫「填了合法整數」的種類；三種都沒填就整段不寫。
 * 空 `{}` 雖過得了 schema，但寫進想定會讓讀的人以為配了額度，其實三種都是不限。
 */
function exportQuotas(q: EditorRequestQuotas | undefined): Record<string, number> | undefined {
  if (!q) return undefined
  const out: Record<string, number> = {}
  for (const kind of REQUEST_QUOTA_KINDS) {
    const n = intOrUndefined(q[kind])
    if (n !== undefined && n >= 0) out[kind] = n
  }
  return Object.keys(out).length ? out : undefined
}

/**
 * 晝夜：schema 要求 sunrise/sunset **同時**存在。UI 勾選啟用時就補齊兩者，
 * 這裡的守則是保險——半殘的宣告會讓存檔整份失敗，寧可當作未宣告。
 */
function exportDayNight(d: EditorDayNight | undefined): Record<string, number> | undefined {
  if (!d) return undefined
  const sunrise = intOrUndefined(d.sunriseMin)
  const sunset = intOrUndefined(d.sunsetMin)
  if (sunrise === undefined || sunset === undefined) return undefined
  const start = intOrUndefined(d.startMin)
  return {
    sunrise_min: sunrise,
    sunset_min: sunset,
    ...(start !== undefined ? { start_min: start } : {}),
  }
}

/**
 * 陣地變換：`enabled=false` 與整段缺席在後端是同一件事（停用），所以停用時不寫。
 * 三個參數留空＝沿用後端預設，故只寫使用者實際填過的（維持既有想定的 diff 乾淨）。
 */
function exportSurvivability(
  s: EditorSurvivabilityMove | undefined,
): Record<string, unknown> | undefined {
  if (!s?.enabled) return undefined
  const missions = intOrUndefined(s.missionsBeforeMove)
  const min = typeof s.minKm === 'number' && Number.isFinite(s.minKm) ? s.minKm : undefined
  const max = typeof s.maxKm === 'number' && Number.isFinite(s.maxKm) ? s.maxKm : undefined
  return {
    enabled: true,
    ...(missions !== undefined ? { missions_before_move: missions } : {}),
    ...(min !== undefined ? { min_km: min } : {}),
    ...(max !== undefined ? { max_km: max } : {}),
  }
}

/** 編輯器模型 → scenario package bundle（scenario/orbat/msel 三段，後端 loader 可讀的 JSON）。 */
export function exportScenario(m: ScenarioModel): Record<string, unknown> & {
  scenario: Record<string, unknown>
  orbat: Record<string, unknown>
  msel: Record<string, unknown>
} {
  const factionsWithUnits = [...new Set(m.units.map((u) => u.faction))]
  const quotas = exportQuotas(m.requestQuotas)
  const dayNight = exportDayNight(m.dayNight)
  const survivability = exportSurvivability(m.survivabilityMove)
  const scenario: Record<string, unknown> = {
    // **先攤開 passthrough**——明確欄位在後面覆蓋它，所以編輯器管的欄位仍以模型為準，
    // 而它不管的（ROE/配額/火協開關/陣地變換/誤傷/晝夜…）原樣存活。
    ...(m.passthrough ?? {}),
    name: m.name,
    version: m.version,
    ...(m.description !== undefined ? { description: m.description } : {}),
    bbox: m.bbox,
    mode: m.mode,
    tick_rate_ms: m.tickRateMs,
    ...(m.hexResolution !== undefined ? { hex_resolution: m.hexResolution } : {}),
    ...(m.aggregateAdjudicationLevel !== undefined
      ? { aggregate_adjudication_level: m.aggregateAdjudicationLevel }
      : {}),
    factions: m.factions.map((f) => ({
      id: f.id,
      ...(f.displayName ? { display_name: f.displayName } : {}),
      ...(f.color ? { color: f.color } : {}),
    })),
    relations: m.relations.map((r) => [r.a, r.b, r.relation]),
    victory_conditions: m.victoryConditions.map((v) => ({ faction: v.faction, condition: v.condition })),
    ...(m.noStrikeZones?.length ? { no_strike_zones: m.noStrikeZones } : {}),
    // 五項想定設定：**未設定就整個不寫**，而不是寫 false/{}。
    // 後端對「缺席」與「false/空」語意相同，但想定是給人讀的文件——
    // 寫一堆 false 會讓作者以為那些機制被刻意關掉，其實只是沒碰過。
    ...(quotas ? { request_quotas: quotas } : {}),
    ...(dayNight ? { day_night: dayNight } : {}),
    ...(m.allowFratricide ? { allow_fratricide: true } : {}),
    ...(m.indirectFireRequiresApproval ? { indirect_fire_requires_approval: true } : {}),
    ...(survivability ? { survivability_move: survivability } : {}),
    files: {
      ...(factionsWithUnits.length
        ? { orbat: Object.fromEntries(factionsWithUnits.map((f) => [f, `orbat/${f.toLowerCase()}.yaml`])) }
        : {}),
      ...(m.msel.length ? { msel: 'msel.yaml' } : {}),
    },
  }
  const orbat = Object.fromEntries(
    factionsWithUnits.map((f) => [
      f,
      {
        faction: f,
        units: m.units
          .filter((u) => u.faction === f)
          .map((u) => ({
            // **先攤開單位的 passthrough**，明確欄位在後面覆蓋——同 scenario 的處理。
            // 編輯器只管 7 個欄位，`equipment`／`attributes`／`authorized_strength` 等
            // 全靠這裡活著。
            ...(u.passthrough ?? {}),
            designation: u.designation,
            unit_level: u.unitLevel,
            ...(u.lat !== undefined ? { lat: u.lat } : {}),
            ...(u.lng !== undefined ? { lng: u.lng } : {}),
            ...(u.parent ? { parent: u.parent } : {}),
            ...(u.fixed ? { fixed: true } : {}),
            // 兵科：UNKNOWN 是預設，省略即等價（維持既有想定的 diff 乾淨）。
            ...(u.branch && u.branch !== 'UNKNOWN' ? { branch: u.branch } : {}),
            // **undefined ≠ []**：省略＝沿用開局的預設配發；空陣列＝刻意什麼都不帶。
            ...(u.equipment !== undefined
              ? {
                  equipment: u.equipment.map((e) => ({
                    template: e.template,
                    ...(e.quantity !== undefined ? { quantity: e.quantity } : {}),
                    ...(e.ammo !== undefined ? { ammo: e.ammo } : {}),
                  })),
                }
              : {}),
          })),
      },
    ]),
  )
  const msel = {
    events: m.msel.map((e) => ({ id: e.id, once: e.once, trigger: e.trigger, inject: e.inject })),
  }
  // bundle 頂層的未建模區段（`roe`／`overrides`）攤在最前面，
  // 三個明確區段在後面覆蓋——順序與上面兩處 passthrough 一致。
  return { ...(m.bundlePassthrough ?? {}), scenario, orbat, msel, ...(m.roe ? { roe: m.roe } : {}) }
}

/** 匯入端的數值守則：想定裡的字串數字（YAML 手寫常見）也吃，其餘一律當沒填。 */
function numOrUndefined(v: unknown): number | undefined {
  const n = typeof v === 'string' ? Number(v) : v
  return typeof n === 'number' && Number.isFinite(n) ? n : undefined
}

function importQuotas(raw: unknown): EditorRequestQuotas | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const src = raw as Record<string, unknown>
  const out: EditorRequestQuotas = {}
  for (const kind of REQUEST_QUOTA_KINDS) {
    const n = numOrUndefined(src[kind])
    if (n !== undefined) out[kind] = Math.trunc(n)
  }
  return Object.keys(out).length ? out : undefined
}

function importDayNight(raw: unknown): EditorDayNight | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const src = raw as Record<string, unknown>
  const sunrise = numOrUndefined(src.sunrise_min)
  const sunset = numOrUndefined(src.sunset_min)
  if (sunrise === undefined || sunset === undefined) return undefined
  const start = numOrUndefined(src.start_min)
  return {
    sunriseMin: Math.trunc(sunrise),
    sunsetMin: Math.trunc(sunset),
    ...(start !== undefined ? { startMin: Math.trunc(start) } : {}),
  }
}

/** 未填的參數保持 undefined（＝沿用後端預設），不要在匯入時補成常數：
 *  補了會讓「作者沒指定」變成「作者指定了這個值」，日後改預設就改不動這些想定。 */
function importSurvivability(raw: unknown): EditorSurvivabilityMove | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const src = raw as Record<string, unknown>
  const missions = numOrUndefined(src.missions_before_move)
  return {
    enabled: Boolean(src.enabled),
    ...(missions !== undefined ? { missionsBeforeMove: Math.trunc(missions) } : {}),
    ...(numOrUndefined(src.min_km) !== undefined ? { minKm: numOrUndefined(src.min_km) } : {}),
    ...(numOrUndefined(src.max_km) !== undefined ? { maxKm: numOrUndefined(src.max_km) } : {}),
  }
}

/** bundle → 編輯器模型（匯入；exportScenario 的逆）。 */
export function importScenario(
  bundle: Record<string, unknown> & {
    scenario: Record<string, unknown>
    orbat?: Record<string, { faction: string; units: Array<Record<string, unknown>> }>
    msel?: {
      events?: Array<{ id: string; once?: boolean; trigger: Condition; inject: InjectAction }>
    }
  },
): ScenarioModel {
  const s = bundle.scenario
  const units: EditorUnit[] = []
  for (const ob of Object.values(bundle.orbat ?? {})) {
    for (const u of ob.units) {
      const unitRest: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(u)) {
        if (!MODELLED_UNIT_KEYS.has(key)) unitRest[key] = value
      }
      units.push({
        faction: ob.faction,
        designation: u.designation as string,
        unitLevel: u.unit_level as UnitLevel,
        lat: u.lat as number | undefined,
        lng: u.lng as number | undefined,
        parent: u.parent as string | undefined,
        fixed: u.fixed as boolean | undefined,
        branch: (u.branch as string | undefined) ?? 'UNKNOWN',
        ...(Array.isArray(u.equipment)
          ? { equipment: (u.equipment as EditorEquipment[]).map((e) => ({ ...e })) }
          : {}),
        ...(Object.keys(unitRest).length ? { passthrough: unitRest } : {}),
      })
    }
  }
  const bundlePassthrough: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(bundle)) {
    if (!MODELLED_BUNDLE_KEYS.has(key)) bundlePassthrough[key] = value
  }
  // 由 bundle.msel.events 重建 EditorMsel[]（once 缺省 → true，對齊後端 loader）。
  const msel: EditorMsel[] = (bundle.msel?.events ?? []).map((e) => ({
    id: e.id,
    once: e.once ?? true,
    trigger: e.trigger,
    inject: e.inject,
  }))
  const passthrough: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(s)) {
    if (!MODELLED_SCENARIO_KEYS.has(key)) passthrough[key] = value
  }
  const requestQuotas = importQuotas(s.request_quotas)
  const dayNight = importDayNight(s.day_night)
  const survivabilityMove = importSurvivability(s.survivability_move)
  return {
    name: s.name as string,
    version: s.version as string,
    ...(s.description !== undefined ? { description: s.description as string } : {}),
    bbox: s.bbox as [number, number, number, number],
    mode: (s.mode as ScenarioModel['mode']) ?? 'REALTIME',
    tickRateMs: (s.tick_rate_ms as number) ?? 60000,
    ...(s.hex_resolution !== undefined ? { hexResolution: s.hex_resolution as number } : {}),
    ...(s.aggregate_adjudication_level !== undefined
      ? { aggregateAdjudicationLevel: s.aggregate_adjudication_level as ScenarioModel['aggregateAdjudicationLevel'] }
      : {}),
    factions: (s.factions as Array<{ id: string; display_name?: string; color?: string }>).map((f) => ({
      id: f.id,
      ...(f.display_name ? { displayName: f.display_name } : {}),
      ...(f.color ? { color: f.color } : {}),
    })),
    relations: ((s.relations as Array<[string, string, RelationValue]>) ?? []).map(([a, b, relation]) => ({ a, b, relation })),
    units,
    msel,
    victoryConditions: ((s.victory_conditions as EditorVictory[]) ?? []),
    ...(Array.isArray(s.no_strike_zones) && s.no_strike_zones.length
      ? { noStrikeZones: s.no_strike_zones as Array<Record<string, unknown>> }
      : {}),
    // 五項想定設定：缺席就保持 undefined（＝未宣告），不要補成 false/{}——
    // 匯入即改寫想定語意，是這個 repo 反覆出事的那種「靜默變更」。
    ...(requestQuotas ? { requestQuotas } : {}),
    ...(dayNight ? { dayNight } : {}),
    ...(s.allow_fratricide !== undefined ? { allowFratricide: Boolean(s.allow_fratricide) } : {}),
    ...(s.indirect_fire_requires_approval !== undefined
      ? { indirectFireRequiresApproval: Boolean(s.indirect_fire_requires_approval) }
      : {}),
    ...(survivabilityMove ? { survivabilityMove } : {}),
    ...(Object.keys(passthrough).length ? { passthrough } : {}),
    ...(Object.keys(bundlePassthrough).length ? { bundlePassthrough } : {}),
    ...(bundle.roe && typeof bundle.roe === 'object' ? { roe: bundle.roe as EditorRoe } : {}),
  }
}
