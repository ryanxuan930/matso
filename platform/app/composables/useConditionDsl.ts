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

/** MSEL 注入動作：event_type 必填；payload/faction 選填（faction 省略＝廣播全體）。 */
export interface InjectAction {
  event_type: string
  payload?: Record<string, unknown>
  faction?: string
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
