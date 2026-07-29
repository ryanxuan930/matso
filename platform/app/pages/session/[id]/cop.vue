<script setup lang="ts">
import type { Contact, OwnUnit, Relation } from '~/composables/useUnits'
import { commsLabel, factionColor, healthColor, unitLevelLabel } from '~/composables/useUnits'
import type { UnitView, WeaponView, OrderResponse } from '~/composables/useOrders'
import type { StateSnapshot } from '~/stores/sessionStream'
import type { ContactView } from '~/composables/useIntel'
import { toContact } from '~/composables/useIntel'
import { apiFetch } from '~/composables/useApi'
import {
  fetchOrders,
  fetchUnits,
  orderStatusLabel,
  orderTypeLabel,
} from '~/composables/useOrders'
import { fetchOrbatPermissions, setOrbatPermissions } from '~/composables/useEquipment'
import { forward as mgrsForward } from 'mgrs'
import { latLngToCell } from 'h3-js'
import { formatCountdown, useAiStatus } from '~/composables/useAiStatus'

// COP（SPEC §13.1/§13.4）：地圖基座（O4.2）+ 單位/fog of war（O4.4）+ 下令 UX（O4.5）。
const route = useRoute()
const sessionId = computed(() => String(route.params.id))

// #79 AI 決策狀態列（思考中／下一次決策倒數）——後端 faction-scoped，一般角色只回己方。
const aiStatus = useAiStatus(() => sessionId.value)
const aiChips = computed(() =>
  aiStatus.factions.value.map((f) => ({
    faction: f.faction,
    state: f.state,
    countdown: formatCountdown(f.seconds_until_next),
  })),
)

// 白軍控制台（時間控制 / 注入 / 視角）限統裁角色（SPEC §12）；其餘角色不顯示入口。
const auth = useAuthStore()
const canControl = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF'].includes(auth.user?.role ?? ''),
)

// #12 浮動工具視窗（六個小工具皆可拖拉/縮放/關閉）＋操作員偏好持久化。
// coordQuery/mapEditorOpen 是對應 widget 的開關別名。
const {
  widgets,
  widgetMenuOpen,
  hasLeftDock,
  hasRightDock,
  focusWidget,
  toggleWidget,
  setWidgetGeom,
  onWidgetGrab,
  onWidgetDrop,
  openFlag,
} = useCopWidgets()
const coordQuery = openFlag('coords')
const mapEditorOpen = openFlag('mapedit')
const {
  hex,
  hillshade,
  contour,
  layerOpacity,
  layerOrder,
  contourMajor,
  contourMinor,
  latlngGrid,
  mgrsGrid,
  gridStepDeg,
  hexMaxRes,
  hexLimitKm,
  dayNight,
  timeOfDay,
  hexLineWidth,
  contourMajorWidth,
  contourMinorWidth,
  hexLineColor,
  contourColor,
  gridColor,
  gridWidth,
  mgrsColor,
  preciseMove,
  basemapSources,
  basemap,
  hasTiles,
  onBasemapError,
} = useCopPrefs(widgets)

// 座標查詢（#10）：點地圖 → 該點經緯度 + MGRS。
const queryPoint = ref<{ lng: number; lat: number } | null>(null)
const queryMgrs = ref('')

const hiddenFeatureIds = ref<string[]>([]) // session-local：隱藏的地圖元素
function toggleFeatureHidden(id: string) {
  const i = hiddenFeatureIds.value.indexOf(id)
  if (i >= 0) hiddenFeatureIds.value.splice(i, 1)
  else hiddenFeatureIds.value.push(id)
}

const TYPES = ['INFANTRY', 'ARMOR', 'ARTILLERY', 'RECON', 'HQ']

// ?units=N 合成單位（FPS/demo，O4.4）
const syntheticUnits = computed<OwnUnit[]>(() => {
  const n = Math.min(Math.max(Number(route.query.units) || 0, 0), 2000)
  const cols = Math.ceil(Math.sqrt(n)) || 1
  const out: OwnUnit[] = []
  for (let i = 0; i < n; i++) {
    out.push({
      id: `syn-${i}`,
      faction: 'BLUE',
      lng: 120.0 + ((i % cols) / cols) * 2.0,
      lat: 22.8 + (Math.floor(i / cols) / cols) * 1.8,
      unitType: TYPES[i % TYPES.length],
      comms: i % 17 === 0 ? 'OFFLINE' : 'ONLINE',
      lastReportedTick: 100 - (i % 40),
    })
  }
  return out
})

// 真單位（GET /units；下令對象）
const realUnits = ref<UnitView[]>([])
const intelContacts = ref<ContactView[]>([]) // 後端 fog 過濾後的敵情（#90）
const commsPosture = ref<string | null>(null) // 觀測陣營整體通聯姿態（WP-C5；god view 為 null）
// 視角切換（#90）：僅全知角色可用。'' = 全局 god view；否則＝以該陣營之眼觀戰（套其戰場迷霧）。
// **不在前端過濾**——值只是帶給後端的 as_faction，實際可見性由後端決定（紅線 #3）。
const viewpoint = ref<string>('')
const sessionFactions = ref<string[]>([]) // 視角下拉選項（全局視角時取得，切視角後不縮）
// #91 觀測者對各陣營的關係（後端以觀測者為中心給，不含第三方之間的結盟）。
const factionRelations = ref<Record<string, string>>({})
const myFaction = ref<string>('') // 觀測者陣營（GET /sessions.my_faction）
const sessionStart = ref<string | null>(null) // 開局時間（#4 執行時間顯示）
const orbatEdit = ref(false) // 本 session 是否可編輯編裝（白軍，或本軍且該局開放自編）
const myUnitScope = ref<string[]>([]) // 限指揮之單位子集（空＝整個陣營）；範圍外單位不可下令
const showOrbat = ref(false) // 詳細卡的編裝編輯器展開狀態

const orders = ref<OrderResponse[]>([])
// 指令列表：以下令 tick（≈真實時間）新到舊排序，剛下的令排最上（穩定排序，同 tick 保原序）。
const sortedOrders = computed(() =>
  [...orders.value].sort((a, b) => (b.issued_at_tick ?? 0) - (a.issued_at_tick ?? 0)),
)
const selectedId = ref<string | null>(null)
const selectedUnit = computed(() => realUnits.value.find((u) => u.id === selectedId.value) ?? null)
// 固定單位（指揮部等）：不可下移動令（後端 validator 權威擋 ORDER_UNIT_FIXED；此為 UX 提示）。
const selectedUnitFixed = computed(() => !!selectedUnit.value?.is_fixed)

// 全域通知（下令被拒等，#7）。
const toasts = useToasts()

// 下令狀態機（指令類型/目的地/瞄準/武器彈種/火力政策/預檢 + 移動預覽 + 送出取消）。
// `refresh` 為函式宣告（已提升），可在其文字定義之前傳入。
const {
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
  ammoOptions,
  firePolicy,
  combinedMode,
  resetOrderForm,
  loadWeapons,
  schedulePreview,
  clearMovePath,
  undoWaypoint,
  submit,
  cancel,
} = useCopOrdering({
  sessionId,
  selectedId,
  selectedUnit,
  selectedUnitFixed,
  refresh: () => refresh(),
  toasts,
})

// 地圖編輯器（stage ③b）——標註/工事/武器據點的繪製、編輯、整形、地形裁切。
// `canControl`/`myFaction`/`viewpoint` 決定可繪與歸屬；選到圖形時把「地圖編輯」小工具叫出來（#26）。
const mapEditor = useMapEditor({
  sessionId,
  viewpoint,
  canControl,
  myFaction,
  hiddenFeatureIds,
  toasts,
  onFeaturePicked: () => {
    if (!widgets.value.mapedit.open) {
      widgets.value.mapedit.open = true
      focusWidget('mapedit')
    }
  },
})
// 面板收 unwrap 過的視圖（樣板不會 unwrap 巢狀 ref；見該元件的說明）。寫入會回寫原 ref。
const mapEditorView = reactive(mapEditor)
// 以下是**頁面自己**要用的部分：
// 地圖圖層資料、地圖點擊/右鍵/拖曳的回呼，以及快照套用。
const {
  selectedFeatureId,
  canDraw,
  drawActive,
  featureFc,
  featSymbol,
  influenceFc,
  draftFc,
  canEditSelectedFeature,
  applyFeatures,
  addDraftPoint,
  onFeatureClick,
  armReshape,
  onFeatureReshape,
  deleteVertexAt,
  onFeatureVertexDelete,
  onFeatureMove,
  rotateFeature,
  removeFeature,
} = mapEditor
// WS 串流（含活模擬 STATE_DIFF 位置）——先宣告以供 livePos 使用。
const stream = useSessionStreamStore()
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
// #84 活油料：STATE_DIFF 串流的 fuel（移動耗油/補給加油即時反映）。無值＝徒步/無油料模型。
function liveFuel(unitId: string | null): number | null {
  const f = stream.unitPatches[unitId ?? '']?.fuel
  return typeof f === 'number' ? f : null
}
// 活彈藥（#53）：交戰消耗即時反映——優先讀 STATE_DIFF 的 ammo_by_weapon（活模擬扣減），
// 否則回 w.ammo_remaining（GET /weapons 的 DB 值）。w.id＝EquipmentInstance.id＝ammo_by_weapon 鍵。
function liveAmmo(w: WeaponView): number | null {
  const abw = stream.unitPatches[selectedId.value ?? '']?.ammo_by_weapon as
    | Record<string, number>
    | undefined
  const live = abw?.[w.id]
  return typeof live === 'number' ? live : (w.ammo_remaining ?? null)
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

/**
 * 單位量體＝加權平均的權重。用滿編戰力（TO&E 分母，與規模同單位）；
 * 缺值退平台/建制數，再缺退 1（此時等同未加權平均）。
 */
function unitMass(u: UnitView): number {
  const m = u.authorized_strength ?? u.platform_count ?? 1
  return m > 0 ? m : 1
}

/**
 * 陣營戰力＝各單位作戰效能%以量體加權平均（Σ 量體×效能 ÷ Σ 量體）。
 * 一個連跌到 50% 不該和一個營跌到 50% 等重，故不用單純平均。
 * **被摧毀單位仍計入分母**（量體照算、效能 0）——否則全滅的陣營會顯示 100%。
 */
function factionPower(units: UnitView[]): { pct: number; mass: number; ko: number } {
  let weighted = 0
  let mass = 0
  let ko = 0
  for (const u of units) {
    const w = unitMass(u)
    const h = Math.min(100, Math.max(0, liveHealth(u) ?? 100))
    if (h <= 0) ko += 1
    weighted += w * h
    mass += w
  }
  return { pct: mass > 0 ? weighted / mass : 0, mass, ko }
}

// 真單位依「我方 / 他軍」分流渲染：我方＝友軍符號（可選取指揮）；他軍＝敵情符號（可鎖為攻擊目標）。
// 觀測者陣營（#90）：白軍/管理員切了視角＝以該陣營之眼觀戰；否則為自身陣營。
// 未選視角的純白軍為空字串＝全局 god view。#91/#92 皆以此為「我方」的判準。
// WHITE_CELL 是統裁保留字、不是交戰陣營：以它當觀測者會導致「沒有任何單位算我方」
// （既有 bug——白軍被登記為 WHITE_CELL 參與者時，COP 的「單位」恆為 0、地圖只剩敵情）。
// 故視同無觀測者＝全局視角。
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
      ...livePos(u),
      // WP-C5：通聯狀態與「最後回報 tick」都取活值——寫死的 lastReportedTick 讓地圖上的
      // 「OFFLINE +Nt」一直是拿假數字算的（見 liveStaleTick / currentTick）。
      comms: liveComms(u) as OwnUnit['comms'],
      lastReportedTick: liveStaleTick(u) ?? currentTick.value,
      health: liveHealth(u), // 血量環（#5）；fog of war：僅我方單位帶血量
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
// 展示用假件（GHOST 虛影 + DEMO_CONTACTS 假敵情）僅在 ?demo=1 或 ?units=N 時顯示；
// 正常 COP 只呈現真單位——避免與左側清單不符的多餘圖標（3-BN / Y-1 等，使用者回報）。
const demoMode = computed(() => route.query.demo === '1' || Number(route.query.units) > 0)
const ownUnits = computed<OwnUnit[]>(() => [
  ...(demoMode.value ? [GHOST] : []),
  ...syntheticUnits.value,
  ...realAsOwn.value,
])
const DEMO_CONTACTS: Contact[] = [
  { contactId: 'c-det', fidelity: 'DETECTED', lng: 121.4, lat: 23.5, errorRadiusM: 2000, lastSeenTick: 40 },
  { contactId: 'c-cls', fidelity: 'CLASSIFIED', lng: 121.5, lat: 23.6, errorRadiusM: 800, unitType: 'ARMOR', lastSeenTick: 80 },
  { contactId: 'c-id', fidelity: 'IDENTIFIED', lng: 121.6, lat: 23.7, errorRadiusM: 200, unitType: 'ARTILLERY', designation: '3-BN', lastSeenTick: 98, faction: 'RED', relation: 'HOSTILE' },
  { contactId: 'c-neutral', fidelity: 'IDENTIFIED', lng: 121.55, lat: 23.55, errorRadiusM: 200, unitType: 'RECON', designation: 'Y-1', lastSeenTick: 96, faction: 'YELLOW', relation: 'NEUTRAL' },
]
const contacts = computed<Contact[]>(() => [
  ...(demoMode.value ? DEMO_CONTACTS : []),
  ...realAsContacts.value,
])

// #95 武器軌跡（純顯示，見 composable 的紅線說明）。端點只取「本 client 看得到的東西」。
const { weaponTrackFc } = useWeaponTracks(ownUnits, contacts, computed(() => stream.events))
// 單位資訊卡的錨定與拖曳（#Fix C / #42）。
const { unitCardPos, unitCardDrag, unitCardStyle, onSelectScreenPos, beginCardDrag } =
  useUnitCardDrag(selectedId)

// 可作 ENGAGE 目標的真單位（他軍）——供下拉與地圖點選鎖定共用。
const realUnitIds = computed(() => new Set(realUnits.value.map((u) => u.id)))
const engageTargets = computed(() =>
  realUnits.value.filter((u) => u.id !== selectedId.value && !isFriendly(u.faction)),
)

// WP-E3：狀態（單位/敵情/關係/標註）改由**單一原子快照**取得。
// 過去是四個獨立 GET 各自回來拼裝——彼此不同時，會拼出「單位是新的、敵情是舊的」的畫面。
function applySnapshot(snap: StateSnapshot) {
  realUnits.value = snap.units
  intelContacts.value = snap.contacts
  commsPosture.value = snap.comms_posture ?? null // WP-C5：敵情粗化的成因（粗化本身已在後端生效）
  // #91 關係矩陣：決定友/中/敵符號。缺 → 空物件 → relationOf 退回 HOSTILE（保守標敵）。
  factionRelations.value = snap.relations?.relations ?? {}
  // 視角下拉的選項來源：只在全局視角時更新（切了視角後快照只回該陣營，會把清單縮成一項）。
  if (!viewpoint.value) sessionFactions.value = [...(snap.relations?.factions ?? [])].sort()
  applyFeatures(snap.map_features)
}

// 收到 RESYNC 後 store 會抓一份快照 → 此處一次性套用（同一 tick 內賦值，畫面只渲染一次）。
watch(
  () => stream.snapshot,
  (snap) => {
    if (snap) applySnapshot(snap as StateSnapshot)
  },
)

async function refresh() {
  if (!(await stream.pullSnapshot())) return // 快照失敗就整批不動，不要留下半套狀態
  orders.value = await fetchOrders(sessionId.value).catch(() => [])
  // 我方陣營（決定友/敵渲染與目標可選集）+ 開局時間（#4 執行時間）——由 session 摘要取得。
  const sessions = await apiFetch<
    {
      id: string
      my_faction?: string
      start_time?: string | null
      orbat_edit?: boolean
      my_unit_scope?: string[]
    }[]
  >('/sessions').catch(() => [])
  const me = sessions.find((s) => s.id === sessionId.value)
  myFaction.value = me?.my_faction ?? ''
  sessionStart.value = me?.start_time ?? null
  orbatEdit.value = !!me?.orbat_edit
  myUnitScope.value = me?.my_unit_scope ?? []
}

// 清空選取與下令子狀態（#6 點空白取消選取 / 選新單位前重置）。
// 切換視角（#90）→ 重抓 units/intel（後端依 as_faction 套該陣營迷霧）；清掉屬於前一視角的選取。
watch(viewpoint, async () => {
  selectedId.value = null
  targetUnitId.value = ''
  await refresh()
})
function clearSelection() {
  selectedId.value = null
  unitCardPos.value = null
  showOrbat.value = false
  resetOrderForm() // 下令子狀態清哪些欄位歸 useCopOrdering（新增欄位時不會漏清）
}

async function selectUnit(id: string) {
  clearSelection()
  selectedId.value = id
  await loadWeapons(id)
}

function onMapClick(e: { lng: number; lat: number; h3: string }) {
  // 繪圖中：點擊＝加頂點（各形狀的完成條件見 useMapEditor.addDraftPoint）。
  if (drawActive.value) {
    addDraftPoint(e.lng, e.lat)
    return
  }
  // 座標查詢模式（#10）：點地圖 → 顯示該點經緯度 + MGRS。
  if (coordQuery.value) {
    queryPoint.value = { lng: e.lng, lat: e.lat }
    try {
      queryMgrs.value = mgrsForward([e.lng, e.lat], 5)
    } catch {
      queryMgrs.value = '—'
    }
    return
  }
  // #28 自訂路徑模式：逐點點擊加入 waypoint（不結束瞄準，可續點）。
  if (orderType.value === 'MOVE' && waypointMode.value) {
    moveWaypoints.value = [...moveWaypoints.value, [e.lng, e.lat]]
    destH3.value = e.h3 // 最後一點作為目的地（供送出/顯示）
    destLatLng.value = { lng: e.lng, lat: e.lat }
    schedulePreview()
    return
  }
  if (orderType.value === 'MOVE' && targeting.value) {
    destH3.value = e.h3
    // 精確移動：記錄精確點；否則落點＝六角格心（destLatLng=null）。
    destLatLng.value = preciseMove.value ? { lng: e.lng, lat: e.lat } : null
    moveWaypoints.value = [] // 單點目的地→清自訂路徑
    targeting.value = false
    schedulePreview()
    return
  }
  // ENGAGE 瞄準中點到空白（未命中敵方單位）→ 取消瞄準但保留選取（避免誤點就丟失單位，#3）。
  if (targeting.value) {
    targeting.value = false
    return
  }
  // 點空白處（非設定目標中）→ 取消選取，避免單位/標註一直被選著（#6）。
  if (selectedId.value) clearSelection()
  if (selectedFeatureId.value) selectedFeatureId.value = null
}

// 點地圖上的單位符號：我方 → 選取指揮；他軍（有選取的我方單位時）→ 鎖為 ENGAGE 目標。
function onUnitClick(e: { id: string; faction: string; kind: string }) {
  const isReal = realUnitIds.value.has(e.id)
  const isMine = isReal && isFriendly(e.faction)
  if (isMine) {
    selectUnit(e.id)
    return
  }
  if (isReal && selectedId.value && !isFriendly(e.faction)) {
    orderType.value = 'ENGAGE'
    targetUnitId.value = e.id
    targeting.value = false
    precheck.value = null
    message.value = `已鎖定目標：${realUnits.value.find((u) => u.id === e.id)?.designation ?? e.id}`
  }
}

// ---- 右鍵選單（#3，ATAK 式移動/攻擊）----
// 流程：右鍵我方單位 → 選單「移動/攻擊」→ 十字準星 → 點地圖選落點/目標 → 於下令面板確認。
const ctxMenu = ref<{
  x: number
  y: number
  lng: number
  lat: number
  unitId?: string
  faction?: string
  kind?: string
  featureId?: string
  vertexIndex?: number // #99 游標下的控制點索引（右鍵可刪點）
} | null>(null)
// #26 右鍵地圖物件選單動作：編輯（開編輯工具列並選取）/ 旋轉 / 刪除。
// #99b：這裡同時「解鎖整形」——控制點與拖曳只在明確從此進入後才生效。
function ctxEditFeature() {
  const id = ctxMenu.value?.featureId
  closeCtx()
  if (!id) return
  onFeatureClick({ id })
  armReshape(id) // 必須在 onFeatureClick 之後：selectedFeatureId 的 watch 會清掉不相符的解鎖
}
async function ctxRotateFeature(deg: number) {
  const id = ctxMenu.value?.featureId
  closeCtx()
  if (!id) return
  if (selectedFeatureId.value !== id) onFeatureClick({ id })
  await nextTick()
  await rotateFeature(deg)
}
async function ctxDeleteFeature() {
  const id = ctxMenu.value?.featureId
  closeCtx()
  if (id) await removeFeature(id)
}
const ctxIsMine = computed(
  () =>
    !!ctxMenu.value?.unitId &&
    realUnitIds.value.has(ctxMenu.value.unitId) &&
    isFriendly(ctxMenu.value.faction),
)
const ctxIsEnemy = computed(
  () =>
    !!ctxMenu.value?.unitId &&
    realUnitIds.value.has(ctxMenu.value.unitId) &&
    !isFriendly(ctxMenu.value.faction),
)
const ctxUnitName = computed(() => {
  const id = ctxMenu.value?.unitId
  return (id && realUnits.value.find((u) => u.id === id)?.designation) || id || ''
})
function onContextMenu(e: {
  x: number
  y: number
  lng: number
  lat: number
  unitId?: string
  faction?: string
  kind?: string
  featureId?: string
  vertexIndex?: number
}) {
  // 繪圖/座標查詢時不彈選單（避免干擾）。
  if (drawActive.value || coordQuery.value) return
  ctxMenu.value = e
}
function closeCtx() {
  ctxMenu.value = null
}
// 選單動作：武裝「移動」——選該單位（若右鍵在單位上），進入 MOVE 目標設定（十字準星）。
function ctxArmMove() {
  if (ctxMenu.value?.unitId && ctxIsMine.value) selectUnit(ctxMenu.value.unitId)
  if (!selectedId.value) return closeCtx()
  orderType.value = 'MOVE'
  targeting.value = true
  closeCtx()
}
// 選單動作：武裝「攻擊」——選該單位，進入 ENGAGE，點敵方單位鎖定目標。
function ctxArmAttack() {
  if (ctxMenu.value?.unitId && ctxIsMine.value) selectUnit(ctxMenu.value.unitId)
  if (!selectedId.value) return closeCtx()
  orderType.value = 'ENGAGE'
  targeting.value = true
  closeCtx()
}
// 選單動作：直接「移動至此」——用右鍵點擊處為落點（免再點一次）。
function ctxMoveHere() {
  const c = ctxMenu.value
  if (!c || !selectedId.value) return closeCtx()
  orderType.value = 'MOVE'
  destH3.value = latLngToCell(c.lat, c.lng, 8)
  destLatLng.value = preciseMove.value ? { lng: c.lng, lat: c.lat } : null
  targeting.value = false
  closeCtx()
}
// 選單動作：右鍵敵方單位（已選我方）→ 直接鎖為攻擊目標。
function ctxLockTarget() {
  const c = ctxMenu.value
  if (!c?.unitId || !selectedId.value) return closeCtx()
  orderType.value = 'ENGAGE'
  targetUnitId.value = c.unitId
  targeting.value = false
  precheck.value = null
  message.value = `已鎖定目標：${ctxUnitName.value}`
  closeCtx()
}

const targetUnit = computed(() => realUnits.value.find((u) => u.id === targetUnitId.value) ?? null)
// 選取單位是否可編裝：需該局開放編裝，且（我為白軍/全知 或 該單位為本軍）。
const selectedEditable = computed(
  () =>
    orbatEdit.value &&
    !!selectedUnit.value &&
    (canControl.value || (!!myFaction.value && selectedUnit.value.faction === myFaction.value)),
)

// unit_scope：白軍/全知不限；scope 空＝整個陣營；否則只能下令範圍內單位（後端 validator 亦強制）。
function inScope(u: UnitView): boolean {
  return canControl.value || myUnitScope.value.length === 0 || myUnitScope.value.includes(u.id)
}
// 單位/下令小工具依陣營分組（可收合/展開）。
const collapsedFactions = ref<Set<string>>(new Set())
function toggleFactionGroup(f: string) {
  const s = new Set(collapsedFactions.value)
  if (s.has(f)) s.delete(f)
  else s.add(f)
  collapsedFactions.value = s
}
const unitsByFaction = computed(() => {
  const groups = new Map<string, UnitView[]>()
  for (const u of realUnits.value) {
    const arr = groups.get(u.faction) ?? []
    arr.push(u)
    groups.set(u.faction, arr)
  }
  return [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([faction, units]) => ({ faction, units, power: factionPower(units) }))
})

// ---- 裝備管理（COP 專屬面板）：白軍編任一單位編裝 + 設各軍自編權限；或本軍（該局開放自編）----
const equipMgr = ref(false)
const equipUnitId = ref('')
const orbatPerms = ref<string[]>([])
const canManageEquip = computed(() => canControl.value || orbatEdit.value)
// 可編裝單位：白軍見全部（依陣營分組）；一般角色僅本軍（且該局開放自編）。
const equipEditableFactions = computed(() =>
  canControl.value
    ? unitsByFaction.value
    : unitsByFaction.value.filter((g) => !!myFaction.value && g.faction === myFaction.value),
)
async function openEquipMgr() {
  equipMgr.value = true
  equipUnitId.value = ''
  if (canControl.value) {
    orbatPerms.value = (await fetchOrbatPermissions(sessionId.value).catch(() => ({ factions: [] })))
      .factions
  }
}
async function toggleOrbatPerm(f: string) {
  const set = new Set(orbatPerms.value)
  if (set.has(f)) set.delete(f)
  else set.add(f)
  const next = [...set]
  try {
    orbatPerms.value = (await setOrbatPermissions(sessionId.value, next)).factions
    toasts.push({
      severity: 'success',
      title: `自編權限：${orbatPerms.value.join('、') || '（僅白軍）'}`,
      timeoutMs: 2500,
    })
  } catch {
    toasts.push({ severity: 'error', title: '設定自編權限失敗', timeoutMs: 3000 })
  }
}

// ---- 地圖狀態編輯（暫停下布局：拖放單位 + 繪障礙，完成再開始兵推）。限白軍/導演。----
const mapEditMode = ref(false)
const selectedUnitCount = ref(0) // 地圖狀態編輯多選數量（「已選 N 個」徽章）
async function enterMapEdit() {
  try {
    await apiFetch(`/sessions/${sessionId.value}/control`, {
      method: 'POST',
      body: { action: 'PAUSE' },
    })
    mapEditMode.value = true
    widgets.value.mapedit.open = true // 順帶開啟繪圖工具（障礙/建築）
    focusWidget('mapedit')
    toasts.push({
      severity: 'info',
      title: '地圖狀態編輯：已暫停推演',
      detail: '拖曳單位調整位置、用地圖編輯繪製障礙/建築，完成後按「開始兵推」。',
      timeoutMs: 6000,
    })
  } catch {
    toasts.push({ severity: 'error', title: '進入編輯模式失敗（需白軍/導演權限）', timeoutMs: 4000 })
  }
}
async function startWargame() {
  try {
    await apiFetch(`/sessions/${sessionId.value}/control`, {
      method: 'POST',
      body: { action: 'RESUME' },
    })
  } finally {
    mapEditMode.value = false
  }
  toasts.push({ severity: 'success', title: '開始兵推', timeoutMs: 2500 })
}
async function onUnitMove(e: { id: string; lng: number; lat: number }) {
  try {
    await apiFetch(`/sessions/${sessionId.value}/units/${e.id}/reposition`, {
      method: 'POST',
      body: { lat: e.lat, lng: e.lng },
    })
  } catch {
    toasts.push({ severity: 'error', title: '單位移動失敗', timeoutMs: 3000 })
  }
  // 無論成敗都重載（成功→定位、失敗→還原到 DB 權威位置）。
  realUnits.value = await fetchUnits(sessionId.value).catch(() => realUnits.value)
}
// 地圖狀態編輯 · 多選整組移動：逐一 reposition（並行）後只重載一次。
function onUnitsSelected(e: { count: number }) {
  selectedUnitCount.value = e.count
}
async function onUnitsMove(e: { moves: { id: string; lng: number; lat: number }[] }) {
  if (!e.moves.length) return
  try {
    await Promise.all(
      e.moves.map((m) =>
        apiFetch(`/sessions/${sessionId.value}/units/${m.id}/reposition`, {
          method: 'POST',
          body: { lat: m.lat, lng: m.lng },
        }),
      ),
    )
    toasts.push({ severity: 'success', title: `已移動 ${e.moves.length} 個單位`, timeoutMs: 2000 })
  } catch {
    toasts.push({ severity: 'error', title: '批次移動失敗（部分單位可能未套用）', timeoutMs: 3000 })
  }
  realUnits.value = await fetchUnits(sessionId.value).catch(() => realUnits.value)
}
/** #99 右鍵控制點 → 刪除該頂點。 */
async function ctxDeleteVertex() {
  const idx = ctxMenu.value?.vertexIndex
  closeCtx()
  if (idx != null) await deleteVertexAt(idx)
}
// 資訊圖卡效能%（#5）——活值優先；缺值時退回 API 初值。health 已是由戰力比導出的效能%。
const hpPct = computed(() => {
  const u = selectedUnit.value
  if (!u) return 0
  return Math.round((liveHealth(u) ?? u.health ?? 100) as number)
})
const hpColor = computed(() => healthColor(hpPct.value))
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
// 選取單位的戰力/平台顯示（漸進消耗一望即知：如「戰力 82/100 · 14 平台」）。
const selForce = computed(() => {
  const u = selectedUnit.value
  if (!u || typeof u.authorized_strength !== 'number') return null
  return {
    cur: Math.round(liveStrength(u) ?? u.strength ?? u.authorized_strength),
    auth: Math.round(u.authorized_strength),
    platforms: u.platform_count ?? 1,
    personnel: u.personnel_current ?? null,
  }
})

// WS stream（O4.3/O4.6）：連 session，顯示收到的裁決事件（stream 於上方 livePos 處宣告）
const streamEvents = computed(() =>
  stream.events.filter((e) => e.type === 'EVENT').slice(-20).reverse(),
)
// 勝負底定橫幅（O11.5/O11.7）：串流出現 SESSION_CONCLUDED 即顯示勝方。
const victory = computed(() => {
  const ev = stream.events.find(
    (e) =>
      e.type === 'EVENT'
      && (e.payload as Record<string, unknown>)?.event_type === 'SESSION_CONCLUDED',
  )
  if (!ev) return null
  const p = ev.payload as Record<string, unknown>
  return { winners: (p.winners as string[]) ?? [], tick: Number(p.tick ?? 0) }
})
// 事件 → 可讀文字（ID→番號、交戰命中/未命中/戰損）。供戰況 feed 即時回饋（含多機同步）。
function unitName(id?: unknown): string {
  const s = typeof id === 'string' ? id : ''
  return (s && realUnits.value.find((u) => u.id === s)?.designation) || s
}
// #27 指令對象：ENGAGE→目標單位；MOVE→目的地 hex（供指令列顯示被下令對象）。
function orderTargetLabel(o: OrderResponse): string {
  if (o.order_type === 'ENGAGE' && o.target_unit_id) {
    const name = realUnits.value.find((u) => u.id === o.target_unit_id)?.designation
    return `→ ${name ?? '敵目標'}`
  }
  if (o.order_type === 'MOVE' && o.target_h3) return `→ ${o.target_h3.slice(0, 9)}`
  return ''
}
function formatEvent(payload: Record<string, unknown>): string {
  const type = String(payload?.event_type ?? '')
  const ini = unitName(payload?.initiator_id)
  const tgt = unitName(payload?.target_id)
  if (type === 'ENGAGEMENT_RESOLVED') {
    const status = String(payload?.status ?? '')
    // 聯合兵種加總（P4）：標示「聯合火力」，讓戰況 feed 區分單武器 vs 武器組合交戰。
    const cx = payload?.mode === 'COMBINED' ? '（聯合火力）' : ''
    if (status === 'HIT') {
      const dmg = payload?.damage != null ? ` −${Math.round(Number(payload.damage))}` : ''
      const hp = Number(payload?.target_health_after)
      const after = Number.isFinite(hp) ? `（剩 ${Math.round(hp)}%）` : ''
      const ko = Number.isFinite(hp) && hp <= 0 ? ' ✖摧毀' : ''
      return `交戰命中${cx} ${ini} → ${tgt}${dmg}${after}${ko}`
    }
    if (status === 'MISS') return `交戰未命中${cx} ${ini} → ${tgt}`
    if (status === 'REJECTED') {
      // 聯合兵種：優先顯示逐武器原因彙總（如「無視線×2、超射程×1、無彈藥×1」），比單一 code 清楚。
      const why = payload?.reason_detail || payload?.reason || ''
      return `交戰不可行 ${ini} → ${tgt}（${why}）`
    }
    return `交戰 ${ini} → ${tgt}`
  }
  if (type === 'UNIT_ARRIVED') return `${ini} 已抵達目標`
  if (type === 'MOVE_ATTRITION') {
    const dmg = payload?.damage != null ? Math.round(Number(payload.damage)) : ''
    return `${ini} 強穿阻礙受損 −${dmg}`
  }
  if (type === 'COMMS_STATE_CHANGED') {
    return `${ini} 通聯 ${commsLabel(String(payload?.from ?? ''))}→${commsLabel(String(payload?.to ?? ''))}`
  }
  const ot = payload?.order_type ? ` · ${orderTypeLabel(String(payload.order_type))}` : ''
  return `${type}${ot}`
}

async function back() {
  stream.disconnect()
  await navigateTo('/lobby')
}

// 定時與核心系統重新同步：WS STATE_DIFF 已即時推變動，但週期性重取讓多機同時查看
// （教學情境）在初始狀態/漏收/DB 權威更新後仍趨於一致——且**指令列表沒有 WS 推播**，
// 只有這條路徑會更新它。WP-E3 把它從「六個獨立 GET」改為「一次原子快照 + 指令」：
// race 的來源是**非原子**，不是「有週期」，所以留下節奏、拿掉拼裝。
let resyncTimer: ReturnType<typeof setInterval> | null = null
onMounted(async () => {
  if (!auth.user) await auth.fetchMe() // 直接開/重整 COP 時補抓使用者，讓角色相關入口（白軍控制台）正確顯示
  refresh()
  stream.connect(sessionId.value, viewpoint.value || null)
  aiStatus.start() // #79 AI 決策狀態輪詢（思考中／倒數）
  if (import.meta.client) resyncTimer = setInterval(() => refresh(), 10_000)
})
onBeforeUnmount(() => {
  stream.disconnect()
  aiStatus.stop()
  if (resyncTimer) clearInterval(resyncTimer)
})
</script>

<template>
  <div class="cop">
    <header class="cop-bar">
      <button data-testid="back-lobby" @click="back">← 系統首頁</button>
      <span class="sid" data-testid="cop-session">Session {{ sessionId }}</span>
      <span class="count" data-testid="unit-count">單位 {{ ownUnits.length }}</span>
      <!-- WP-C5：本軍通聯不良 → 敵情圖已在**後端**粗化（位置跳到 3km 格心、只剩 DETECTED）。
           不標的話操作者只會覺得「敵情圖怎麼突然變爛了」而不知道原因。 -->
      <span
        v-if="commsPosture && commsPosture !== 'ONLINE'"
        class="posture"
        data-testid="comms-posture"
        :title="
          commsPosture === 'OFFLINE'
            ? '本軍通聯全斷：敵情圖停止更新並已粗化到約 3km 格'
            : '本軍通聯不良：敵情位置已粗化到約 3km 格、情報等級降為 DETECTED'
        "
      >
        <i class="pi pi-wifi" /> 敵情粗化（{{ commsLabel(commsPosture) }}）
      </span>
      <ClientOnly><SimClockBar :tick="stream.lastTick" :start-time="sessionStart" /></ClientOnly>
      <nav class="cop-nav">
        <!-- 視角切換（#90）：僅全知角色。選陣營＝以該陣營之眼觀戰（後端套其戰場迷霧）。 -->
        <label v-if="canControl" class="vp" :class="{ 'vp-on': !!viewpoint }">
          <i class="pi pi-eye" />
          <select
            v-model="viewpoint"
            data-testid="viewpoint"
            :title="
              viewpoint
                ? `目前以 ${viewpoint} 視角觀戰：只看得到該陣營看得到的（含其偵測到的敵情）`
                : '全局視角（全知）：看得到所有陣營的單位'
            "
          >
            <option value="">全局視角（全知）</option>
            <option v-for="f in sessionFactions" :key="f" :value="f">{{ f }} 視角</option>
          </select>
        </label>
        <!-- 導覽鈕一律只留 icon（頂列空間留給地圖）；名稱與說明走 data-tip 的 hover 提示。
             不用原生 title：延遲約 1 秒且樣式不受控，icon-only 之下等於沒有提示。 -->
        <button
          v-if="canControl && !mapEditMode"
          class="icon-btn"
          data-testid="nav-map-edit"
          data-tip="地圖狀態編輯"
          data-tip2="暫停推演、拖放單位、繪障礙，完成再開始"
          aria-label="地圖狀態編輯"
          @click="enterMapEdit"
        >
          <i class="pi pi-pencil" />
        </button>
        <button
          v-if="canControl"
          class="icon-btn"
          data-testid="nav-white-cell"
          data-tip="白軍控制台"
          data-tip2="時間控制、注入事件、視角"
          aria-label="白軍控制台"
          @click="navigateTo(`/session/${sessionId}/white-cell`)"
        >
          <i class="pi pi-cog" />
        </button>
        <button
          v-if="canManageEquip"
          class="icon-btn"
          data-testid="nav-equip-mgr"
          data-tip="裝備管理"
          data-tip2="編輯各單位配發的武器/裝備（白軍編任一；本軍需該局開放自編）"
          aria-label="裝備管理"
          @click="openEquipMgr"
        >
          <i class="pi pi-box" />
        </button>
        <div class="widget-menu">
          <button
            class="icon-btn"
            data-testid="nav-widgets"
            :class="{ on: widgetMenuOpen }"
            data-tip="工具"
            data-tip2="開啟/關閉小工具視窗"
            aria-label="工具視窗"
            @click="widgetMenuOpen = !widgetMenuOpen"
          >
            <i class="pi pi-th-large" />
          </button>
          <template v-if="widgetMenuOpen">
            <div class="wm-backdrop" @click="widgetMenuOpen = false" />
            <div class="wm-pop" data-testid="widget-menu">
              <div class="wm-hd">工具視窗</div>
              <label
                v-for="d in WIDGET_DEFS"
                v-show="d.id !== 'mapedit' || canDraw"
                :key="d.id"
                :class="{ off: !widgets[d.id].open }"
                :data-testid="`widget-toggle-${d.id}`"
              >
                <input type="checkbox" :checked="widgets[d.id].open" @change="toggleWidget(d.id)">
                {{ d.label }}
              </label>
            </div>
          </template>
        </div>
        <button
          v-if="canControl"
          class="icon-btn"
          data-testid="nav-autonomy"
          data-tip="自主推演"
          data-tip2="指派 AI 控制陣營"
          aria-label="自主推演"
          @click="navigateTo(`/session/${sessionId}/autonomy`)"
        >
          <i class="pi pi-bolt" />
        </button>
        <button
          class="icon-btn"
          data-testid="nav-aar"
          data-tip="AAR"
          data-tip2="戰後檢討報告"
          aria-label="AAR 戰後檢討"
          @click="navigateTo(`/session/${sessionId}/aar`)"
        >
          <i class="pi pi-chart-bar" />
        </button>
      </nav>
    </header>
    <div v-if="mapEditMode" class="mapedit-bar" data-testid="mapedit-bar">
      <i class="pi pi-pencil" />
      <span v-if="selectedUnitCount" class="meb-badge" data-testid="selected-count">已選 {{ selectedUnitCount }} 個</span>
      <span class="meb-txt">
        <strong>地圖狀態編輯（推演已暫停）</strong>——拖曳單位調整位置；<b>Shift＋點單位</b>可多選、<b>Shift＋空白處拖曳</b>可框選，再拖曳任一選取單位即整組移動；用「地圖編輯」工具繪障礙/建築。
      </span>
      <button class="meb-start" data-testid="start-wargame" @click="startWargame">
        ▶ 開始兵推
      </button>
    </div>
    <div v-if="victory" class="victory-banner" data-testid="victory-banner">
      🏁 推演結束 —
      <strong>{{ victory.winners.length ? `${victory.winners.join('、')} 獲勝` : '平手' }}</strong>
      （tick {{ victory.tick }}）
      <button class="vb-aar" @click="navigateTo(`/session/${sessionId}/aar`)">看 AAR →</button>
    </div>
    <!-- #79 AI 決策狀態列（思考中／下一次決策倒數）——僅在本局有 AI 陣營時顯示 -->
    <div v-if="aiChips.length" class="ai-status-bar" data-testid="ai-status-bar">
      <span class="asb-label"><i class="pi pi-bolt" /> AI 指揮</span>
      <span
        v-for="c in aiChips"
        :key="c.faction"
        class="asb-chip"
        :class="c.state"
        :data-testid="`ai-status-${c.faction}`"
      >
        <b class="asb-fac">{{ c.faction }}</b>
        <span v-if="c.state === 'thinking'" class="asb-state"
          ><i class="pi pi-spin pi-spinner" /> 思考中…</span
        >
        <span v-else-if="c.state === 'idle'" class="asb-state"
          >下一次決策 <b>{{ c.countdown }}</b></span
        >
        <span v-else class="asb-state asb-off">離線</span>
      </span>
    </div>

    <!-- 裝備管理面板：白軍編任一單位編裝 + 設各軍自編權限；本軍（開放自編）僅編本軍單位 -->
    <div v-if="equipMgr" class="equip-overlay" data-testid="equip-mgr" @click.self="equipMgr = false">
      <div class="equip-modal">
        <div class="eq-hd">
          <h3><i class="pi pi-box" /> 裝備管理</h3>
          <button class="eq-x" data-testid="equip-close" @click="equipMgr = false"><i class="pi pi-times" /></button>
        </div>

        <div v-if="canControl" class="eq-perms" data-testid="equip-perms">
          <div class="eq-perms-hd">各軍自編權限（開放後該陣營指揮官可自行編裝本軍單位）</div>
          <div class="eq-perms-row">
            <label
              v-for="g in unitsByFaction"
              :key="g.faction"
              class="eq-perm"
              :data-testid="`equip-perm-${g.faction}`"
            >
              <input
                type="checkbox"
                :checked="orbatPerms.includes(g.faction)"
                @change="toggleOrbatPerm(g.faction)"
              >
              <span class="u-dot" :style="{ background: factionColor(g.faction) }" />{{ g.faction }}
            </label>
            <span v-if="!unitsByFaction.length" class="eq-hint">（本局尚無單位）</span>
          </div>
        </div>

        <div class="eq-body">
          <div class="eq-units" data-testid="equip-unit-list">
            <div v-for="g in equipEditableFactions" :key="g.faction" class="eq-fac">
              <div class="eq-fac-hd">
                <span class="u-dot" :style="{ background: factionColor(g.faction) }" /><b>{{ g.faction }}</b>
                <span class="dim">· {{ g.units.length }}</span>
              </div>
              <button
                v-for="u in g.units"
                :key="u.id"
                class="eq-unit"
                :class="{ sel: u.id === equipUnitId }"
                data-testid="equip-unit"
                @click="equipUnitId = u.id"
              >
                {{ u.designation }}
              </button>
            </div>
            <p v-if="!equipEditableFactions.length" class="eq-hint">（無可編裝的單位）</p>
          </div>
          <div class="eq-editor">
            <div v-if="equipUnitId" class="eq-editor-in">
              <div class="eq-editor-hd">編裝 · {{ realUnits.find((u) => u.id === equipUnitId)?.designation ?? equipUnitId }}</div>
              <UnitOrbatEditor :session-id="sessionId" :unit-id="equipUnitId" :can-edit="true" />
            </div>
            <p v-else class="eq-hint">← 選一個單位以編輯其武器/裝備配發</p>
          </div>
        </div>
      </div>
    </div>

    <div class="body">
      <!-- #12 停靠側欄容器（拖到最左/右緣的視窗落於此；空則以 :empty 隱藏）。 -->
      <div id="dock-left-col" class="dock-col left" />
      <div id="dock-right-col" class="dock-col right" />
      <ClientOnly>
      <Teleport
        :to="widgets.units.dock === 'left' ? '#dock-left-col' : '#dock-right-col'"
        :disabled="widgets.units.dock === 'float'"
      >
      <FloatingWidget
        v-if="widgets.units.open"
        title="單位 / 下令"
        :geom="widgets.units"
        :z="widgets.units.z"
        :docked="widgets.units.dock !== 'float'"
        @update:geom="(g) => setWidgetGeom('units', g)"
        @grab="(g) => onWidgetGrab('units', g)"
        @drop="(g) => onWidgetDrop('units', g)"
        @close="widgets.units.open = false"
        @focus="focusWidget('units')"
      >
        <div class="wsec-hd">單位（{{ realUnits.length }}）</div>
        <div class="units" data-testid="unit-list">
          <div
            v-for="g in unitsByFaction"
            :key="g.faction"
            class="ufac"
            data-testid="unit-faction-group"
          >
            <button
              class="ufac-hd"
              data-testid="unit-faction-head"
              :title="`${g.faction}：${g.units.length} 單位、戰力 ${Math.round(g.power.pct)}%`
                + `（各單位效能以量體加權平均，總量體 ${Math.round(g.power.mass)}）`
                + (g.power.ko ? `；已折損 ${g.power.ko} 個單位` : '')
                + ' — 點擊收合/展開'"
              @click="toggleFactionGroup(g.faction)"
            >
              <i class="pi" :class="collapsedFactions.has(g.faction) ? 'pi-chevron-right' : 'pi-chevron-down'" />
              <span class="u-dot" :style="{ background: factionColor(g.faction) }" />
              <b>{{ g.faction }}</b>
              <span class="ufac-count">· {{ g.units.length }}</span>
              <span
                class="ufac-pow"
                :style="{ color: healthColor(Math.round(g.power.pct)) }"
                data-testid="unit-faction-power"
              >{{ Math.round(g.power.pct) }}%</span>
              <span v-if="g.power.ko" class="ufac-ko">✖{{ g.power.ko }}</span>
            </button>
            <ul v-show="!collapsedFactions.has(g.faction)" class="ufac-units">
              <li
                v-for="u in g.units"
                :key="u.id"
                :class="{ sel: u.id === selectedId, 'out-scope': !inScope(u) }"
                :title="inScope(u) ? '' : '不在你的指揮範圍（此帳號僅獲授權指揮部分單位）'"
                data-testid="unit-item"
                @click="inScope(u) ? selectUnit(u.id) : null"
              >
                {{ u.designation }} ·
                <span class="u-hp" :style="{ color: healthColor(Math.round(liveHealth(u) ?? 100)) }">
                  {{ Math.round(liveHealth(u) ?? 100) }}%
                </span>
                <span v-if="u.is_fixed" class="u-fixed" title="固定單位（指揮部等）：不可移動">🔒</span>
                <span v-if="!inScope(u)" class="u-ban" title="不在指揮範圍">🚫</span>
                <span v-if="(liveHealth(u) ?? 100) <= 0" class="u-ko">✖ 摧毀</span>
              </li>
            </ul>
          </div>
          <div v-if="!realUnits.length" class="empty">（此 session 無可下令單位）</div>
        </div>

        <div v-if="selectedId" class="order" data-testid="order-panel">
          <h3>下令 · <span class="selunit" data-testid="selected-unit">{{ selectedUnit?.designation ?? selectedId }}</span></h3>
          <select v-model="orderType" data-testid="order-type">
            <option value="MOVE">移動</option>
            <option value="ENGAGE">交戰</option>
          </select>
          <p v-if="selectedUnitFixed" class="fixed-note" data-testid="fixed-note">
            🔒 固定單位（指揮部等）——不可下移動令；此單位不會被派去移動或機動交戰（可於劇本編輯器調整）。
          </p>
          <template v-if="orderType === 'MOVE' && !selectedUnitFixed">
            <label class="precise">
              <input v-model="preciseMove" type="checkbox" data-testid="precise-move">
              精確移動（走到點擊處，不吸附六角格心）
            </label>
            <div class="movebtns">
              <button
                data-testid="pick-dest"
                :class="{ armed: targeting }"
                @click="waypointMode = false; targeting = true"
              >
                {{ targeting ? '點地圖設目標…' : '設定目標點' }}
              </button>
              <button
                data-testid="pick-waypoints"
                :class="{ armed: waypointMode }"
                title="逐點點擊地圖建立自訂路徑"
                @click="targeting = false; waypointMode = !waypointMode"
              >
                {{ waypointMode ? `加點中…（${moveWaypoints.length}）` : '自訂路徑' }}
              </button>
              <button
                v-if="moveWaypoints.length"
                data-testid="undo-waypoint"
                title="移除最後一個路徑點"
                @click="undoWaypoint"
              >
                <i class="pi pi-undo" /> 退一點
              </button>
              <button
                v-if="destH3 || moveWaypoints.length"
                data-testid="clear-path"
                title="清除路徑"
                @click="clearMovePath"
              >
                <i class="pi pi-times" /> 清除
              </button>
            </div>
            <div class="dest" data-testid="dest-h3">
              {{ destH3 || '未設目標' }}
              <span v-if="destH3 && !preciseMove && !moveWaypoints.length" class="snaphint">· 吸附至六角格心（大範圍省算；近距會跑回格心）</span>
              <span v-if="destH3 && preciseMove && !moveWaypoints.length" class="snaphint precise">· 精確落點（單位走到黃色標記；近距作戰建議）</span>
              <span v-if="moveWaypoints.length" class="snaphint precise">· 自訂路徑 {{ moveWaypoints.length }} 點</span>
            </div>
            <!-- #28 路徑成本試算 -->
            <div v-if="movePreview" class="mvprev" data-testid="move-preview">
              <div class="mv-row">
                <span>距離 <b>{{ (movePreview.distance_m / 1000).toFixed(2) }} km</b></span>
                <span>約 <b>{{ movePreview.duration_ticks }}</b> tick</span>
                <span v-if="movePreview.fuel_cost > 0">油耗 <b>{{ movePreview.fuel_cost.toFixed(0) }}</b></span>
              </div>
              <!-- #80/#81：機動能力 + 實際速度（已含地形/坡度調變） -->
              <div class="mv-row mv-sub">
                <span :title="`機動 profile（由編裝導出）：${movePreview.mobility_profile}`">
                  <i class="pi pi-forward" /> {{ mobilityLabel(movePreview.mobility_profile) }}
                  <b>{{ movePreview.speed_kmh.toFixed(1) }}</b> km/h
                </span>
                <span v-if="movePreview.terrain_routed" class="mv-routed" title="已依地形 A* 繞開不可通行區（非直線）">
                  <i class="pi pi-share-alt" /> 地形繞路
                </span>
              </div>
              <!-- #84：油料是否夠走完全程 -->
              <div v-if="movePreview.fuel_remaining > 0" class="mv-row mv-sub">
                <span :class="{ 'mv-lowfuel': !movePreview.fuel_sufficient }">
                  <i class="pi pi-bolt" /> 油料 <b>{{ movePreview.fuel_remaining.toFixed(0) }}</b>
                  <template v-if="!movePreview.fuel_sufficient">（不足，將中途拋錨）</template>
                </span>
              </div>
              <div v-if="movePreview.terrain_impassable" class="mv-forced" data-testid="move-impassable">
                ⛔ 路徑穿越此單位<b>無法通行</b>的地形（{{ mobilityLabel(movePreview.mobility_profile) }}）——將於邊界停止
              </div>
              <div v-else-if="movePreview.feasible" class="mv-ok" title="此預覽僅檢查路徑上的已知障礙與地形；地形可達性於送出時再驗證">
                ✓ 無障礙阻擋（地形可達性於送出時驗證）
              </div>
              <div v-else class="mv-forced" data-testid="move-forced">
                ⚠ 需強穿 {{ movePreview.crossings.length }} 處阻礙（隨機額外耗損）
                <ul>
                  <li v-for="(c, i) in movePreview.crossings" :key="i">
                    {{ crossKindLabel(c.kind) }}{{ c.label ? `（${c.label}）` : '' }}
                  </li>
                </ul>
              </div>
            </div>
          </template>
          <template v-else>
            <div class="hint">點地圖上的敵方單位鎖定目標（紅環），或從清單選：</div>
            <select v-model="targetUnitId" data-testid="engage-target">
              <option :value="null">選目標</option>
              <option v-for="u in engageTargets" :key="u.id" :value="u.id">{{ u.designation }}</option>
            </select>
            <div class="dest" data-testid="target-label">
              {{ targetUnit ? `🎯 ${targetUnit.designation}（${targetUnit.faction}）` : '未鎖定目標' }}
            </div>
            <template v-if="weapons.length">
              <select v-model="weaponId" data-testid="engage-weapon">
                <option :value="null">{{ weapons.length >= 2 ? '聯合火力（全武器一起打）' : '預設武器' }}</option>
                <option v-for="w in weapons" :key="w.id" :value="w.id">
                  {{ w.name }}<span v-if="liveAmmo(w) != null"> · 彈 {{ liveAmmo(w) }}</span>
                </option>
              </select>
              <!-- 聯合火力（未選單一武器且 ≥2 武器）：顯示將開火的武器組合 + 火力政策（P4）。 -->
              <template v-if="combinedMode">
                <select v-model="firePolicy" data-testid="engage-fire-policy">
                  <option v-for="p in FIRE_POLICY_OPTS" :key="p.value" :value="p.value">
                    {{ p.label }}
                  </option>
                </select>
                <ul class="weapon-mix" data-testid="weapon-mix">
                  <li v-for="w in weapons" :key="w.id">
                    <i class="pi pi-bullseye" /> {{ w.name }}
                    <span v-if="w.max_range_m" class="dim">· {{ (w.max_range_m / 1000).toFixed(1) }} km</span>
                    <span v-if="liveAmmo(w) != null" class="dim">· 彈 {{ liveAmmo(w) }}</span>
                  </li>
                </ul>
              </template>
              <!-- 指定單一武器：彈種選擇（單武器射擊路徑）。 -->
              <select
                v-if="!combinedMode && ammoOptions.length"
                v-model="ammoType"
                data-testid="engage-ammo"
              >
                <option :value="null">彈種（預設）</option>
                <option v-for="a in ammoOptions" :key="a" :value="a">{{ a }}</option>
              </select>
            </template>
          </template>
          <button
            data-testid="submit-order"
            :disabled="orderType === 'MOVE' ? !destH3 : !targetUnitId"
            @click="submit"
          >
            {{ orderType === 'MOVE' ? '送出移動' : '送出交戰' }}
          </button>
          <p v-if="message" data-testid="order-message">{{ message }}</p>
          <div v-if="precheck" class="precheck" data-testid="precheck">
            <div :class="precheck.feasible ? 'ok' : 'bad'">
              預檢：{{ precheck.feasible ? '可行' : '不可行' }}
            </div>
            <ul>
              <li v-for="(c, i) in precheck.checks" :key="i">
                {{ c.passed ? '✓' : '✗' }} {{ c.name }} <span v-if="c.detail">— {{ c.detail }}</span>
              </li>
            </ul>
          </div>
        </div>
      </FloatingWidget>
      </Teleport>

      <Teleport
        :to="widgets.events.dock === 'left' ? '#dock-left-col' : '#dock-right-col'"
        :disabled="widgets.events.dock === 'float'"
      >
      <FloatingWidget
        v-if="widgets.events.open"
        title="戰況事件"
        :geom="widgets.events"
        :z="widgets.events.z"
        :docked="widgets.events.dock !== 'float'"
        @update:geom="(g) => setWidgetGeom('events', g)"
        @grab="(g) => onWidgetGrab('events', g)"
        @drop="(g) => onWidgetDrop('events', g)"
        @close="widgets.events.open = false"
        @focus="focusWidget('events')"
      >
        <div class="wsec-hd">戰況事件 <span class="ws">· {{ stream.status }}</span></div>
        <ul class="events" data-testid="event-list">
          <li v-for="(e, i) in streamEvents" :key="i" data-testid="event-row">
            {{ formatEvent(e.payload as Record<string, unknown>) }}
          </li>
          <li v-if="!streamEvents.length" class="empty">（尚無事件）</li>
        </ul>
      </FloatingWidget>
      </Teleport>

      <Teleport
        :to="widgets.orders.dock === 'left' ? '#dock-left-col' : '#dock-right-col'"
        :disabled="widgets.orders.dock === 'float'"
      >
      <FloatingWidget
        v-if="widgets.orders.open"
        title="指令"
        :geom="widgets.orders"
        :z="widgets.orders.z"
        :docked="widgets.orders.dock !== 'float'"
        @update:geom="(g) => setWidgetGeom('orders', g)"
        @grab="(g) => onWidgetGrab('orders', g)"
        @drop="(g) => onWidgetDrop('orders', g)"
        @close="widgets.orders.open = false"
        @focus="focusWidget('orders')"
      >
        <div class="wsec-hd">指令（{{ orders.length }}）</div>
        <ul class="orders" data-testid="order-list">
          <li v-for="o in sortedOrders" :key="o.id" data-testid="order-row">
            <div class="ord-main">
              <span class="ord-unit">{{ unitName(o.unit_id) || '單位' }}</span>
              <span class="ord-type">{{ orderTypeLabel(o.order_type) }}</span>
              <span v-if="orderTargetLabel(o)" class="ord-tgt">{{ orderTargetLabel(o) }}</span>
            </div>
            <div class="ord-meta">
              <span class="ord-time" title="下令 sim tick">T{{ o.issued_at_tick
                }}<span v-if="o.resolved_at_tick != null"> → T{{ o.resolved_at_tick }}</span></span>
              <span class="ord-status" :class="`st-${o.status}`">{{ orderStatusLabel(o.status) }}</span>
              <button
                v-if="o.status === 'VALIDATED' || o.status === 'PENDING' || o.status === 'EXECUTING'"
                data-testid="cancel-order"
                :title="o.status === 'EXECUTING' ? '停止移動並就地凍結（不彈回原位）' : '取消未執行指令'"
                @click="cancel(o.id)"
              >
                {{ o.status === 'EXECUTING' ? '停止' : '取消' }}
              </button>
            </div>
          </li>
          <li v-if="!orders.length" class="empty">（無指令）</li>
        </ul>
      </FloatingWidget>
      </Teleport>
      </ClientOnly>

      <div
        class="map-wrap"
        :style="{
          '--ldock': hasLeftDock ? `${DOCK_W}px` : '0px',
          '--rdock': hasRightDock ? `${DOCK_W}px` : '0px',
        }"
      >
        <ClientOnly>
          <MapCanvas
            :hex-visible="hex"
            :hillshade-visible="hillshade"
            :contour-visible="contour"
            :own-units="ownUnits"
            :contacts="contacts"
            :current-tick="currentTick"
            :selected-id="selectedId"
            :target-id="targetUnitId"
            :basemap-id="basemap"
            :dest-h3="destH3"
            :dest-point="destLatLng"
            :move-path="movePathCoords"
            :move-forced="movePreview?.forced ?? false"
            :move-crossings="moveCrossPoints"
            :layer-opacity="layerOpacity"
            :layer-order="layerOrder"
            :contour-major="contourMajor"
            :contour-minor="contourMinor"
            :hex-line-width="hexLineWidth"
            :contour-major-width="contourMajorWidth"
            :contour-minor-width="contourMinorWidth"
            :hex-line-color="hexLineColor"
            :contour-color="contourColor"
            :grid-color="gridColor"
            :grid-width="gridWidth"
            :mgrs-color="mgrsColor"
            :feature-fc="featureFc"
            :feat-symbol-fc="featSymbol.fc"
            :feat-symbol-icons="featSymbol.icons"
            :influence-fc="influenceFc"
            :draft-fc="draftFc"
            :weapon-track-fc="weaponTrackFc"
            :selected-feature-id="selectedFeatureId"
            :feature-edit="canEditSelectedFeature"
            :draw-active="drawActive"
            :latlng-grid="latlngGrid"
            :mgrs-grid="mgrsGrid"
            :grid-step-deg="gridStepDeg"
            :query-point="queryPoint"
            :hex-max-res="hexMaxRes"
            :hex-limit-km="hexLimitKm"
            :day-night="dayNight"
            :time-of-day="timeOfDay"
            :targeting="targeting"
            :edit-units="mapEditMode"
            @units-move="onUnitsMove"
            @units-selected="onUnitsSelected"
            @map-click="onMapClick"
            @unit-click="onUnitClick"
            @select-screen-pos="onSelectScreenPos"
            @feature-click="onFeatureClick"
            @feature-move="onFeatureMove"
            @feature-reshape="onFeatureReshape"
            @feature-vertex-delete="onFeatureVertexDelete"
            @unit-move="onUnitMove"
            @basemap-error="onBasemapError"
            @context-menu="onContextMenu"
          />
          <template #fallback>
            <div class="map-loading" data-testid="map-loading">地圖載入中…</div>
          </template>
        </ClientOnly>
        <ClientOnly>
        <Teleport
          :to="widgets.layers.dock === 'right' ? '#dock-right-col' : '#dock-left-col'"
          :disabled="widgets.layers.dock === 'float'"
        >
        <FloatingWidget
          v-if="widgets.layers.open"
          title="圖層 / 底圖"
          :geom="widgets.layers"
          :z="widgets.layers.z"
          :docked="widgets.layers.dock !== 'float'"
          @update:geom="(g) => setWidgetGeom('layers', g)"
          @grab="(g) => onWidgetGrab('layers', g)"
          @drop="(g) => onWidgetDrop('layers', g)"
          @close="widgets.layers.open = false"
          @focus="focusWidget('layers')"
        >
          <LayerToggles
            v-model:hex="hex"
            v-model:hillshade="hillshade"
            v-model:contour="contour"
            v-model:basemap="basemap"
            v-model:layer-opacity="layerOpacity"
            v-model:layer-order="layerOrder"
            v-model:contour-major="contourMajor"
            v-model:contour-minor="contourMinor"
            v-model:latlng-grid="latlngGrid"
            v-model:mgrs-grid="mgrsGrid"
            v-model:grid-step-deg="gridStepDeg"
            v-model:hex-max-res="hexMaxRes"
            v-model:hex-limit-km="hexLimitKm"
            v-model:day-night="dayNight"
            v-model:time-of-day="timeOfDay"
            v-model:hex-line-width="hexLineWidth"
            v-model:contour-major-width="contourMajorWidth"
            v-model:contour-minor-width="contourMinorWidth"
            v-model:hex-line-color="hexLineColor"
            v-model:contour-color="contourColor"
            v-model:grid-color="gridColor"
            v-model:grid-width="gridWidth"
            v-model:mgrs-color="mgrsColor"
            :hillshade-enabled="hasTiles"
            :contour-enabled="hasTiles"
            :basemaps="basemapSources"
          />
        </FloatingWidget>
        </Teleport>
        </ClientOnly>
        <div v-if="!hasTiles" class="map-notice" data-testid="map-notice">
          <strong>離線底圖模式</strong>
          <span>目前顯示經緯格線 + 單位符號（無向量瓦片）。要載入台灣街道/地形底圖，需由
            <code>taiwan.osm.pbf</code> 產生 mbtiles 並啟用 tileserver。</span>
        </div>

        <!-- 線條粗細/顏色（#22）已併入「圖層」小工具，不再獨立浮動 modal。 -->

        <!-- 右鍵選單（#3，ATAK 式移動/攻擊）：右鍵單位/地圖 → 移動/攻擊 → 十字準星 → 點目標。 -->
        <template v-if="ctxMenu">
          <div class="ctx-backdrop" @click="closeCtx" @contextmenu.prevent="closeCtx" />
          <div
            class="ctx-menu"
            data-testid="ctx-menu"
            :style="{ left: `${ctxMenu.x}px`, top: `${ctxMenu.y}px` }"
          >
            <!-- #99 右鍵控制點：刪點優先於一般物件選單（游標下同時有頂點與圖形本體）。 -->
            <template v-if="ctxMenu?.vertexIndex != null && canEditSelectedFeature">
              <div class="ctx-title">控制點 #{{ ctxMenu.vertexIndex + 1 }}</div>
              <button class="ctx-danger" data-testid="ctx-vertex-del" @click="ctxDeleteVertex">
                <i class="pi pi-trash" /> 刪除控制點
              </button>
            </template>
            <template v-else-if="ctxMenu?.featureId && canDraw">
              <div class="ctx-title">地圖物件</div>
              <button data-testid="ctx-feat-edit" @click="ctxEditFeature">
                <i class="pi pi-pencil" /> 編輯形狀 / 屬性
              </button>
              <button data-testid="ctx-feat-rot-ccw" @click="ctxRotateFeature(-15)"><i class="pi pi-undo" /> 旋轉 15°</button>
              <button data-testid="ctx-feat-rot-cw" @click="ctxRotateFeature(15)"><i class="pi pi-refresh" /> 旋轉 15°</button>
              <button class="ctx-danger" data-testid="ctx-feat-del" @click="ctxDeleteFeature"><i class="pi pi-trash" /> 刪除</button>
            </template>
            <template v-else-if="ctxIsMine">
              <div class="ctx-title">{{ ctxUnitName }}</div>
              <button data-testid="ctx-move" @click="ctxArmMove"><i class="pi pi-arrow-right" /> 移動</button>
              <button data-testid="ctx-attack" @click="ctxArmAttack"><i class="pi pi-bullseye" /> 攻擊</button>
            </template>
            <template v-else-if="ctxIsEnemy && selectedId">
              <div class="ctx-title">目標：{{ ctxUnitName }}</div>
              <button data-testid="ctx-lock-target" @click="ctxLockTarget">
                <i class="pi pi-bullseye" /> 以「{{ selectedUnit?.designation ?? selectedId }}」攻擊
              </button>
            </template>
            <template v-else-if="selectedId">
              <div class="ctx-title">{{ selectedUnit?.designation ?? selectedId }}</div>
              <button data-testid="ctx-move-here" @click="ctxMoveHere"><i class="pi pi-arrow-right" /> 移動至此</button>
              <button data-testid="ctx-attack" @click="ctxArmAttack"><i class="pi pi-bullseye" /> 攻擊…</button>
            </template>
            <template v-else>
              <div class="ctx-empty">先選取我方單位</div>
            </template>
          </div>
        </template>

        <!-- 地圖編輯器（stage ③b）：繪製標註/工事/武器據點。 -->
        <ClientOnly>
        <Teleport
          :to="widgets.mapedit.dock === 'right' ? '#dock-right-col' : '#dock-left-col'"
          :disabled="widgets.mapedit.dock === 'float'"
        >
        <FloatingWidget
          v-if="mapEditorOpen && canDraw"
          title="地圖編輯"
          :geom="widgets.mapedit"
          :z="widgets.mapedit.z"
          :docked="widgets.mapedit.dock !== 'float'"
          @update:geom="(g) => setWidgetGeom('mapedit', g)"
          @grab="(g) => onWidgetGrab('mapedit', g)"
          @drop="(g) => onWidgetDrop('mapedit', g)"
          @close="widgets.mapedit.open = false"
          @focus="focusWidget('mapedit')"
        >
          <MapEditorPanel
            :editor="mapEditorView"
            :can-control="canControl"
            :session-factions="sessionFactions"
            :hidden-feature-ids="hiddenFeatureIds"
            @toggle-hidden="toggleFeatureHidden"
          />
        </FloatingWidget>
        </Teleport>
        </ClientOnly>

        <!-- 座標查詢讀值（#10）：點地圖任一點顯示經緯度 + MGRS。 -->
        <ClientOnly>
        <Teleport
          :to="widgets.coords.dock === 'right' ? '#dock-right-col' : '#dock-left-col'"
          :disabled="widgets.coords.dock === 'float'"
        >
        <FloatingWidget
          v-if="coordQuery"
          title="座標查詢"
          :geom="widgets.coords"
          :z="widgets.coords.z"
          :docked="widgets.coords.dock !== 'float'"
          @update:geom="(g) => setWidgetGeom('coords', g)"
          @grab="(g) => onWidgetGrab('coords', g)"
          @drop="(g) => onWidgetDrop('coords', g)"
          @close="widgets.coords.open = false"
          @focus="focusWidget('coords')"
        >
          <div class="coord-readout" data-testid="coord-readout">
            <div class="cr-hd">座標查詢 · 點地圖任一點</div>
            <template v-if="queryPoint">
              <div class="cr-row"><span>緯度</span><code>{{ queryPoint.lat.toFixed(5) }}</code></div>
              <div class="cr-row"><span>經度</span><code>{{ queryPoint.lng.toFixed(5) }}</code></div>
              <div class="cr-row"><span>MGRS</span><code data-testid="coord-mgrs">{{ queryMgrs }}</code></div>
            </template>
            <div v-else class="cr-hint">尚未點選</div>
          </div>
        </FloatingWidget>
        </Teleport>
        </ClientOnly>

        <!-- 單位詳細資訊圖卡（#5）：懸浮於選取圖標旁（#Fix C），非固定左下。 -->
        <div
          v-if="selectedUnit"
          class="unit-card"
          :class="{ 'card-anchored': !!unitCardPos && !unitCardDrag, 'card-dragged': !!unitCardDrag }"
          :style="unitCardStyle"
          data-testid="unit-detail-card"
        >
          <button class="card-close" data-testid="card-close" title="關閉（取消選取）" @click="clearSelection">
            <i class="pi pi-times" />
          </button>
          <div
            class="card-hd"
            title="拖曳可移動資訊卡"
            @mousedown="beginCardDrag"
            @touchstart="beginCardDrag"
          >
            <span class="card-grip"><i class="pi pi-bars" /></span>
            <span class="fdot" :style="{ background: factionColor(selectedUnit.faction) }" />
            <strong class="cname">{{ selectedUnit.designation }}</strong>
            <span class="clevel">{{ unitLevelLabel(selectedUnit.unit_level) }} · {{ selectedUnit.faction }}</span>
          </div>
          <div class="hpbar" :title="`作戰效能 ${hpPct}%`">
            <div class="hpfill" :style="{ width: `${hpPct}%`, background: hpColor }" />
            <span class="hptxt">效能 {{ hpPct }}%</span>
          </div>
          <dl class="card-meta">
            <div v-if="selForce">
              <dt>戰力</dt>
              <dd>
                {{ selForce.cur }}/{{ selForce.auth }}
                <span class="dim">· {{ selForce.platforms }} 平台</span>
                <span v-if="selForce.personnel != null" class="dim">· {{ selForce.personnel }} 人</span>
              </dd>
            </div>
            <div>
              <dt>通聯</dt>
              <dd :class="`comms-${liveComms(selectedUnit).toLowerCase()}`" data-testid="unit-comms">
                {{ commsLabel(liveComms(selectedUnit)) }}
              </dd>
            </div>
            <div>
              <dt>座標</dt>
              <dd data-testid="unit-coords">
                {{ (selectedUnit.lat ?? 0).toFixed(4) }}, {{ (selectedUnit.lng ?? 0).toFixed(4) }}
                <!-- WP-C5：斷聯單位的座標是最後一次回報，不是現在的位置。不標的話指揮官
                     會拿一個過時的點下令，還以為是即時的。 -->
                <span v-if="liveStaleTick(selectedUnit) != null" class="stale">
                  · T{{ liveStaleTick(selectedUnit) }} 最後回報（已失聯
                  {{ Math.max(0, currentTick - (liveStaleTick(selectedUnit) ?? 0)) }}t）
                </span>
              </dd>
            </div>
            <div v-if="liveFuel(selectedId) != null" data-testid="unit-fuel">
              <dt>油料</dt>
              <dd :class="{ lowfuel: (liveFuel(selectedId) ?? 0) <= 0 }">
                {{ (liveFuel(selectedId) ?? 0).toFixed(0) }}
                <span v-if="(liveFuel(selectedId) ?? 0) <= 0" class="dim">· 拋錨（需補給）</span>
              </dd>
            </div>
          </dl>
          <div v-if="weapons.length && !showOrbat" class="card-weapons">
            <div class="card-sub">武器裝載</div>
            <ul>
              <li v-for="w in weapons" :key="w.id">
                {{ w.name }}
                <span v-if="w.max_range_m" class="dim">· {{ (w.max_range_m / 1000).toFixed(1) }} km</span>
                <span v-if="liveAmmo(w) != null" class="dim">· 彈 {{ liveAmmo(w) }}</span>
              </li>
            </ul>
          </div>
          <div v-if="selectedEditable" class="card-orbat">
            <button class="orbat-toggle" data-testid="toggle-orbat" @click="showOrbat = !showOrbat">
              {{ showOrbat ? '▾ 編裝編輯' : '▸ 編裝編輯（武器/彈藥）' }}
            </button>
            <UnitOrbatEditor
              v-if="showOrbat"
              :session-id="sessionId"
              :unit-id="selectedId ?? ''"
              :can-edit="true"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cop {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0a1626;
  color: #e2e8f0;
}
.victory-banner {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 1rem;
  background: linear-gradient(90deg, rgba(29, 78, 216, 0.35), rgba(16, 185, 129, 0.25));
  border-bottom: 1px solid #334155;
  color: #f1f5f9;
  font-size: 0.95rem;
}
/* #79 AI 決策狀態列 */
.ai-status-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.4rem 1rem;
  background: rgba(15, 23, 42, 0.7);
  border-bottom: 1px solid #334155;
  font-size: 0.85rem;
  color: #cbd5e1;
}
.ai-status-bar .asb-label {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  color: #93c5fd;
  font-weight: 600;
}
.ai-status-bar .asb-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.15rem 0.6rem;
  border: 1px solid #334155;
  border-radius: 999px;
  background: rgba(30, 41, 59, 0.6);
}
.ai-status-bar .asb-chip.thinking {
  border-color: #2563eb;
  color: #bfdbfe;
}
.ai-status-bar .asb-chip.idle {
  border-color: #475569;
}
.ai-status-bar .asb-chip.offline {
  opacity: 0.55;
}
.ai-status-bar .asb-fac {
  color: #f1f5f9;
}
.ai-status-bar .asb-off {
  color: #94a3b8;
}
.mapedit-bar {
  /* 置中浮動藥丸：避免被左右浮動工具視窗（z 15+）遮住兩端與「開始兵推」鈕。 */
  position: fixed;
  top: 64px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 50;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  max-width: min(760px, 94vw);
  padding: 0.45rem 0.55rem 0.45rem 0.9rem;
  background: rgba(69, 51, 8, 0.97);
  border: 1px solid rgba(251, 191, 36, 0.6);
  border-radius: 0.55rem;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
  color: #fde68a;
  font-size: 0.85rem;
}
.mapedit-bar .meb-txt {
  flex: 1 1 auto;
}
.mapedit-bar .meb-badge {
  flex: 0 0 auto;
  background: #0e7490;
  color: #cffafe;
  border: 1px solid #22d3ee;
  border-radius: 999px;
  padding: 0.1rem 0.55rem;
  font-size: 0.78rem;
  font-weight: 700;
  white-space: nowrap;
}
.mapedit-bar .meb-start {
  flex: 0 0 auto;
  background: #16a34a;
  border: none;
  color: #fff;
  border-radius: 0.35rem;
  padding: 0.4rem 0.95rem;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 600;
  white-space: nowrap;
}
.mapedit-bar .meb-start:hover {
  background: #15803d;
}
.equip-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
}
.equip-modal {
  width: min(760px, 94vw);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 0.5rem;
  color: #e2e8f0;
  overflow: hidden;
}
.equip-modal .eq-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.7rem 0.9rem;
  border-bottom: 1px solid #1e293b;
}
.equip-modal .eq-hd h3 {
  margin: 0;
  font-size: 1rem;
}
.equip-modal .eq-x {
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  font-size: 1rem;
}
.eq-perms {
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid #1e293b;
  background: #0a1626;
}
.eq-perms-hd {
  font-size: 0.75rem;
  color: #94a3b8;
  margin-bottom: 0.35rem;
}
.eq-perms-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.9rem;
}
.eq-perm {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.82rem;
  cursor: pointer;
}
.eq-perm .u-dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  display: inline-block;
}
.eq-body {
  display: flex;
  min-height: 0;
  flex: 1 1 auto;
}
.eq-units {
  flex: 0 0 40%;
  max-width: 16rem;
  overflow-y: auto;
  padding: 0.6rem;
  border-right: 1px solid #1e293b;
}
.eq-fac-hd {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.8rem;
  color: #cbd5e1;
  margin: 0.4rem 0 0.2rem;
}
.eq-fac-hd .u-dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  display: inline-block;
}
.eq-unit {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.3rem 0.5rem;
  margin: 0.15rem 0;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
  font-size: 0.82rem;
}
.eq-unit.sel {
  border-color: #2563eb;
  background: #172554;
}
.eq-editor {
  flex: 1 1 auto;
  min-width: 0;
  overflow-y: auto;
  padding: 0.7rem 0.9rem;
}
.eq-editor-hd {
  font-size: 0.85rem;
  color: #cbd5e1;
  margin-bottom: 0.5rem;
}
.eq-hint {
  color: #64748b;
  font-size: 0.82rem;
}
.victory-banner .vb-aar {
  margin-left: auto;
  background: #1d4ed8;
  border: none;
  color: #fff;
  border-radius: 0.3rem;
  padding: 0.3rem 0.7rem;
  cursor: pointer;
  font-size: 0.82rem;
}
.cop-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: #0f172a;
  border-bottom: 1px solid #1e293b;
  position: relative;
  z-index: 1000; /* 頂列 + 工具選單永遠壓過浮動視窗 */
}
/* #12 工具視窗開關選單 */
.widget-menu {
  position: relative;
}
.wm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
}
.wm-pop {
  position: absolute;
  top: 110%;
  left: 0;
  z-index: 1001;
  min-width: 9rem;
  padding: 0.35rem;
  background: #0f1b2e;
  border: 1px solid #24344a;
  border-radius: 0.4rem;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.wm-hd {
  font-size: 0.68rem;
  color: #64748b;
  padding: 0.1rem 0.3rem 0.25rem;
}
.wm-pop label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.25rem 0.35rem;
  border-radius: 0.25rem;
  font-size: 0.78rem;
  color: #e2e8f0;
  cursor: pointer;
}
.wm-pop label:hover {
  background: #1e293b;
}
.wm-pop label.off {
  color: #64748b;
}
/* 段落小標（取代舊 sec-hd，浮動視窗內用） */
.wsec-hd {
  font-size: 0.78rem;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 0.4rem;
}
/* #12 停靠側欄：拖到最左/右緣的視窗排成側欄（Photoshop 式）。空欄以 :empty 隱藏。 */
.dock-col {
  position: fixed;
  top: 52px;
  bottom: 8px;
  width: 312px;
  z-index: 40;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.4rem;
  overflow-y: auto;
  overflow-x: hidden;
}
.dock-col.left {
  left: 0;
}
.dock-col.right {
  right: 0;
}
.dock-col:empty {
  display: none;
}
/* 浮動視窗內：解除子面板原本的絕對定位，改為填滿視窗本體 */
:deep(.fw .toggles),
:deep(.fw .map-editor),
:deep(.fw .coord-readout) {
  position: static;
  inset: auto;
  transform: none;
  width: auto;
  max-width: none;
  min-width: 0;
  z-index: auto;
  box-shadow: none;
  border: 0;
  background: transparent;
  padding: 0;
  margin: 0;
}
.cop-bar button {
  padding: 0.25rem 0.75rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
}
.sid,
.count {
  font-size: 0.875rem;
  color: #94a3b8;
}
/* WP-C5 位置凍結註記：與 .dim 同層級，但用琥珀色點出「這不是即時值」。 */
.stale {
  color: #fbbf24;
  font-size: 0.8125rem;
}
/* WP-C5 敵情粗化告示：琥珀色（警示但非錯誤——是戰場現實，不是系統故障）。 */
.posture {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.1rem 0.5rem;
  border: 1px solid #b45309;
  border-radius: 0.25rem;
  font-size: 0.8125rem;
  color: #fbbf24;
  cursor: help;
}
.cop-nav {
  margin-left: auto;
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.cop-nav button {
  padding: 0.25rem 0.75rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
}
/* 視角切換（#90）：套了陣營視角時整顆變琥珀，提醒「你現在不是全知」。 */
.vp {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.15rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  color: #94a3b8;
}
.vp select {
  background: transparent;
  border: 0;
  color: #e2e8f0;
  font-size: 0.85rem;
  cursor: pointer;
}
.vp select option {
  background: #0f172a;
}
.vp-on {
  border-color: #f59e0b;
  color: #f59e0b;
}
.vp-on select {
  color: #f59e0b;
  font-weight: 600;
}
.cop-nav button:hover {
  border-color: #2563eb;
}
/* 只留 icon 的導覽鈕：正方形、字級放大到讀得出圖形 */
.cop-nav .icon-btn {
  position: relative; /* hover 提示以此為定位基準 */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.1rem;
  height: 2.1rem;
  padding: 0;
  font-size: 1rem;
  line-height: 1;
}
/* hover 提示：名稱（data-tip）+ 選填的補充說明（data-tip2）。
   自繪而非原生 title——原生的約 1 秒才出現，icon-only 之下等於沒有提示。 */
.cop-nav .icon-btn::after {
  content: attr(data-tip);
  position: absolute;
  top: calc(100% + 0.45rem);
  /* 靠按鈕右緣、往左長：導覽列本身靠右，置中對齊會讓最右邊幾顆的提示被視窗切掉。 */
  right: 0;
  z-index: 1002; /* 壓過工具選單彈出層（1001） */
  max-width: 15rem;
  width: max-content;
  padding: 0.3rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.3rem;
  background: #0b1324;
  color: #e2e8f0;
  font-size: 0.75rem;
  line-height: 1.35;
  text-align: left;
  white-space: pre-line; /* 名稱與補充說明以 \A 分行（見下方 [data-tip2] 規則） */
  box-shadow: 0 6px 16px rgb(0 0 0 / 45%);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s ease;
}
/* 有補充說明者：名稱一行、說明一行 */
.cop-nav .icon-btn[data-tip2]::after {
  content: attr(data-tip) '\A' attr(data-tip2);
}
.cop-nav .icon-btn:hover::after,
.cop-nav .icon-btn:focus-visible::after {
  opacity: 1;
}
.cop-nav button.on {
  border-color: #eab308;
  color: #fde68a;
}
.cop-nav .help {
  font-size: 0.8125rem;
  color: #60a5fa;
  text-decoration: none;
}
.cop-nav .help:hover {
  text-decoration: underline;
}
.map-notice {
  position: absolute;
  left: calc(1rem + var(--ldock, 0));
  bottom: 1rem;
  z-index: 10;
  max-width: 22rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.625rem 0.875rem;
  border-radius: 0.5rem;
  border: 1px solid #1e3a5f;
  background: rgba(15, 23, 42, 0.9);
  font-size: 0.75rem;
  color: #94a3b8;
  line-height: 1.5;
}
.map-notice strong {
  color: #e2e8f0;
}
.map-notice code {
  color: #7dd3fc;
  font-size: 0.7rem;
}
/* 線寬設定觸發鈕（#5）——地圖右下角浮動（停靠側欄存在時右移讓位）。 */
.linewidth-btn {
  position: absolute;
  right: calc(1rem + var(--rdock, 0));
  bottom: 3.4rem;
  z-index: 11;
  padding: 0.3rem 0.55rem;
  border: 1px solid #334155;
  border-radius: 0.35rem;
  background: rgba(15, 23, 42, 0.9);
  color: #cbd5e1;
  font-size: 0.72rem;
  cursor: pointer;
}
.linewidth-btn:hover {
  border-color: #2563eb;
  color: #e2e8f0;
}
/* 右鍵選單（#3）——ATAK 式移動/攻擊。 */
.ctx-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1090; /* 壓過停靠側欄(40)/Unit 卡(45)/浮動視窗，避免右鍵選單被遮住（#3） */
}
.ctx-menu {
  position: absolute;
  z-index: 1091;
  min-width: 8rem;
  transform: translate(2px, 2px);
  display: flex;
  flex-direction: column;
  padding: 0.25rem;
  border: 1px solid #334155;
  border-radius: 0.4rem;
  background: rgba(15, 23, 42, 0.97);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
}
.ctx-title {
  padding: 0.25rem 0.5rem 0.35rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: #7dd3fc;
  border-bottom: 1px solid #1e293b;
  margin-bottom: 0.2rem;
}
.ctx-menu button {
  text-align: left;
  padding: 0.4rem 0.55rem;
  border: 0;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  font-size: 0.8rem;
  cursor: pointer;
}
.ctx-menu button.ctx-danger {
  color: #fca5a5;
}
.ctx-menu button:hover {
  background: #1d4ed8;
}
.ctx-empty {
  padding: 0.4rem 0.55rem;
  font-size: 0.75rem;
  color: #94a3b8;
}
/* 通用 modal（#5 線寬 / 其他 COP 設定）。 */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
}
.modal {
  width: 22rem;
  max-width: 90vw;
  padding: 1.25rem;
  border-radius: 0.5rem;
  border: 1px solid #334155;
  background: #0f172a;
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}
.modal h3 {
  margin: 0;
  font-size: 1rem;
}
.modal label {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.8rem;
  color: #94a3b8;
}
.modal label b {
  color: #7dd3fc;
  font-weight: 600;
}
.modal input[type='range'] {
  width: 100%;
}
.modal-hint {
  margin: 0;
  font-size: 0.72rem;
  color: #64748b;
}
.modal-btns {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.modal-btns button {
  padding: 0.4rem 0.9rem;
  border: 0;
  border-radius: 0.3rem;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
}
.modal-btns .ghost {
  background: transparent;
  border: 1px solid #334155;
  color: #e2e8f0;
}
.body {
  display: flex;
  flex: 1;
  min-height: 0;
}
/* 下令面板小標（浮動視窗內） */
.order h3 {
  margin: 0.75rem 0 0.375rem;
  font-size: 0.8125rem;
  color: #94a3b8;
}
.units,
.orders,
.events {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.events li {
  padding: 0.25rem 0.5rem;
  border-left: 2px solid #f59e0b;
  background: #1c1917;
  font-size: 0.75rem;
}
.ws {
  color: #64748b;
  font-weight: normal;
}
.units li,
.orders li {
  padding: 0.375rem 0.5rem;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
  cursor: pointer;
}
/* 單位小工具依陣營分組（可收合/展開） */
.ufac {
  margin-bottom: 0.35rem;
}
.ufac-hd {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  width: 100%;
  padding: 0.25rem 0.4rem;
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 0.3rem;
  color: #cbd5e1;
  font-size: 0.8rem;
  cursor: pointer;
}
.ufac-hd:hover {
  border-color: #334155;
}
.ufac-hd .pi {
  font-size: 0.7rem;
  color: #64748b;
}
.ufac-count {
  color: #64748b;
  font-size: 0.75rem;
}
/* 陣營戰力（量體加權）——靠右對齊，與各單位的效能%同一套色帶。 */
.ufac-pow {
  margin-left: auto;
  font-size: 0.78rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.ufac-ko {
  color: #ef4444;
  font-size: 0.7rem;
}
.ufac-units {
  list-style: none;
  margin: 0.2rem 0 0;
  padding: 0 0 0 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
/* #27 指令列：對象 + 時間 + 狀態。 */
.orders li {
  cursor: default;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.ord-main {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.ord-unit {
  font-weight: 600;
  color: #e2e8f0;
}
.ord-type {
  color: #93c5fd;
  font-size: 0.72rem;
}
.ord-tgt {
  color: #fca5a5;
  font-size: 0.72rem;
}
.ord-meta {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.7rem;
  color: #94a3b8;
}
.ord-time {
  font-variant-numeric: tabular-nums;
}
.ord-status {
  padding: 0 0.3rem;
  border-radius: 0.2rem;
  background: #1e293b;
}
.ord-status.st-COMPLETED {
  color: #86efac;
}
.ord-status.st-REJECTED,
.ord-status.st-CANCELLED {
  color: #fca5a5;
}
.ord-status.st-EXECUTING {
  color: #fcd34d;
}
.ord-meta button {
  margin-left: auto;
  padding: 0.1rem 0.4rem;
  font-size: 0.68rem;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  background: transparent;
  color: #cbd5e1;
  cursor: pointer;
}
.units li.sel {
  border-color: #2563eb;
  background: #172554;
}
.ufac-hd .u-dot {
  display: inline-block;
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  flex: none;
}
.units li .u-hp {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.units li .u-ko {
  margin-left: 0.35rem;
  color: #ef4444;
  font-size: 0.72rem;
  font-weight: 700;
}
.units li .u-fixed {
  margin-left: 0.3rem;
  font-size: 0.78rem;
}
.units li.out-scope {
  opacity: 0.45;
  cursor: not-allowed;
}
.units li.out-scope:hover {
  border-color: #1e293b;
}
.units li .u-ban {
  margin-left: 0.3rem;
  font-size: 0.72rem;
}
.order .fixed-note {
  margin: 0.35rem 0;
  padding: 0.35rem 0.5rem;
  background: rgba(251, 191, 36, 0.12);
  border: 1px solid rgba(251, 191, 36, 0.4);
  border-radius: 0.3rem;
  color: #fde68a;
  font-size: 0.74rem;
  line-height: 1.4;
}
.empty {
  color: #64748b;
  cursor: default !important;
}
.order {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  margin: 0.5rem 0;
}
.order select,
.order button {
  padding: 0.375rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  cursor: pointer;
}
.order button.armed {
  border-color: #eab308;
}
.dest {
  font-family: monospace;
  color: #94a3b8;
}
.dest .snaphint {
  font-family: system-ui, sans-serif;
  color: #eab308;
  font-size: 0.68rem;
}
.dest .snaphint.precise {
  color: #f472b6;
}
.order .precise {
  display: flex;
  gap: 0.35rem;
  align-items: center;
  color: #94a3b8;
  font-size: 0.72rem;
  cursor: pointer;
}
/* #28 移動路徑：按鈕列 + 成本試算 */
.order .movebtns {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.order .movebtns button {
  font-size: 0.72rem;
  padding: 0.3rem 0.45rem;
}
.mvprev {
  margin-top: 0.3rem;
  padding: 0.4rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.3rem;
  background: #0b1220;
  font-size: 0.74rem;
}
.mvprev .mv-row {
  display: flex;
  gap: 0.75rem;
  color: #cbd5e1;
}
.mvprev .mv-row b {
  color: #38bdf8;
}
.unit-card .lowfuel {
  color: #f87171;
}
.mvprev .mv-sub {
  color: #94a3b8;
  font-size: 0.78rem;
}
.mvprev .mv-routed {
  color: #7dd3fc;
}
.mvprev .mv-lowfuel {
  color: #fbbf24;
}
.mvprev .mv-ok {
  margin-top: 0.25rem;
  color: #4ade80;
}
.mvprev .mv-forced {
  margin-top: 0.25rem;
  color: #f59e0b;
}
.mvprev .mv-forced ul {
  margin: 0.2rem 0 0;
  padding-left: 1.1rem;
  color: #fbbf24;
}
.order .selunit {
  color: #60a5fa;
  font-weight: 600;
}
.order .hint {
  color: #94a3b8;
  font-size: 0.72rem;
  line-height: 1.4;
}
/* 聯合火力武器組合清單（P4）：顯示將一起開火的武器。 */
.weapon-mix {
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0.35rem 0.5rem;
  background: rgba(30, 58, 95, 0.25);
  border: 1px solid #1e3a5f;
  border-radius: 0.35rem;
  font-size: 0.72rem;
  color: #cbd5e1;
}
.weapon-mix li {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.1rem 0;
}
.weapon-mix .dim {
  color: #94a3b8;
}
.precheck .ok {
  color: #4ade80;
}
.precheck .bad {
  color: #f87171;
}
.precheck ul {
  margin: 0.25rem 0 0;
  padding-left: 1rem;
}
.orders button {
  margin-left: 0.5rem;
  padding: 0.0625rem 0.375rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #f87171;
  cursor: pointer;
}
.map-wrap {
  position: relative;
  flex: 1;
}
/* 停靠側欄存在時，地圖控制項/比例尺/線寬鈕自動讓位到未被遮蔽處（--ldock / --rdock）。 */
.map-wrap :deep(.maplibregl-ctrl-top-left),
.map-wrap :deep(.maplibregl-ctrl-bottom-left) {
  margin-left: var(--ldock, 0);
  transition: margin 0.12s ease;
}
.map-wrap :deep(.maplibregl-ctrl-top-right),
.map-wrap :deep(.maplibregl-ctrl-bottom-right) {
  margin-right: var(--rdock, 0);
  transition: margin 0.12s ease;
}
/* 座標查詢讀值（#10）——浮在地圖上緣中央。 */
.coord-readout {
  position: absolute;
  top: 1rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 11;
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid #6b2a52;
  background: rgba(15, 23, 42, 0.95);
  color: #e2e8f0;
  font-size: 0.78rem;
  min-width: 12rem;
}
.coord-readout .cr-hd {
  color: #f472b6;
  font-size: 0.72rem;
  margin-bottom: 0.3rem;
}
.coord-readout .cr-row {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
}
.coord-readout .cr-row span {
  color: #94a3b8;
}
.coord-readout code {
  font-family: ui-monospace, monospace;
  color: #e2e8f0;
}
.coord-readout .cr-hint {
  color: #64748b;
}


/* 單位詳細資訊圖卡（#5）——浮在地圖左下。 */
/* Unit 資訊卡：懸浮於選取圖標旁（#Fix C；定位由 inline unitCardStyle 提供 fixed left/top）。 */
.unit-card {
  position: fixed;
  z-index: 45;
  width: 19rem;
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  padding: 0.75rem 0.875rem;
  border-radius: 0.5rem;
  border: 1px solid #1e3a5f;
  background: rgba(15, 23, 42, 0.96);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  font-size: 0.78rem;
  color: #e2e8f0;
}
/* 錨定時附一條指向圖標的小尾巴（左側）。 */
.unit-card.card-anchored::before {
  content: '';
  position: absolute;
  left: -6px;
  top: 14px;
  width: 10px;
  height: 10px;
  background: rgba(15, 23, 42, 0.96);
  border-left: 1px solid #1e3a5f;
  border-bottom: 1px solid #1e3a5f;
  transform: rotate(45deg);
}
.unit-card .card-close {
  position: absolute;
  top: 0.375rem;
  right: 0.375rem;
  padding: 0 0.3rem;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 0.9rem;
}
.unit-card .card-close:hover {
  color: #e2e8f0;
}
.unit-card .card-hd {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding-right: 1rem;
  cursor: move;
  user-select: none;
  touch-action: none;
}
.unit-card .card-grip {
  color: #475569;
  font-size: 0.7rem;
  flex: none;
  margin-right: -0.1rem;
}
/* 拖曳後脫離錨定 → 不顯示指向圖標的小尾巴。 */
.unit-card.card-dragged::before {
  display: none;
}
.unit-card .fdot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
  flex: none;
}
.unit-card .cname {
  color: #f8fafc;
}
.unit-card .clevel {
  color: #94a3b8;
  font-size: 0.68rem;
}
.unit-card .hpbar {
  position: relative;
  height: 1.05rem;
  margin: 0.5rem 0 0.4rem;
  border-radius: 0.25rem;
  background: #1e293b;
  overflow: hidden;
}
.unit-card .hpfill {
  height: 100%;
  transition: width 0.3s ease;
}
.unit-card .hptxt {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.66rem;
  font-weight: 600;
  color: #0a1626;
  text-shadow: 0 0 2px rgba(255, 255, 255, 0.4);
}
.unit-card .card-meta {
  margin: 0.25rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.unit-card .card-meta > div {
  display: flex;
  gap: 0.5rem;
}
.unit-card .card-meta dt {
  color: #64748b;
  min-width: 2.5rem;
}
.unit-card .card-meta dd {
  margin: 0;
  color: #cbd5e1;
  font-family: monospace;
}
/* #33 通聯狀態色：上線綠 / 降級黃 / 離線紅 */
.unit-card .card-meta dd.comms-online {
  color: #4ade80;
}
.unit-card .card-meta dd.comms-degraded {
  color: #facc15;
}
.unit-card .card-meta dd.comms-offline {
  color: #f87171;
}
.unit-card .card-sub {
  margin: 0.5rem 0 0.2rem;
  color: #64748b;
  font-size: 0.68rem;
}
.unit-card .card-weapons ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.unit-card .card-weapons .dim {
  color: #94a3b8;
  font-size: 0.7rem;
}
.unit-card .card-orbat {
  margin-top: 0.5rem;
  border-top: 1px solid #1e293b;
  padding-top: 0.4rem;
}
.unit-card .orbat-toggle {
  border: none;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
  font-size: 0.72rem;
  padding: 0 0 0.3rem;
}
.unit-card .orbat-toggle:hover {
  color: #bae6fd;
}
.map-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
}
</style>
