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
  const precheck = ref<OrderResponse['precheck'] | null>(null)
  const message = ref('')

  // #28 移動路徑預覽：目的地/自訂路徑 → 試算距離/tick/油耗/可行性/強穿阻礙。
  const movePreview = ref<MovementPreview | null>(null)
  const moveWaypoints = ref<number[][]>([]) // 自訂路徑（[lng,lat]，不含起點）
  const waypointMode = ref(false) // 逐點點擊建自訂路徑
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
    weaponId.value = null
    ammoType.value = null
    firePolicy.value = 'FREE'
    weapons.value = []
    firePoint.value = null
    fireRequestId.value = null
  }

  /** 抓此單位可用武器；失敗（他方/無裝備）→ 空清單，下拉隱藏。 */
  async function loadWeapons(unitId: string) {
    weapons.value = await fetchWeapons(sessionId.value, unitId).catch(() => [])
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
        mobility_profile: 'FOOT',
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
    precheck.value = null
    const payload = buildPayload()
    try {
      const resp = await submitOrder(sessionId.value, {
        unit_id: selectedId.value,
        order_type: orderType.value,
        payload,
      })
      precheck.value = resp.precheck ?? null
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
    precheck,
    message,
    movePreview,
    moveWaypoints,
    waypointMode,
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
