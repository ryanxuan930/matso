/**
 * COP 下令狀態機（O4.5 + #28 移動預覽 + SPEC_EXTEND P4 聯合火力）。
 *
 * 涵蓋「選好單位之後」的整段互動：指令類型、目的地/自訂路徑、瞄準與目標鎖定、武器/彈種/
 * 火力政策、預檢結果，以及送出與取消。
 *
 * **選取本身不歸這裡**：`selectedId` 由頁面持有（地圖點選、右鍵選單、單位清單三處都會改它），
 * 本 composable 只吃它。頁面的 `clearSelection` 應呼叫 `resetOrderForm()`——把「清哪些欄位」
 * 留在這裡，才不會日後新增欄位時漏清一項（那種漏清會表現成「換單位後還帶著上一單位的彈種」）。
 *
 * 紅線：前端的檢查一律只是 UX 早退，**後端 validator 才是權威閘門**。
 */
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import type { components } from '~/types/api'
import type { ApiError } from '~/composables/useApi'
import {
  cancelOrder,
  fetchWeapons,
  orderStatusLabel,
  orderTypeLabel,
  submitOrder,
  type OrderResponse,
  type UnitView,
  type WeaponView,
} from '~/composables/useOrders'
import { fetchMovementPreview, type MovementPreview } from '~/composables/useMapFeatures'
import { fetchRequests, type RequestView } from '~/composables/useC2'

export type FirePolicy = components['schemas']['FirePolicy']

export const FIRE_POLICY_OPTS: { value: FirePolicy; label: string }[] = [
  { value: 'FREE', label: '自由開火（全武器）' },
  { value: 'SMALL_ARMS_ONLY', label: '僅輕兵器（節約重火力）' },
  { value: 'ANTI_ARMOR_HOLD', label: '反裝甲留給裝甲目標' },
]

/**
 * 行軍節奏（#80）。
 *
 * 後端一直是完整的（`MovePayload.tempo`、`movement.py` 的速度 ×1.5 與
 * `TEMPO_ATTRITION_FACTOR` ×2.5、預覽端的 `MovementPreviewRequest.tempo`），
 * 但**唯一的使用者是 AI**（`ai_loop/orders_bridge.py`）——同一局裡 AI 陣營的機動速度上限
 * 比人類高，而畫面上完全沒有提示說明為什麼。
 */
export type MarchTempo = 'NORMAL' | 'FORCED_MARCH'
export const TEMPO_OPTS: { value: MarchTempo; label: string }[] = [
  { value: 'NORMAL', label: '一般行軍 · 常速，磨耗最低' },
  { value: 'FORCED_MARCH', label: '強行軍 · 速度 1.5 倍，以行軍耗損換速度' },
]

const CROSS_KIND_LABELS: Record<string, string> = {
  OBSTACLE: '障礙',
  BUILDING: '建築',
  TERRAIN: '地形',
}
// #80：機動 profile 中文標籤（由編裝導出）。
const MOBILITY_LABELS: Record<string, string> = {
  FOOT: '徒步',
  WHEELED: '輪型',
  TRACKED: '履帶',
  BOAT: '舟艇',
  AIR: '空中',
}

export function crossKindLabel(kind: string): string {
  return CROSS_KIND_LABELS[kind] ?? kind
}
export function mobilityLabel(profile: string): string {
  return MOBILITY_LABELS[profile] ?? profile
}
/**
 * 射程的顯示字串（公尺／公里自動切換）。
 *
 * 最小射程常是幾百公尺，一律寫成 `0.2 km` 讀起來像「幾乎沒有限制」——
 * 而它正是迫砲打不到腳邊那個死角的邊界，數量級要看得出來。
 */
export function rangeLabel(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`
}

export function useCopOrdering(opts: {
  sessionId: Ref<string>
  selectedId: Ref<string | null>
  selectedUnit: ComputedRef<UnitView | null>
  selectedUnitFixed: ComputedRef<boolean>
  /** 送出/取消後重新拉狀態（頁面的 refresh，含快照與指令列表）。 */
  refresh: () => Promise<void>
  toasts: ReturnType<typeof useToasts>
}) {
  const { sessionId, selectedId, selectedUnit, selectedUnitFixed, refresh, toasts } = opts

  const orderType = ref<
    'MOVE' | 'ENGAGE' | 'FIRE_MISSION' | 'POSTURE' | 'MISSION' | 'FORMATION' | 'ENGINEER'
  >('MOVE')
  const destH3 = ref<string | null>(null)
  const destLatLng = ref<{ lng: number; lat: number } | null>(null) // 精確移動落點（#2）
  const targeting = ref(false)
  const targetUnitId = ref<string | null>(null)
  /**
   * WP-C9：對友軍/盟軍開火的**二次確認**。
   *
   * 後端 `allow_fratricide` 只是「不擋」，不是「隨手就能點」。誤傷是要寫進 AAR 的事，
   * 所以它需要一個**刻意的動作**——換了目標就自動退回未確認（下面的 watch），
   * 否則勾一次就能一路對不同友軍連續開火，那個勾選等於沒有意義。
   */
  const fratricideAck = ref(false)
  // 換目標 → 確認自動失效。勾一次就能連續對不同友軍開火的話，這個勾選等於沒有意義。
  watch(targetUnitId, () => {
    fratricideAck.value = false
    restrictedAck.value = false
  })
  const precheck = ref<OrderResponse['precheck'] | null>(null)
  const message = ref('')
  /**
   * WP-A3 限制射擊區的二次確認。
   *
   * 後端擋下來時明講「確認仍要射擊請重送並勾選確認」，放行條件是
   * `OrderRequest.acknowledge_restricted`——但這個欄位**前端從來沒送過**，
   * 於是在地圖上畫了「限制射擊區（需確認）」之後，該區就變成事實上的絕對禁射區，
   * 跟 NO_STRIKE 沒有差別，而畫面上還一直叫你去勾一個不存在的核取方塊。
   *
   * 與誤傷確認同紀律：**換目標/換落點就自動失效**——勾一次就能一路往管制區裡打，
   * 那個確認等於沒有意義。
   */
  const restrictedAck = ref(false)
  /** 上一次送出被「限制射擊區」擋下來 → 面板才顯示那個核取方塊。 */
  const restrictedBlocked = computed(
    () =>
      precheck.value?.checks?.some(
        (c) => c.name === 'no_strike' && !c.passed && String(c.detail ?? '').includes('限制射擊'),
      ) ?? false,
  )

  // #28 移動路徑預覽：目的地/自訂路徑 → 試算距離/tick/油耗/可行性/強穿阻礙。
  const movePreview = ref<MovementPreview | null>(null)
  const moveWaypoints = ref<number[][]>([]) // 自訂路徑（[lng,lat]，不含起點）
  const waypointMode = ref(false) // 逐點點擊建自訂路徑
  /** #80 行軍節奏：一般／強行軍（速度↔行軍耗損的取捨）。預覽與送出**必須帶同一個值**。 */
  const tempo = ref<MarchTempo>('NORMAL')
  let previewTimer: ReturnType<typeof setTimeout> | null = null

  // ENGAGE 武器/彈種（資料驅動 baseStats；選取單位時抓 GET /units/{id}/weapons）
  const weapons = ref<WeaponView[]>([])
  const weaponId = ref<string | null>(null)
  const ammoType = ref<string | null>(null)
  const selectedWeapon = computed(() => weapons.value.find((w) => w.id === weaponId.value) ?? null)
  const ammoOptions = computed(() => selectedWeapon.value?.ammo_types ?? [])
  // 換武器 → 清空彈種（避免殘留他武器的彈種）
  watch(weaponId, () => {
    ammoType.value = null
  })
  const firePolicy = ref<FirePolicy>('FREE')
  // 聯合火力模式＝未指定單一武器（≥2 武器才有意義）；指定武器＝單武器射擊。
  const combinedMode = computed(() => weaponId.value === null && weapons.value.length >= 2)

  // ---- WP-C10.2 面目標射擊（打座標，不打單位）----
  const firePoint = ref<{ lng: number; lat: number } | null>(null)
  const fireRounds = ref(4)
  /** 本局要求火協時要掛的已核准 FIRE_SUPPORT 申請單。 */
  const fireRequestId = ref<string | null>(null)
  const approvedFireRequests = ref<RequestView[]>([])
  /**
   * 撈本陣營「已核准且還沒用掉」的火力支援申請。
   *
   * **EXPENDED 一定要排除**：一張核准單只能兌現一次，列出用過的只會讓下令者選到必被
   * 預檢打回的那張。後端擋得住，但讓人選一個注定失敗的選項不是可用的介面。
   */
  async function loadFireRequests() {
    const list = await fetchRequests(sessionId.value).catch(() => null)
    approvedFireRequests.value = (list?.requests ?? []).filter(
      (r) => r.kind === 'FIRE_SUPPORT' && r.status === 'APPROVED',
    )
  }

  /**
   * 活彈藥（#53）：交戰消耗即時反映——優先讀 STATE_DIFF 的 `ammo_by_weapon`（活模擬扣減），
   * 否則回 `w.ammo_remaining`（GET /weapons 的 DB 值）。
   * `w.id` ＝ EquipmentInstance.id ＝ `ammo_by_weapon` 的鍵。
   */
  const stream = useSessionStreamStore()
  function liveAmmo(w: WeaponView): number | null {
    const abw = stream.unitPatches[selectedId.value ?? '']?.ammo_by_weapon as
      | Record<string, number>
      | undefined
    const live = abw?.[w.id]
    return typeof live === 'number' ? live : (w.ammo_remaining ?? null)
  }

  /** 選新單位/取消選取時要清掉的下令子狀態（#6）。 */
  function resetOrderForm() {
    precheck.value = null
    message.value = ''
    destH3.value = null
    destLatLng.value = null
    targeting.value = false
    targetUnitId.value = null
    fratricideAck.value = false
    weaponId.value = null
    ammoType.value = null
    firePolicy.value = 'FREE'
    weapons.value = []
    firePoint.value = null
    fireRequestId.value = null
    // 節奏跟著單位清掉：強行軍是一個要付戰力代價的例外決定，不該因為換了一個單位
    // 就被沿用下去（下一個單位的指揮官沒有做過那個決定）。
    tempo.value = 'NORMAL'
  }

  /** 抓此單位可用武器；失敗（他方/無裝備）→ 空清單，下拉隱藏。 */
  async function loadWeapons(unitId: string) {
    weapons.value = await fetchWeapons(sessionId.value, unitId).catch(() => [])
    // 火力任務發數改用該單位曲射武器的**準則發數**（`rounds_per_mission`）。
    // 這一欄在軍械庫編得動，但過去**沒有任何程式讀它**——一個編得動卻什麼都不影響的欄位
    // 比沒有這個欄位更糟。取所有武器裡宣告過的最大值（多門砲時以火力最強者為準）；
    // 都沒宣告（0）→ 保持既有預設，行為不變。
    const doctrinal = Math.max(0, ...weapons.value.map((w) => w.rounds_per_mission ?? 0))
    if (doctrinal > 0) fireRounds.value = doctrinal
  }

  // ---- #28 移動路徑預覽 ----
  // 目的地/自訂路徑改變 → 去抖後打 preview 端點。
  function schedulePreview() {
    if (previewTimer) clearTimeout(previewTimer)
    previewTimer = setTimeout(refreshMovePreview, 180)
  }
  async function refreshMovePreview() {
    if (orderType.value !== 'MOVE' || !selectedId.value) {
      movePreview.value = null
      return
    }
    const hasWps = moveWaypoints.value.length > 0
    if (!hasWps && !destH3.value) {
      movePreview.value = null
      return
    }
    try {
      movePreview.value = await fetchMovementPreview(sessionId.value, {
        unit_id: selectedId.value,
        // **預覽要帶與送出同一個節奏**：後端的速度、tick 數與行軍耗損都乘了 tempo 係數，
        // 不帶就等於用常速去估一趟強行軍——那正是這一輪一直在修的「預覽與實跑不一致」。
        tempo: tempo.value,
        ...(hasWps
          ? { waypoints: moveWaypoints.value }
          : {
              to_h3: destH3.value,
              ...(destLatLng.value
                ? { to_lat: destLatLng.value.lat, to_lng: destLatLng.value.lng }
                : {}),
            }),
      })
    } catch {
      movePreview.value = null
    }
  }
  // 移動路徑折線（[lng,lat]）；供 MapCanvas 畫線。
  const movePathCoords = computed<number[][]>(() => movePreview.value?.path ?? [])
  // 強穿標記點：沿路徑依 entry_frac 內插出座標（近似進入阻礙處）。
  const moveCrossPoints = computed<number[][]>(() => {
    const p = movePreview.value
    if (!p || p.path.length < 2 || !p.crossings.length) return []
    const pts = p.path
    const segLen: number[] = []
    let total = 0
    for (let i = 0; i < pts.length - 1; i++) {
      const d = Math.hypot(pts[i + 1]![0]! - pts[i]![0]!, pts[i + 1]![1]! - pts[i]![1]!)
      segLen.push(d)
      total += d
    }
    return p.crossings.map((c) => {
      let target = (c.entry_frac ?? 0) * total
      for (let i = 0; i < segLen.length; i++) {
        if (target <= segLen[i]! || i === segLen.length - 1) {
          const t = segLen[i]! > 0 ? target / segLen[i]! : 0
          return [
            pts[i]![0]! + (pts[i + 1]![0]! - pts[i]![0]!) * t,
            pts[i]![1]! + (pts[i + 1]![1]! - pts[i]![1]!) * t,
          ]
        }
        target -= segLen[i]!
      }
      return pts[0]!
    })
  })
  function clearMovePath() {
    moveWaypoints.value = []
    waypointMode.value = false
    movePreview.value = null
    destH3.value = null
    destLatLng.value = null
  }
  function undoWaypoint() {
    if (!moveWaypoints.value.length) return
    const next = moveWaypoints.value.slice(0, -1)
    moveWaypoints.value = next
    const last = next[next.length - 1]
    if (last) {
      destLatLng.value = { lng: last[0]!, lat: last[1]! }
    } else {
      destH3.value = null
      destLatLng.value = null
    }
    schedulePreview()
  }
  // 切換單位/指令類型 → 清路徑預覽（避免殘留他單位的路線）。
  watch([selectedId, orderType], () => {
    clearMovePath()
  })
  // 換節奏 → 重算預覽。不重算的話，面板上會留著常速那份距離/tick/耗損，
  // 使用者切到「強行軍」卻看不到任何數字變動，只能猜它有沒有生效。
  watch(tempo, schedulePreview)
  // 切到火力任務才抓核准單清單——沒人下面射擊時不必每選一個單位就多打一次 API。
  watch(orderType, (t) => {
    if (t === 'FIRE_MISSION') void loadFireRequests()
  })

  // ---- WP-A2 任務級下令 ----
  //
  // **下的是任務，不是動作**：分解器會把它持續展開成 MOVE/ENGAGE/POSTURE 並執行到完成。
  // 前端只負責收「任務型 + 幾何」，不試圖預覽分解結果——那是符號層每 tick 依當下敵情
  // 重新決定的事，前端畫出來的任何「預計路線」都會在第一次接敵時就失真。
  const missionType = ref<'SEIZE' | 'DEFEND' | 'SCREEN' | 'MOVE_MARCH'>('SEIZE')
  /** 主目標（SEIZE 的 objective / DEFEND 的 area）。 */
  const missionPoint = ref<{ lng: number; lat: number } | null>(null)
  /** 多點幾何（SEIZE 的 axis 途經點 / SCREEN 的 line / MOVE_MARCH 的 route）。 */
  const missionPath = ref<number[][]>([])
  /** 目標圈/防區半徑（公尺）。 */
  const missionRadiusM = ref(500)

  /** 各任務型要收哪一種幾何——UI 與 payload 共用同一份定義，避免兩邊各寫一次而漂移。 */
  const missionNeedsPoint = computed(() => missionType.value === 'SEIZE' || missionType.value === 'DEFEND')
  const missionNeedsPath = computed(
    () => missionType.value === 'SCREEN' || missionType.value === 'MOVE_MARCH' || missionType.value === 'SEIZE',
  )

  function clearMission() {
    missionPoint.value = null
    missionPath.value = []
  }
  // 換任務型 → 清幾何。SEIZE 的 objective 與 SCREEN 的 line 語義完全不同，
  // 留著上一個任務型的點只會送出一道意思相反的令。
  watch(missionType, clearMission)

  // ---- WP-C3 隊形/乘駐車令 ----
  //
  // 後端收成**一個 FORMATION 令**（payload 可帶 formation 與/或 mounted），
  // 至少要指定一項。`mounted` 是三態：`null`＝不動該欄（只想換隊形時不該把乘駐車一起重設）。
  const formation = ref<'COLUMN' | 'LINE' | 'WEDGE' | 'VEE' | 'HERRINGBONE' | ''>('')
  const mounted = ref<'' | 'true' | 'false'>('')

  // ---- WP-C2 障礙作業令 ----
  //
  // BREACH 破既有障礙（要 feature_id）；EMPLACE 設新障礙（要型別 + 座標）。
  // **須工兵單位**（ORBAT `attributes.unit_kind=ENGINEER`）且距作業點 500 m 內——
  // 預檢會擋，所以這裡不重複驗，但錯誤訊息會說明原因。
  const engineerAction = ref<'BREACH' | 'EMPLACE'>('EMPLACE')
  const obstacleType = ref<'MINEFIELD' | 'WIRE' | 'TANK_DITCH' | 'ABATIS' | 'BRIDGE_DEMO'>('WIRE')
  const engineerFeatureId = ref<string>('')
  /** EMPLACE 的落點（點地圖選）。 */
  const engineerPoint = ref<{ lng: number; lat: number } | null>(null)
  const engineerRadiusM = ref(200)

  // ---- WP-C1 姿態令 ----
  // 預設 DEFENSE 而不是 MOVING：下姿態令的人不會是為了叫單位站起來走（那是 MOVE 令的事）。
  const posture = ref<'MOVING' | 'HASTY' | 'DEFENSE' | 'DUG_IN'>('DEFENSE')

  /** 依當前令型組 payload。四種令型各自獨立，不共用欄位——共用過的欄位最容易忘了清。 */
  function buildPayload(): Record<string, unknown> {
    if (orderType.value === 'MOVE') {
      return {
        to_h3: destH3.value,
        // **用預覽算出來的那個 profile**，不是寫死 'FOOT'。
        // 後端吃 payload 的值（`precheck.path_reachable` 與 `movement/system.plan` 都用它），
        // 所以寫死等於：面板顯示「履帶 32 km/h、已繞開不可通行區」，送出去卻用徒步規劃——
        // 沼澤/陡坡對兩者可通行性相反，預覽畫的路線與實際走的可以完全不同。
        // 預覽還沒回來（剛點下去就送出）→ 退回 FOOT，與改版前相同。
        mobility_profile: movePreview.value?.mobility_profile ?? 'FOOT',
        // #80 行軍節奏。**無條件帶**（不是只在 FORCED_MARCH 時才帶）：這一欄會落進 Ledger，
        // AAR 要能分辨「指揮官選了常速」與「這道令是舊格式沒有節奏」——後者才該套預設。
        tempo: tempo.value,
        ...(destLatLng.value
          ? { to_lat: destLatLng.value.lat, to_lng: destLatLng.value.lng }
          : {}),
        // #28 自訂路徑：夾帶 waypoints 讓執行期沿折線前進 + 強穿耗損。
        ...(moveWaypoints.value.length ? { waypoints: moveWaypoints.value } : {}),
      }
    }
    if (orderType.value === 'POSTURE') return { posture: posture.value }
    if (orderType.value === 'FORMATION') {
      // **只送有宣告的欄位**——送 null 會被 pattern 驗證擋掉，而送空字串更糟（意思不明）。
      return {
        ...(formation.value ? { formation: formation.value } : {}),
        ...(mounted.value ? { mounted: mounted.value === 'true' } : {}),
      }
    }
    if (orderType.value === 'ENGINEER') {
      if (engineerAction.value === 'BREACH') {
        return { action: 'BREACH', feature_id: engineerFeatureId.value }
      }
      return {
        action: 'EMPLACE',
        obstacle_type: obstacleType.value,
        lat: engineerPoint.value?.lat,
        lng: engineerPoint.value?.lng,
        radius_m: engineerRadiusM.value,
      }
    }
    if (orderType.value === 'MISSION') {
      const point = missionPoint.value ? { lat: missionPoint.value.lat, lng: missionPoint.value.lng } : null
      const path = missionPath.value.map(([lng, lat]) => ({ lat: lat as number, lng: lng as number }))
      const params: Record<string, unknown> =
        missionType.value === 'SEIZE'
          ? { objective: point, axis: path, objective_radius_m: missionRadiusM.value }
          : missionType.value === 'DEFEND'
            ? { area: point, area_radius_m: missionRadiusM.value }
            : missionType.value === 'SCREEN'
              ? { line: path }
              : { route: path }
      return { mission_type: missionType.value, params }
    }
    if (orderType.value === 'FIRE_MISSION') {
      return {
        target_lat: firePoint.value?.lat,
        target_lng: firePoint.value?.lng,
        rounds: fireRounds.value,
        ...(fireRequestId.value ? { fire_request_id: fireRequestId.value } : {}),
      }
    }
    return {
      target_unit_id: targetUnitId.value,
      ...(weaponId.value ? { weapon_id: weaponId.value } : {}),
      ...(ammoType.value ? { ammo_type: ammoType.value } : {}),
      // 聯合火力（未指定單一武器）且政策非 FREE → 夾帶 fire_policy（SPEC_EXTEND P4）。
      ...(!weaponId.value && firePolicy.value !== 'FREE' ? { fire_policy: firePolicy.value } : {}),
    }
  }

  async function submit() {
    if (!selectedId.value) return
    // 固定單位（指揮部等）不可移動——前端先擋（後端 validator 為權威閘門，回 ORDER_UNIT_FIXED）。
    if (orderType.value === 'MOVE' && selectedUnitFixed.value) {
      toasts.push({
        severity: 'warn',
        title: '固定單位不可移動',
        detail: `${selectedUnit.value?.designation ?? ''} 為固定單位（指揮部等），不接受移動令。`,
        timeoutMs: 4000,
      })
      return
    }
    message.value = ''
    const payload = buildPayload()
    try {
      const resp = await submitOrder(sessionId.value, {
        unit_id: selectedId.value,
        order_type: orderType.value,
        payload,
        // 只在使用者真的勾了才送——這個欄位是「我知道那是管制區，仍要射擊」的留痕，
        // 無條件帶 false 會讓 body 多一個沒有意義的欄位。
        ...(restrictedAck.value ? { acknowledge_restricted: true } : {}),
      })
      precheck.value = resp.precheck ?? null
      restrictedAck.value = false  // 收下了就退回未確認（同誤傷確認的紀律）
      message.value = `已下令（${orderStatusLabel(resp.status)}）`
      toasts.push({
        severity: 'success',
        title: `已下令：${orderTypeLabel(orderType.value)} · ${selectedUnit.value?.designation ?? ''}`,
        timeoutMs: 4000,
      })
      if (orderType.value === 'MOVE') clearMovePath() // #28 送出後清路徑預覽
      if (orderType.value === 'MISSION') clearMission()
      if (orderType.value === 'FIRE_MISSION') {
        // 核准單在令被收下時就兌現掉了（B5.3）——不重抓的話，下拉裡還留著那張已用掉的單。
        firePoint.value = null
        fireRequestId.value = null
        await loadFireRequests()
      }
      await refresh()
    } catch (e) {
      const err = e as ApiError & { message?: string }
      const pc = (err as unknown as { details?: { precheck?: OrderResponse['precheck'] } }).details
      precheck.value = pc?.precheck ?? null
      message.value = `不可行：${err.code ?? ''}`
      // #7：下令被系統拒絕 → 彈出通知，逐項列出失敗預檢的詳細原因（地形遮蔽/超出射程/無彈…）。
      const failed = (precheck.value?.checks ?? []).filter((c) => !c.passed)
      const lines = failed.map((c) => `✗ ${c.name}${c.detail ? ` — ${c.detail}` : ''}`)
      toasts.push({
        severity: 'error',
        title: `下令被拒：${orderTypeLabel(orderType.value)}${err.code ? `（${err.code}）` : ''}`,
        detail: lines.length ? undefined : (err.message ?? '系統拒絕此指令'),
        lines,
        timeoutMs: 10000, // #7：10 秒後自動關閉
      })
    }
  }

  async function cancel(id: string) {
    await cancelOrder(sessionId.value, id).catch(() => undefined)
    await refresh()
  }

  return {
    orderType,
    destH3,
    destLatLng,
    targeting,
    targetUnitId,
    fratricideAck,
    restrictedAck,
    restrictedBlocked,
    precheck,
    message,
    movePreview,
    moveWaypoints,
    waypointMode,
    tempo,
    movePathCoords,
    moveCrossPoints,
    weapons,
    weaponId,
    ammoType,
    selectedWeapon,
    ammoOptions,
    firePolicy,
    combinedMode,
    firePoint,
    fireRounds,
    posture,
    // WP-C3 隊形/乘駐車
    formation,
    mounted,
    // WP-C2 障礙作業
    engineerAction,
    obstacleType,
    engineerFeatureId,
    engineerPoint,
    engineerRadiusM,
    missionType,
    missionPoint,
    missionPath,
    missionRadiusM,
    missionNeedsPoint,
    missionNeedsPath,
    clearMission,
    fireRequestId,
    approvedFireRequests,
    loadFireRequests,
    liveAmmo,
    resetOrderForm,
    loadWeapons,
    schedulePreview,
    clearMovePath,
    undoWaypoint,
    submit,
    cancel,
  }
}
