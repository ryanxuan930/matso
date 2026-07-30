// 想定編輯器模型 + 匯出/匯入（O7.3，SPEC §11.2）——純函數，roundtrip 可測。
// 匯出為 JSON（yaml.safe_load 可讀，與後端 loader 相容）；後端 dump/load roundtrip 另有 Python 測試。
import type { Condition, InjectAction } from '~/composables/useConditionDsl'

export type RelationValue = 'ALLIED' | 'NEUTRAL' | 'HOSTILE'
export type UnitLevel =
  | 'THEATER' | 'CORPS' | 'DIVISION' | 'BRIGADE' | 'BATTALION'
  | 'COMPANY' | 'PLATOON' | 'SQUAD' | 'FIRETEAM' | 'INDIVIDUAL'

export interface EditorFaction { id: string; displayName?: string; color?: string }
export interface EditorUnit {
  faction: string
  designation: string
  unitLevel: UnitLevel
  lat?: number
  lng?: number
  parent?: string
  fixed?: boolean // 固定單位（指揮部等）：不接受 MOVE 令、不會被派去移動或機動交戰
}
export interface EditorRelation { a: string; b: string; relation: RelationValue }
export interface EditorMsel { id: string; once: boolean; trigger: Condition; inject: InjectAction }
export interface EditorVictory { faction: string; condition: Condition }

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
  // WP-B6：編輯器不編輯禁射區（那是 COP 地圖編輯器的事，WP-A3），但**必須原樣帶著**。
  // 過去這裡沒有這個欄位 → 用編輯器開一個有保護區的想定再存回去，禁射區整段消失，
  // 而且不會有任何錯誤訊息。安全機制的沉默失效。
  noStrikeZones?: Array<Record<string, unknown>>
  /**
   * **編輯器不認得的 scenario 鍵，原樣帶著。**
   *
   * ⚠ 同一個 bug 已經咬過三次：後端的 `scenario_to_dict` 手寫白名單、
   * 後端的 `clone_session` 漏七個欄、以及這裡。每次的修法都是「再列一個欄位」，
   * 於是下一個新設定又會被下一個人忘記——`request_quotas`、
   * `indirect_fire_requires_approval`、`survivability_move` 現在就正在被漏掉。
   *
   * 所以這次改成**結構性**的：import 時把所有沒被模型吃掉的鍵收進這裡，
   * export 時先攤開它再覆蓋明確欄位。**任何未來的想定設定都會自動存活**，
   * 不需要有人記得回來改。
   */
  passthrough?: Record<string, unknown>
}

/** 編輯器**明確建模**的 scenario 鍵。其餘一律走 `passthrough`。 */
const MODELLED_SCENARIO_KEYS = new Set([
  'name', 'version', 'description', 'bbox', 'mode', 'tick_rate_ms',
  'hex_resolution', 'aggregate_adjudication_level', 'factions', 'relations',
  'victory_conditions', 'no_strike_zones', 'files',
])

export function emptyScenario(): ScenarioModel {
  return {
    name: 'New Scenario',
    version: '1.0',
    bbox: [120.9, 23.6, 121.4, 23.9],
    mode: 'REALTIME',
    tickRateMs: 1000,
    factions: [{ id: 'BLUE', color: '#3b7dd8' }, { id: 'RED', color: '#d83b3b' }],
    relations: [{ a: 'BLUE', b: 'RED', relation: 'HOSTILE' }],
    units: [],
    msel: [],
    // 想定規格要求至少一條勝負條件（victory_conditions minItems=1）；預設「藍軍於紅軍被殲滅時獲勝」，
    // 使新想定可直接存檔，使用者再依需求增修。
    victoryConditions: [{ faction: 'BLUE', condition: { type: 'faction_eliminated', faction: 'RED' } }],
  }
}

/** 編輯器模型 → scenario package bundle（scenario/orbat/msel 三段，後端 loader 可讀的 JSON）。 */
export function exportScenario(m: ScenarioModel): {
  scenario: Record<string, unknown>
  orbat: Record<string, unknown>
  msel: Record<string, unknown>
} {
  const factionsWithUnits = [...new Set(m.units.map((u) => u.faction))]
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
            designation: u.designation,
            unit_level: u.unitLevel,
            ...(u.lat !== undefined ? { lat: u.lat } : {}),
            ...(u.lng !== undefined ? { lng: u.lng } : {}),
            ...(u.parent ? { parent: u.parent } : {}),
            ...(u.fixed ? { fixed: true } : {}),
          })),
      },
    ]),
  )
  const msel = {
    events: m.msel.map((e) => ({ id: e.id, once: e.once, trigger: e.trigger, inject: e.inject })),
  }
  return { scenario, orbat, msel }
}

/** bundle → 編輯器模型（匯入；exportScenario 的逆）。 */
export function importScenario(bundle: {
  scenario: Record<string, unknown>
  orbat?: Record<string, { faction: string; units: Array<Record<string, unknown>> }>
  msel?: { events?: Array<{ id: string; once?: boolean; trigger: Condition; inject: InjectAction }> }
}): ScenarioModel {
  const s = bundle.scenario
  const units: EditorUnit[] = []
  for (const ob of Object.values(bundle.orbat ?? {})) {
    for (const u of ob.units) {
      units.push({
        faction: ob.faction,
        designation: u.designation as string,
        unitLevel: u.unit_level as UnitLevel,
        lat: u.lat as number | undefined,
        lng: u.lng as number | undefined,
        parent: u.parent as string | undefined,
        fixed: u.fixed as boolean | undefined,
      })
    }
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
  return {
    name: s.name as string,
    version: s.version as string,
    ...(s.description !== undefined ? { description: s.description as string } : {}),
    bbox: s.bbox as [number, number, number, number],
    mode: (s.mode as ScenarioModel['mode']) ?? 'REALTIME',
    tickRateMs: (s.tick_rate_ms as number) ?? 1000,
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
    ...(Object.keys(passthrough).length ? { passthrough } : {}),
  }
}
