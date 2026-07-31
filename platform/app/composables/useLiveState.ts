/**
 * 「活值」讀取器——同一條規則的九個實例：**STATE_DIFF 串流推來的值優先，否則退回
 * GET /units 的初值**。分散在頁面裡時看不出它們是同一件事，湊在一起就一目了然，
 * 也讓下一個活值欄位知道該照哪個樣子加。
 *
 * `liveStaleTick` 是唯一的例外，而且是**踩過坑才長成這樣**：它以「patch 有沒有這個鍵」
 * 為準，而不是 `typeof === 'number'`。因為恢復通聯時後端送的是 `stale_since_tick: null`，
 * 用型別判斷會讓它退回快照裡的舊數字，單位就永遠掛著「失聯」標籤拿不掉。
 * 其餘欄位沒有「以 null 表達已解除」的語意，故維持型別判斷。
 */
import { computed } from 'vue'
import type { components } from '~/types/api'
import type { UnitView } from '~/composables/useOrders'
import type { useSessionStreamStore } from '~/stores/sessionStream'

export type SupplyLevelView = components['schemas']['SupplyLevelView']
/** 顯示順序（＝北約編號順序）。後端 `/units` 已照此排序；熱狀態 patch 則是字典序。 */
const SUPPLY_ORDER = ['I', 'III', 'V', 'IX']

export function useLiveState(stream: ReturnType<typeof useSessionStreamStore>) {
  // 活模擬位置（O10.1）：優先用 STATE_DIFF 累積的最新座標，否則用 GET /units 的初始座標。
  function livePos(u: UnitView): { lat: number; lng: number } {
    const p = stream.unitPatches[u.id]
    return {
      lat: (typeof p?.lat === 'number' ? p.lat : u.lat) ?? 23.7,
      lng: (typeof p?.lng === 'number' ? p.lng : u.lng) ?? 121,
    }
  }
  // 活血量（#5）：交戰 HIT 後由 STATE_DIFF 帶入，否則用 GET /units 初始值。
  function liveHealth(u: UnitView): number | undefined {
    const p = stream.unitPatches[u.id]
    return (typeof p?.health === 'number' ? p.health : u.health) ?? undefined
  }
  // 活戰力（真實化交戰）：STATE_DIFF 帶入的當前戰力優先，否則 GET /units 初值。
  function liveStrength(u: UnitView): number | undefined {
    const p = stream.unitPatches[u.id]
    const s = (typeof p?.strength === 'number' ? p.strength : u.strength) as number | undefined
    return s
  }
  // 活通聯狀態（#33 comms 子系統）：STATE_DIFF 的 comms_state 優先，否則單位初值。
  function liveComms(u: UnitView): string {
    const p = stream.unitPatches[u.id]
    return (typeof p?.comms_state === 'string' ? p.comms_state : (u.comms ?? 'ONLINE')) as string
  }
  // #84 活油料：STATE_DIFF 串流的 fuel（移動耗油/補給加油即時反映）。無值＝徒步/無油料模型。
  function liveFuel(unitId: string | null): number | null {
    const f = stream.unitPatches[unitId ?? '']?.fuel
    return typeof f === 'number' ? f : null
  }
  /**
   * 活壓制度（WP-C1）。0＝無壓制；被命中累積、停火後每 tick 衰減。
   *
   * **敵軍單位一律讀到 0**——後端不供應（`/units` 的 fog 規則 + STATE_DIFF 的可見集投影）。
   * 這裡不做任何過濾：fog of war 只在後端（紅線 3）。
   */
  function liveSuppression(u: UnitView): number {
    const p = stream.unitPatches[u.id]
    const raw = typeof p?.suppression === 'number' ? p.suppression : u.suppression
    return typeof raw === 'number' ? raw : 0
  }
  /** 活姿態（WP-C1）。**已就位**的那一級，不是正在挖的目標。 */
  function livePosture(u: UnitView): string {
    const p = stream.unitPatches[u.id]
    const raw = typeof p?.posture === 'string' ? p.posture : u.posture
    return typeof raw === 'string' && raw ? raw : 'MOVING'
  }
  /**
   * 活補給水位（WP-C7）。空陣列＝**這個單位沒有宣告任何補給類別**，不是「全部見底」。
   *
   * **兩種形狀收在這一個函式裡**，這是本函式存在的全部理由：
   * STATE_DIFF 推來的是熱狀態原形 `{"I": [存量, 容量]}`（那個二元陣列是後端為了
   * 雜湊穩定而定的編碼），`GET /units` 的快照給的是 `SupplyLevelView[]`。
   * 兩種形狀若各自散在樣板裡解一次，遲早有一邊解錯——而解錯的症狀是「水位永遠不動」，
   * 看起來與「補給系統沒生效」一模一樣。
   *
   * fog of war 不在這裡做（紅線 3）：他方單位的 `/units` 回空陣列、STATE_DIFF 整筆不投影。
   */
  function liveSupply(u: UnitView): SupplyLevelView[] {
    const raw = stream.unitPatches[u.id]?.supply
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      const out: SupplyLevelView[] = []
      for (const cls of SUPPLY_ORDER) {
        const pair = (raw as Record<string, unknown>)[cls]
        if (!Array.isArray(pair) || pair.length < 2) continue
        const onHand = Number(pair[0])
        const capacity = Number(pair[1])
        // 未編制（capacity <= 0）不列——與後端 `SupplyLevel.declared` 同一條規則。
        if (!Number.isFinite(onHand) || !Number.isFinite(capacity) || capacity <= 0) continue
        out.push({
          supply_class: cls as SupplyLevelView['supply_class'],
          on_hand: onHand,
          capacity,
          fraction: Math.max(0, Math.min(1, onHand / capacity)),
        })
      }
      return out
    }
    return u.supply ?? []
  }
  /** 活斷補天數（WP-C7）。>0 ＝ Class I 見底且持續中；補到一次即歸零。 */
  function liveStarvedDays(u: UnitView): number {
    const p = stream.unitPatches[u.id]
    const raw = typeof p?.starved_days === 'number' ? p.starved_days : u.starved_days
    return typeof raw === 'number' && raw > 0 ? raw : 0
  }
  /**
   * 位置凍結的時間戳（WP-C5）。非 null ＝ 圖上的座標是**最後一次位置回報**而非真實位置。
   *
   * patch 只要**有這個鍵**就以它為準（含恢復通聯時送來的 null）——只看 `typeof === 'number'`
   * 的話，恢復通聯後會退回快照裡的舊值，單位永遠掛著「失聯」標籤。
   */
  function liveStaleTick(u: UnitView): number | null {
    const p = stream.unitPatches[u.id]
    const raw = p && 'stale_since_tick' in p ? p.stale_since_tick : u.stale_since_tick
    return typeof raw === 'number' ? raw : null
  }
  // 系統當前 tick：以串流為準（CLOCK 心跳/STATE_DIFF 都帶）。WP-C5 之前這是**寫死的 100**，
  // 於是地圖上「失聯 +Nt」與敵情老化淡出都是拿假 tick 算的。
  const currentTick = computed(() => stream.lastTick ?? 0)

  return {
    livePos,
    liveHealth,
    liveStrength,
    liveComms,
    liveFuel,
    liveSuppression,
    livePosture,
    liveSupply,
    liveStarvedDays,
    liveStaleTick,
    currentTick,
  }
}
