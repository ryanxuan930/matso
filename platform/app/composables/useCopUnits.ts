/**
 * 把後端的單位/敵情投影成地圖渲染模型——「誰算我方、誰算敵情、各自長什麼樣」。
 *
 * 一切以**觀測者陣營**為軸：白軍/管理員切了視角＝以該陣營之眼觀戰；否則為自身陣營；
 * 未選視角的純白軍為空字串＝全局 god view。#91/#92 皆以此為「我方」的判準。
 *
 * **WHITE_CELL 是統裁保留字、不是交戰陣營**：以它當觀測者會導致「沒有任何單位算我方」
 * ——白軍被登記為 WHITE_CELL 參與者時，COP 的「單位」恆為 0、地圖只剩敵情（既有 bug）。
 * 故在 `observerFaction` 就視同無觀測者＝全局視角。
 *
 * 紅線 #3：這裡**不做 fog of war 過濾**。敵情一律取後端 `/intel` 已過濾的結果，
 * 不從 `/units` 反推——舊做法讓一般角色的敵情恆為空，白軍則等於把 ground truth 當敵情。
 */
import { computed, type ComputedRef, type Ref } from 'vue'
import { toContact } from '~/composables/useIntel'
import type { Contact, OwnUnit, Relation } from '~/composables/useUnits'
import type { ContactView } from '~/composables/useIntel'
import type { UnitView } from '~/composables/useOrders'
import type { useLiveState } from '~/composables/useLiveState'

// 固定示範一個 OFFLINE 虛影（fog of war demo，O4.4）
const GHOST: OwnUnit = {
  id: 'demo-ghost',
  faction: 'BLUE',
  lng: 121.2,
  lat: 24.2,
  unitType: 'HQ',
  comms: 'OFFLINE',
  lastReportedTick: 60,
}
const DEMO_CONTACTS: Contact[] = [
  { contactId: 'c-det', fidelity: 'DETECTED', lng: 121.4, lat: 23.5, errorRadiusM: 2000, lastSeenTick: 40 },
  { contactId: 'c-cls', fidelity: 'CLASSIFIED', lng: 121.5, lat: 23.6, errorRadiusM: 800, unitType: 'ARMOR', lastSeenTick: 80 },
  { contactId: 'c-id', fidelity: 'IDENTIFIED', lng: 121.6, lat: 23.7, errorRadiusM: 200, unitType: 'ARTILLERY', designation: '3-BN', lastSeenTick: 98, faction: 'RED', relation: 'HOSTILE' },
  { contactId: 'c-neutral', fidelity: 'IDENTIFIED', lng: 121.55, lat: 23.55, errorRadiusM: 200, unitType: 'RECON', designation: 'Y-1', lastSeenTick: 96, faction: 'YELLOW', relation: 'NEUTRAL' },
]

export function useCopUnits(opts: {
  live: ReturnType<typeof useLiveState>
  realUnits: Ref<UnitView[]>
  intelContacts: Ref<ContactView[]>
  /** 視角切換（#90）：'' = 全局 god view，否則＝以該陣營之眼觀戰。 */
  viewpoint: Ref<string>
  myFaction: Ref<string>
  /** 觀測者對各陣營的關係（後端以觀測者為中心給）。 */
  factionRelations: Ref<Record<string, string>>
  /** ?units=N 合成單位（FPS/demo）。 */
  syntheticUnits: ComputedRef<OwnUnit[]>
  /** ?demo=1 或 ?units=N 時才疊展示用假件。 */
  demoMode: ComputedRef<boolean>
}) {
  const {
    live,
    realUnits,
    intelContacts,
    viewpoint,
    myFaction,
    factionRelations,
    syntheticUnits,
    demoMode,
  } = opts

  const observerFaction = computed(() =>
    viewpoint.value || (myFaction.value === 'WHITE_CELL' ? '' : myFaction.value),
  )

  /**
   * 觀測者對某陣營的關係（#91）——2525 affiliation 的唯一依據。
   *
   * 己方恆 ALLIED；其餘查後端給的關係列。**未宣告 → HOSTILE**（SPEC §12.1 預設，
   * 與後端 `FactionRelations` 同一語義；不在前端另立一套判敵規則）。
   * faction 為 undefined（contact 未達 IDENTIFIED，敵我尚未揭露）→ 亦回 HOSTILE 保守標敵。
   */
  function relationOf(faction?: string | null): Relation {
    if (!faction) return 'HOSTILE'
    if (faction === observerFaction.value) return 'ALLIED'
    const r = factionRelations.value[faction]
    return r === 'ALLIED' || r === 'NEUTRAL' ? r : 'HOSTILE'
  }
  /** 我方＋友軍（#91 共享視圖）：這些陣營的單位以 Friendly 外型呈現、且可被指揮判定沿用。 */
  function isFriendly(faction?: string | null): boolean {
    return !observerFaction.value || relationOf(faction) === 'ALLIED'
  }

  // observerFaction 未知（純白軍全局視角）時，全部以友軍呈現以便至少可見。
  // #91：我方**與友軍（ALLIED）**皆列此（後端 units 已回共享視圖，此處符號一致以 Friendly 呈現）。
  const realAsOwn = computed<OwnUnit[]>(() =>
    realUnits.value
      .filter((u) => isFriendly(u.faction))
      .map((u) => ({
        id: u.id,
        faction: (u.faction as OwnUnit['faction']) ?? 'BLUE',
        designation: u.designation, // APP-6A Field T——地圖符號的番號
        unitLevel: u.unit_level, // APP-6A Field B——階層符號（SIDC 第 12 位）
        unitType: u.branch, // 兵科 → SIDC function ID（步兵斜線/裝甲橢圓/砲兵圓點…）
        ...live.livePos(u),
        // WP-C5：通聯狀態與「最後回報 tick」都取活值——寫死的 lastReportedTick 讓地圖上的
        // 「OFFLINE +Nt」一直是拿假數字算的（見 liveStaleTick / currentTick）。
        comms: live.liveComms(u) as OwnUnit['comms'],
        lastReportedTick: live.liveStaleTick(u) ?? live.currentTick.value,
        health: live.liveHealth(u), // 血量環（#5）；fog of war：僅我方單位帶血量
        isFixed: u.is_fixed, // 固定單位（指揮部等）→ 地圖鎖頭徽章
      })),
  )
  /**
   * 敵情 contacts（#90）：**取自後端偵測結果**（`/intel`），不再由 `/units` 反推。
   *
   * 舊做法是「拿 units 裡非我方的挑出來當敵情」，那有兩個問題：一般陣營角色的 `/units`
   * 只回己方 → 敵情恆為空（實際上就是看不到敵人）；白軍全知 → 等於把 ground truth 當敵情。
   * 現在一律以後端 fog 過濾後的 contacts 為準（未偵獲就是看不到），位置為最後已知。
   */
  const realAsContacts = computed<Contact[]>(() =>
    // 全局視角（無觀測者）不畫敵情：該視角本就以 ground truth 呈現全部單位，再疊各陣營的偵測結果
    // 會讓同一個單位出現兩次（一次友軍符號、一次 contact）。有觀測者時才是「他看得到什麼」。
    !observerFaction.value
      ? []
      : // #91：affiliation 依觀測者對該陣營的關係決定（未達 IDENTIFIED 時 faction 未揭露 → 保守標敵）。
        intelContacts.value.map((c) => toContact(c, relationOf)),
  )

  // 展示用假件（GHOST 虛影 + DEMO_CONTACTS 假敵情）僅在 ?demo=1 或 ?units=N 時顯示；
  // 正常 COP 只呈現真單位——避免與左側清單不符的多餘圖標（3-BN / Y-1 等，使用者回報）。
  const ownUnits = computed<OwnUnit[]>(() => [
    ...(demoMode.value ? [GHOST] : []),
    ...syntheticUnits.value,
    ...realAsOwn.value,
  ])
  const contacts = computed<Contact[]>(() => [
    ...(demoMode.value ? DEMO_CONTACTS : []),
    ...realAsContacts.value,
  ])

  return { observerFaction, relationOf, isFriendly, realAsOwn, ownUnits, contacts }
}
