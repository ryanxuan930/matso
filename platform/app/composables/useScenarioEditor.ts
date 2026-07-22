// 想定編輯器模型 + 匯出/匯入（O7.3，SPEC §11.2）——純函數，roundtrip 可測。
// 匯出為 JSON（yaml.safe_load 可讀，與後端 loader 相容）；後端 dump/load roundtrip 另有 Python 測試。
import type { Condition, InjectAction } from '~/composables/useConditionDsl'

export type RelationValue = 'ALLIED' | 'NEUTRAL' | 'HOSTILE'
export type UnitLevel =
  | 'THEATER' | 'CORPS' | 'DIVISION' | 'BRIGADE' | 'BATTALION'
  | 'COMPANY' | 'PLATOON' | 'SQUAD' | 'FIRETEAM' | 'INDIVIDUAL'

export interface EditorFaction { id: string; color?: string }
export interface EditorUnit {
  faction: string
  designation: string
  unitLevel: UnitLevel
  lat?: number
  lng?: number
  parent?: string
}
export interface EditorRelation { a: string; b: string; relation: RelationValue }
export interface EditorMsel { id: string; once: boolean; trigger: Condition; inject: InjectAction }
export interface EditorVictory { faction: string; condition: Record<string, unknown> }

export interface ScenarioModel {
  name: string
  version: string
  bbox: [number, number, number, number]
  mode: 'REALTIME' | 'WEGO' | 'IGO_UGO'
  tickRateMs: number
  factions: EditorFaction[]
  relations: EditorRelation[]
  units: EditorUnit[]
  msel: EditorMsel[]
  victoryConditions: EditorVictory[]
}

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
    victoryConditions: [],
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
    name: m.name,
    version: m.version,
    bbox: m.bbox,
    mode: m.mode,
    tick_rate_ms: m.tickRateMs,
    factions: m.factions.map((f) => (f.color ? { id: f.id, color: f.color } : { id: f.id })),
    relations: m.relations.map((r) => [r.a, r.b, r.relation]),
    victory_conditions: m.victoryConditions.map((v) => ({ faction: v.faction, condition: v.condition })),
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
  return {
    name: s.name as string,
    version: s.version as string,
    bbox: s.bbox as [number, number, number, number],
    mode: (s.mode as ScenarioModel['mode']) ?? 'REALTIME',
    tickRateMs: (s.tick_rate_ms as number) ?? 1000,
    factions: (s.factions as EditorFaction[]).map((f) => ({ id: f.id, color: f.color })),
    relations: ((s.relations as Array<[string, string, RelationValue]>) ?? []).map(([a, b, relation]) => ({ a, b, relation })),
    units,
    msel,
    victoryConditions: ((s.victory_conditions as EditorVictory[]) ?? []),
  }
}
