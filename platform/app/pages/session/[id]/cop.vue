<script setup lang="ts">
import type { Contact, OwnUnit, Relation } from '~/composables/useUnits'
import { commsLabel, factionColor, healthColor, unitLevelLabel } from '~/composables/useUnits'
import type { UnitView, WeaponView, OrderResponse } from '~/composables/useOrders'
import type { components } from '~/types/api'
import type { StateSnapshot } from '~/stores/sessionStream'
import type { ApiError } from '~/composables/useApi'
import type { ContactView } from '~/composables/useIntel'
import { toContact } from '~/composables/useIntel'
import { apiFetch } from '~/composables/useApi'
import { buildBasemapSources } from '~/composables/useMapStyle'
import {
  cancelOrder,
  fetchOrders,
  fetchUnits,
  fetchWeapons,
  orderStatusLabel,
  orderTypeLabel,
  submitOrder,
} from '~/composables/useOrders'
import {
  fetchEquipmentTemplates,
  fetchOrbatPermissions,
  setOrbatPermissions,
  type EquipmentTemplate,
} from '~/composables/useEquipment'
import { forward as mgrsForward } from 'mgrs'
import { latLngToCell } from 'h3-js'
import {
  FEATURE_KINDS,
  createMapFeature,
  deleteMapFeature,
  draftToFc,
  editMapFeature,
  featureDisplayColor,
  featureSymbolFc,
  featureZoneClass,
  ZONE_CLASSES,
  featuresToFc,
  fetchMapFeatures,
  fetchMovementPreview,
  fetchTerrainFootprint,
  influenceToFc,
  MIN_VERTICES,
  type MovementPreview,
  openRing,
  removeVertex,
  rotatePoints,
  shapeToPolygon,
  type DraftKind,
  type FeatureCreate,
  type MapFeature,
} from '~/composables/useMapFeatures'
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

const hex = ref(false)
const hillshade = ref(false)
const contour = ref(false)
// 圖層透明度乘數 + 套疊順序（#9）+ 主/次等高線間距（#8）——localStorage 持久化。
const layerOpacity = ref<Record<string, number>>({ basemap: 1, hillshade: 1, contour: 1, hex: 1 })
const layerOrder = ref<string[]>(['hex', 'contour', 'hillshade'])
const contourMajor = ref(100)
const contourMinor = ref(50)
// 座標網格（#9）+ 座標查詢（#10）
const latlngGrid = ref(false)
const mgrsGrid = ref(false)
const gridStepDeg = ref(0.5)
const queryPoint = ref<{ lng: number; lat: number } | null>(null)
const queryMgrs = ref('')
const hexMaxRes = ref(8) // 六角網格最細解析度上限（設定最小網格）
const hexLimitKm = ref(0) // 交戰範圍限制（km；0=不限）
const dayNight = ref(false) // 日照視覺（#6）
const timeOfDay = ref(12) // 一日時間 0–24（#6）
// 線條粗細設定（#5）：六角網格線 + 主/次等高線線寬（px）——專屬 modal 調整、localStorage 持久化。
const hexLineWidth = ref(0.5)
const contourMajorWidth = ref(1.2)
const contourMinorWidth = ref(0.5)
// #22 線條顏色 + 座標網格線寬（併入圖層小工具，取代舊 modal）。
const hexLineColor = ref('#38bdf8')
const contourColor = ref('#c9a15c')
const gridColor = ref('#5b7fa6')
const gridWidth = ref(0.5)
const mgrsColor = ref('#facc15')
// #12 浮動工具視窗：六個小工具皆可拖拉/縮放/關閉，並以工具選單勾選開關；幾何+開關持久化。
// 取代舊的左右固定面板/換邊。coordQuery/mapEditorOpen 改為對應 widget 的開關別名。
type WidgetId = 'layers' | 'units' | 'events' | 'orders' | 'mapedit' | 'coords'
type DockSide = 'left' | 'right' | 'float'
interface WStat {
  open: boolean
  dock: DockSide
  x: number
  y: number
  w: number
  h: number
  z: number
}
const WIDGET_DEFS: { id: WidgetId; title: string; label: string }[] = [
  { id: 'layers', title: '圖層 / 底圖', label: '圖層' },
  { id: 'units', title: '單位 / 下令', label: '單位' },
  { id: 'events', title: '戰況事件', label: '戰況事件' },
  { id: 'orders', title: '指令', label: '指令' },
  { id: 'mapedit', title: '地圖編輯', label: '地圖編輯' },
  { id: 'coords', title: '座標查詢', label: '座標' },
]
const DOCK_EDGE = 72 // 拖到最左/右 DOCK_EDGE px 內即停靠成側欄
function defaultWidgets(): Record<WidgetId, WStat> {
  const vw = import.meta.client ? window.innerWidth : 1280
  const rx = Math.max(324, vw - 320)
  return {
    layers: { open: true, dock: 'left', x: 12, y: 60, w: 296, h: 470, z: 11 },
    units: { open: true, dock: 'right', x: rx, y: 60, w: 300, h: 300, z: 12 },
    events: { open: true, dock: 'right', x: rx, y: 372, w: 300, h: 148, z: 13 },
    orders: { open: true, dock: 'right', x: rx, y: 532, w: 300, h: 180, z: 14 },
    mapedit: { open: false, dock: 'float', x: 12, y: 60, w: 326, h: 540, z: 15 },
    coords: { open: false, dock: 'float', x: 12, y: 540, w: 260, h: 170, z: 16 },
  }
}
const widgets = ref<Record<WidgetId, WStat>>(defaultWidgets())
const widgetZTop = ref(20)
const widgetMenuOpen = ref(false)
const DOCK_W = 320 // 停靠側欄寬（含邊距）——供地圖控制項讓位
const hasLeftDock = computed(() =>
  WIDGET_DEFS.some((d) => widgets.value[d.id].open && widgets.value[d.id].dock === 'left'),
)
const hasRightDock = computed(() =>
  WIDGET_DEFS.some((d) => widgets.value[d.id].open && widgets.value[d.id].dock === 'right'),
)
function focusWidget(id: WidgetId) {
  widgetZTop.value += 1
  widgets.value[id].z = widgetZTop.value
}
function toggleWidget(id: WidgetId) {
  const w = widgets.value[id]
  w.open = !w.open
  if (w.open) focusWidget(id)
}
function setWidgetGeom(id: WidgetId, g: { x: number; y: number; w: number; h: number }) {
  Object.assign(widgets.value[id], g)
}
// 拖曳起手：先脫離停靠變浮動，落在目前螢幕位置跟著游標走。
function onWidgetGrab(id: WidgetId, g: { x: number; y: number; w: number; h: number }) {
  const w = widgets.value[id]
  w.dock = 'float'
  w.x = g.x
  w.y = g.y
  w.h = g.h
  focusWidget(id)
}
// 拖曳落下：靠最左/右緣 → 停靠成側欄；否則維持浮動於落點。
function onWidgetDrop(id: WidgetId, g: { x: number; y: number; w: number; h: number }) {
  const w = widgets.value[id]
  const vw = window.innerWidth
  if (g.x <= DOCK_EDGE) w.dock = 'left'
  else if (g.x + g.w >= vw - DOCK_EDGE) w.dock = 'right'
  else {
    w.dock = 'float'
    w.x = g.x
    w.y = g.y
  }
}
const coordQuery = computed({
  get: () => widgets.value.coords.open,
  set: (v: boolean) => {
    widgets.value.coords.open = v
  },
})
const hiddenFeatureIds = ref<string[]>([]) // session-local：隱藏的地圖元素
function toggleFeatureHidden(id: string) {
  const i = hiddenFeatureIds.value.indexOf(id)
  if (i >= 0) hiddenFeatureIds.value.splice(i, 1)
  else hiddenFeatureIds.value.push(id)
}
const LAYER_PREFS_KEY = 'matso.cop.layers'

// 底圖來源（可抽換，#2）：離線 / 街道 / 衛星 / 軍用…由 runtimeConfig 注入。
const _pub = useRuntimeConfig().public
const basemapSources = buildBasemapSources({
  tileUrl: _pub.tileUrl as string,
  satelliteUrl: _pub.satelliteUrl as string | undefined,
  basemaps: _pub.basemaps as never,
  onlineBasemaps: _pub.onlineBasemaps as boolean,
})
// 預設用「街道」（有本地 tileserver 時）；載不到才回退離線格線。
const basemap = ref(basemapSources.some((s) => s.id === 'street') ? 'street' : 'offline')
function onBasemapError() {
  if (basemap.value !== 'offline') basemap.value = 'offline'
}

// 是否已設定離線 tile server（有 .mbtiles）。未設 → 顯示離線底圖提示（SPEC §13.2）。
const hasTiles = computed(() => !!_pub.tileUrl)

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

// 地圖編輯器（stage ③b）——標註/工事/武器據點的繪製與管理。
const mapFeatures = ref<MapFeature[]>([])
const mapEditorOpen = computed({
  get: () => widgets.value.mapedit.open,
  set: (v: boolean) => {
    widgets.value.mapedit.open = v
  },
})
const drawKind = ref<DraftKind | null>(null)
const drawFeatureKind = ref('OBSTACLE')
const drawWeaponTemplate = ref('')
const draftCoords = ref<number[][]>([])
// 繪製屬性（#11）：名稱/顏色/備註/高度（障礙/建築預設 2m）。
const drawLabel = ref('')
const drawColor = ref('')
const drawNotes = ref('')
const drawHeight = ref<number | null>(null)
const drawSidc = ref('') // 北約符號（點特徵，#11）
const selectedFeatureId = ref<string | null>(null)
// 選取特徵的編輯欄位（#11）
const editFeatLabel = ref('')
const editFeatColor = ref('')
const editFeatOwner = ref('') // #92 歸屬陣營（僅全知可改；'' = 不變更）
// WP-A3 禁射級別（僅全知可設；只對面有意義——點/線不成區，後端只認 POLYGON）。
const editFeatZone = ref('')
const drawWidth = ref(DEFAULT_FEATURE_WIDTH) // #96 繪製線寬
const editFeatWidth = ref(DEFAULT_FEATURE_WIDTH) // #96 編輯線寬
const editFeatNotes = ref('')
const editFeatHeight = ref<number | null>(null)
const editFeatSidc = ref('')
// 武器射向/雷達扇區（#11 Chunk C）：射程(m) + 方向(度) + 張角(度，360=全向)。
const editFeatRange = ref<number | null>(null)
const editFeatDir = ref(0)
const editFeatArc = ref(360)
// 選取當下的射程/方向/張角（供儲存時判斷是否真的改動 → 才失效地形裁切環）。
const origRange = ref<number | null>(null)
const origDir = ref(0)
const origArc = ref(360)
// 地形裁切（#11）：feature id → viewshed 環（後端逐方位 LOS）；套用中旗標。
const terrainClips = ref<Record<string, number[][]>>({})
const clipBusy = ref(false)
const weaponTemplates = ref<EquipmentTemplate[]>([])
const orders = ref<OrderResponse[]>([])
// 指令列表：以下令 tick（≈真實時間）新到舊排序，剛下的令排最上（穩定排序，同 tick 保原序）。
const sortedOrders = computed(() =>
  [...orders.value].sort((a, b) => (b.issued_at_tick ?? 0) - (a.issued_at_tick ?? 0)),
)
const selectedId = ref<string | null>(null)
const orderType = ref<'MOVE' | 'ENGAGE'>('MOVE')
const destH3 = ref<string | null>(null)
const destLatLng = ref<{ lng: number; lat: number } | null>(null) // 精確移動落點（#2）
// 精確移動預設「開」：跳過六角格心吸附，單位精確走到點擊處。六角格心吸附在 <1km 近距作戰
// （校園/大樓）會把落點吸回格心（≈原位）造成「下令後跑回原位」的錯覺（#2/#15）；預設關閉吸附
// 消除此問題。需大範圍推演的粗略化/省算時，可取消勾選改回六角吸附。
const preciseMove = ref(true)
const targeting = ref(false)
// #28 移動路徑預覽：目的地/自訂路徑 → 試算距離/tick/油耗/可行性/強穿阻礙。
const movePreview = ref<MovementPreview | null>(null)
const moveWaypoints = ref<number[][]>([]) // 自訂路徑（[lng,lat]，不含起點）
const waypointMode = ref(false) // 逐點點擊建自訂路徑
let previewTimer: ReturnType<typeof setTimeout> | null = null
const targetUnitId = ref<string | null>(null)
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
// 聯合兵種火力政策（SPEC_EXTEND P4）：未選單一武器時＝以武器組合聯合開火，政策精修。
type FirePolicy = components['schemas']['FirePolicy']
const firePolicy = ref<FirePolicy>('FREE')
const FIRE_POLICY_OPTS: { value: FirePolicy; label: string }[] = [
  { value: 'FREE', label: '自由開火（全武器）' },
  { value: 'SMALL_ARMS_ONLY', label: '僅輕兵器（節約重火力）' },
  { value: 'ANTI_ARMOR_HOLD', label: '反裝甲留給裝甲目標' },
]
// 聯合火力模式＝未指定單一武器（≥2 武器才有意義）；指定武器＝單武器射擊。
const combinedMode = computed(() => weaponId.value === null && weapons.value.length >= 2)
// 活彈藥（#53）：交戰消耗即時反映——優先讀 STATE_DIFF 串流的 ammo_by_weapon（活模擬扣減），
// 否則回 w.ammo_remaining（GET /weapons 的 DB 值）。w.id＝EquipmentInstance.id＝ammo_by_weapon 鍵。
// #84 活油料：STATE_DIFF 串流的 fuel（移動耗油/補給加油即時反映）。無值＝徒步/無油料模型。
function liveFuel(unitId: string | null): number | null {
  const f = stream.unitPatches[unitId ?? '']?.fuel
  return typeof f === 'number' ? f : null
}
function liveAmmo(w: WeaponView): number | null {
  const abw = stream.unitPatches[selectedId.value ?? '']?.ammo_by_weapon as
    | Record<string, number>
    | undefined
  const live = abw?.[w.id]
  return typeof live === 'number' ? live : (w.ammo_remaining ?? null)
}
const precheck = ref<OrderResponse['precheck'] | null>(null)
const message = ref('')

// 全域通知（下令被拒等，#7）。
const toasts = useToasts()

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

// ---- #95 武器軌跡（**純顯示**）----
//
// 紅線：這只是把已裁決的結果畫出來，**絕不回頭影響裁決**——軌跡由 ENGAGEMENT_RESOLVED
// 事件（後端已裁決完）觸發，前端不做任何命中/可達判定。
//
// 端點座標取自「client 本來就看得到的東西」（我方/友軍單位 + 已偵獲的 contact），
// **刻意不讓後端在事件裡夾帶座標**：交戰事件目前沒有 faction 受眾標籤（見
// state/broadcaster.py），夾帶座標等於把全場每次交戰的精確位置廣播給所有陣營。
// 代價：陣營視角下若看不到某一端（例如未偵獲的射手），該次交戰就不畫——這是正確的迷霧行為。
const TRACK_TTL_MS = 4000
interface WeaponTrack {
  key: string
  from: [number, number]
  to: [number, number]
  status: string
  born: number
}
const weaponTracks = ref<WeaponTrack[]>([])
const trackNow = ref(0) // 由計時器推進，驅動淡出（只在有軌跡時跑）

/** 由 id 找地圖上的座標：我方/友軍單位，或已偵獲的 contact。查無 → null（不畫）。 */
function trackPos(id?: string | null): [number, number] | null {
  if (!id) return null
  const u = ownUnits.value.find((x) => x.id === id)
  if (u) return [u.lng, u.lat]
  const c = contacts.value.find((x) => x.contactId === id)
  return c ? [c.lng, c.lat] : null
}

const weaponTrackFc = computed(() => ({
  type: 'FeatureCollection' as const,
  features: weaponTracks.value.map((t) => {
    const age = Math.max(0, trackNow.value - t.born)
    return {
      type: 'Feature' as const,
      properties: {
        status: t.status,
        // 線性淡出；REJECTED 不畫（下方過濾），HIT 較亮、MISS 較淡以便一眼分辨。
        opacity: Math.max(0, 1 - age / TRACK_TTL_MS) * (t.status === 'HIT' ? 0.95 : 0.5),
      },
      geometry: { type: 'LineString' as const, coordinates: [t.from, t.to] },
    }
  }),
}))

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
// Unit 資訊卡懸浮位置（#Fix C）：MapCanvas 投影選取單位螢幕座標 → 卡片浮於圖標旁。
const unitCardPos = ref<{ x: number; y: number } | null>(null)
function onSelectScreenPos(p: { x: number; y: number } | null) {
  unitCardPos.value = p
}
// 使用者拖曳後的卡片位置（#42）：一旦拖曳即脫離圖標錨定，固定於此螢幕座標。
const unitCardDrag = ref<{ x: number; y: number } | null>(null)
// 選取換單位/取消選取時，讓卡片回到自動錨定（不沿用上一單位的手動位置）。
watch(selectedId, () => {
  unitCardDrag.value = null
})
// #95 交戰事件 → 武器軌跡。只吃新到的事件（events 是累積緩衝，重看舊事件不該重畫）。
let trackCursor = 0
let trackTimer: ReturnType<typeof setInterval> | null = null
watch(
  () => stream.events.length,
  (len) => {
    if (len < trackCursor) trackCursor = 0 // 緩衝被裁切（MAX_EVENTS）→ 重置游標
    for (const env of stream.events.slice(trackCursor)) {
      const p = (env as { payload?: Record<string, unknown> }).payload ?? {}
      if (p.event_type !== 'ENGAGEMENT_RESOLVED') continue
      const status = String(p.status ?? '')
      if (status === 'REJECTED') continue // 根本沒射出去，不畫
      const from = trackPos(p.initiator_id as string | undefined)
      const to = trackPos(p.target_id as string | undefined)
      if (!from || !to) continue // 有一端看不到 → 不畫（迷霧下的正確行為）
      weaponTracks.value.push({
        key: `${p.initiator_id}-${p.target_id}-${env.seq ?? len}`,
        from,
        to,
        status,
        born: Date.now(),
      })
    }
    trackCursor = len
    // 有軌跡才起計時器（推進淡出 + 到期清除）；清空即停，避免閒置空轉。
    if (weaponTracks.value.length && !trackTimer) {
      trackTimer = setInterval(() => {
        trackNow.value = Date.now()
        weaponTracks.value = weaponTracks.value.filter((t) => trackNow.value - t.born < TRACK_TTL_MS)
        if (!weaponTracks.value.length && trackTimer) {
          clearInterval(trackTimer)
          trackTimer = null
        }
      }, 200)
    }
  },
)
// 切換視角（#90）→ 重抓 units/intel（後端依 as_faction 套該陣營迷霧）；清掉屬於前一視角的選取。
watch(viewpoint, async () => {
  selectedId.value = null
  targetUnitId.value = ''
  await refresh()
})
// 卡片實際定位：拖曳過 → 手動座標；否則圖標右上方偏移，並夾在視窗內（卡片約 304×320）。
const unitCardStyle = computed(() => {
  if (unitCardDrag.value) {
    return { left: `${unitCardDrag.value.x}px`, top: `${unitCardDrag.value.y}px` }
  }
  const p = unitCardPos.value
  if (!p) return { display: 'none' }
  const CW = 304 // ≈ 19rem
  const CH = 320
  const vw = import.meta.client ? window.innerWidth : 1280
  const vh = import.meta.client ? window.innerHeight : 800
  let left = p.x + 18 // 圖標右側
  let top = p.y - 10
  if (left + CW > vw - 8) left = p.x - CW - 18 // 右側放不下 → 移到圖標左側
  if (left < 8) left = 8
  top = Math.min(Math.max(56, top), vh - CH - 8)
  return { left: `${left}px`, top: `${top}px` }
})
// 卡片拖曳（#42）：以標頭為把手，滑鼠/觸控皆可，夾在視窗內。
let cardDragging = false
let cardSX = 0
let cardSY = 0
let cardStartX = 0
let cardStartY = 0
function cardPoint(e: MouseEvent | TouchEvent): { x: number; y: number } {
  const t = 'touches' in e ? e.touches[0] : null
  return t
    ? { x: t.clientX, y: t.clientY }
    : { x: (e as MouseEvent).clientX, y: (e as MouseEvent).clientY }
}
function beginCardDrag(e: MouseEvent | TouchEvent) {
  if ((e.target as HTMLElement).closest('.card-close')) return
  const rect = (e.currentTarget as HTMLElement).closest('.unit-card')?.getBoundingClientRect()
  cardStartX = rect ? rect.left : 0
  cardStartY = rect ? rect.top : 0
  const p = cardPoint(e)
  cardSX = p.x
  cardSY = p.y
  unitCardDrag.value = { x: cardStartX, y: cardStartY }
  cardDragging = true
  window.addEventListener('mousemove', onCardDrag)
  window.addEventListener('mouseup', endCardDrag)
  window.addEventListener('touchmove', onCardDrag, { passive: false })
  window.addEventListener('touchend', endCardDrag)
}
function onCardDrag(e: MouseEvent | TouchEvent) {
  if (!cardDragging) return
  if ('touches' in e) e.preventDefault()
  const p = cardPoint(e)
  const maxX = window.innerWidth - 80
  const maxY = window.innerHeight - 40
  unitCardDrag.value = {
    x: Math.min(Math.max(0, cardStartX + p.x - cardSX), maxX),
    y: Math.min(Math.max(52, cardStartY + p.y - cardSY), maxY),
  }
}
function endCardDrag() {
  cardDragging = false
  window.removeEventListener('mousemove', onCardDrag)
  window.removeEventListener('mouseup', endCardDrag)
  window.removeEventListener('touchmove', onCardDrag)
  window.removeEventListener('touchend', endCardDrag)
}
onBeforeUnmount(endCardDrag)
function clearSelection() {
  selectedId.value = null
  unitCardPos.value = null
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
  showOrbat.value = false
}

async function selectUnit(id: string) {
  clearSelection()
  selectedId.value = id
  // 抓此單位可用武器（ENGAGE 選武器/彈種）；失敗（他方/無裝備）→ 空清單，下拉隱藏。
  weapons.value = await fetchWeapons(sessionId.value, id).catch(() => [])
}

function onMapClick(e: { lng: number; lat: number; h3: string }) {
  // 繪圖中：點擊＝加頂點。POINT 一點完成；CIRCLE/RECTANGLE 兩點完成（#11）；線/面累積至「完成」。
  if (drawActive.value) {
    if (drawKind.value === 'POINT') {
      draftCoords.value = [[e.lng, e.lat]]
      finishDraw()
    } else if (drawKind.value === 'CIRCLE' || drawKind.value === 'RECTANGLE') {
      draftCoords.value = [...draftCoords.value, [e.lng, e.lat]]
      if (draftCoords.value.length >= 2) finishDraw() // 中心+邊 / 兩對角
    } else {
      draftCoords.value = [...draftCoords.value, [e.lng, e.lat]]
    }
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

const selectedUnit = computed(() => realUnits.value.find((u) => u.id === selectedId.value) ?? null)
// 固定單位（指揮部等）：不可下移動令（後端 validator 權威擋 ORDER_UNIT_FIXED；此為 UX 提示）。
const selectedUnitFixed = computed(() => !!selectedUnit.value?.is_fixed)
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

// ---- 地圖編輯器（stage ③b）----
const canDraw = computed(() => canControl.value || !!myFaction.value)
const drawActive = computed(() => drawKind.value !== null)

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
// #12 元素顯隱：地圖只渲染未隱藏的特徵（清單仍列全部，供切換）。
const shownFeatures = computed(() =>
  mapFeatures.value.filter((f) => !hiddenFeatureIds.value.includes(f.id)),
)
const featureFc = computed(() => featuresToFc(shownFeatures.value))
const featSymbol = computed(() => featureSymbolFc(shownFeatures.value)) // 北約符號點特徵（#11）
const influenceFc = computed(() => influenceToFc(shownFeatures.value, terrainClips.value))
const draftFc = computed(() => draftToFc(drawKind.value, draftCoords.value))
const drawableKinds = computed(() => FEATURE_KINDS.filter((k) => k.value !== 'WEAPON_EMPLACEMENT'))

// 標註套用（WP-E3 抽出）：快照與單獨重載共用同一份，避免兩處各自還原裁切環而漂移。
function applyFeatures(features: typeof mapFeatures.value) {
  mapFeatures.value = features
  // 還原已持久化的地形裁切環（#43）：裁切射界存在 attributes.viewshed_ring，重新整理後仍在。
  const clips: Record<string, number[][]> = {}
  for (const f of mapFeatures.value) {
    const ring = (f.attributes as Record<string, unknown> | undefined)?.viewshed_ring
    if (Array.isArray(ring) && ring.length >= 3) clips[f.id] = ring as number[][]
  }
  terrainClips.value = clips
}

async function loadFeatures() {
  // #92：帶視角 → 後端只回「共同 + 該陣營」的標註（過濾在後端，紅線 #3）。
  applyFeatures(
    await fetchMapFeatures(sessionId.value, viewpoint.value || null).catch(() => []),
  )
}
async function ensureWeaponTemplates() {
  if (!weaponTemplates.value.length) {
    weaponTemplates.value = await fetchEquipmentTemplates().catch(() => [])
  }
}
function startDraw(kind: DraftKind, featureKind: string) {
  selectedFeatureId.value = null
  armReshape(null) // #99b 開始繪製 → 收掉上一個物件的控制點
  drawFeatureKind.value = featureKind
  drawKind.value = kind
  draftCoords.value = []
  drawLabel.value = ''
  drawColor.value = ''
  drawNotes.value = ''
  drawSidc.value = ''
  // 障礙/建築預設高度 2m（#11）。
  drawHeight.value = featureKind === 'OBSTACLE' || featureKind === 'BUILDING' ? 2 : null
}
async function startWeaponDraw() {
  await ensureWeaponTemplates()
  startDraw('POINT', 'WEAPON_EMPLACEMENT')
}
function cancelDraw() {
  drawKind.value = null
  draftCoords.value = []
}
async function finishDraw() {
  if (!drawKind.value || !draftCoords.value.length) {
    cancelDraw()
    return
  }
  const isWeapon = drawFeatureKind.value === 'WEAPON_EMPLACEMENT'
  const tmpl = isWeapon ? weaponTemplates.value.find((t) => t.id === drawWeaponTemplate.value) : null
  const range = Number((tmpl?.base_stats as Record<string, unknown> | undefined)?.max_range_m)
  // 圓/矩存為 POLYGON（環由中心+邊 / 兩對角導出）；其餘照舊。
  const isShape = drawKind.value === 'CIRCLE' || drawKind.value === 'RECTANGLE'
  const ring = isShape ? shapeToPolygon(drawKind.value, draftCoords.value) : null
  const attrs: Record<string, unknown> = {}
  if (drawColor.value) attrs.color = drawColor.value
  if (drawWidth.value !== DEFAULT_FEATURE_WIDTH) attrs.width = drawWidth.value
  if (drawNotes.value.trim()) attrs.notes = drawNotes.value.trim()
  if (drawHeight.value != null) attrs.height_m = drawHeight.value
  if (drawSidc.value && drawKind.value === 'POINT') attrs.sidc = drawSidc.value
  const body: FeatureCreate = {
    kind: drawFeatureKind.value,
    geometry_type: isShape ? 'POLYGON' : drawKind.value,
    geometry: isShape ? ring : drawKind.value === 'POINT' ? draftCoords.value[0] : draftCoords.value,
    label: drawLabel.value.trim() || tmpl?.name || null,
    // #92 歸屬：套了陣營視角時，繪出的標註歸該陣營（否則全知繪製一律落 WHITE_CELL 共同層，
    // 等於白軍替某軍畫的東西全體都看得到）。一般角色不帶，後端一律歸本軍。
    owner_faction: viewpoint.value || null,
    weapon_template_id: isWeapon ? drawWeaponTemplate.value || null : null,
    influence_radius_m: isWeapon && Number.isFinite(range) ? range : null,
    attributes: Object.keys(attrs).length ? attrs : undefined,
  }
  try {
    await createMapFeature(sessionId.value, body)
    await loadFeatures()
    toasts.push({ severity: 'success', title: '已新增地圖標註', timeoutMs: 2500 })
  } catch (e) {
    toasts.push({
      severity: 'error',
      title: '新增標註失敗',
      detail: (e as { message?: string }).message,
      timeoutMs: 0,
    })
  }
  cancelDraw()
}
function onFeatureClick(e: { id: string }) {
  selectedFeatureId.value = e.id
  // #26 點地圖物件即跳出「地圖編輯」小工具的編輯工具列（若有繪製權）。
  if (canDraw.value && !widgets.value.mapedit.open) {
    widgets.value.mapedit.open = true
    focusWidget('mapedit')
  }
  const f = mapFeatures.value.find((x) => x.id === e.id)
  const a = (f?.attributes ?? {}) as Record<string, unknown>
  editFeatLabel.value = f?.label ?? ''
  editFeatColor.value = typeof a.color === 'string' ? a.color : ''
  editFeatOwner.value = f?.owner_faction ?? ''
  editFeatZone.value = f ? featureZoneClass(f) : ''
  editFeatWidth.value = f ? featureLineWidth(f) : DEFAULT_FEATURE_WIDTH
  editFeatNotes.value = typeof a.notes === 'string' ? a.notes : ''
  editFeatHeight.value = typeof a.height_m === 'number' ? a.height_m : null
  editFeatSidc.value = typeof a.sidc === 'string' ? a.sidc : ''
  editFeatRange.value = typeof f?.influence_radius_m === 'number' ? f.influence_radius_m : null
  editFeatDir.value = typeof a.direction_deg === 'number' ? a.direction_deg : 0
  editFeatArc.value = typeof a.arc_deg === 'number' ? a.arc_deg : 360
  origRange.value = editFeatRange.value
  origDir.value = editFeatDir.value
  origArc.value = editFeatArc.value
}
const selectedFeature = computed(
  () => mapFeatures.value.find((f) => f.id === selectedFeatureId.value) ?? null,
)
// 地形裁切射界（#11）：對選取的武器/雷達特徵逐方位查 LOS → 存 viewshed 環（取代理想扇形）。
async function applyTerrainClip() {
  const f = selectedFeature.value
  const range = editFeatRange.value
  if (!f || !range || range <= 0) return
  const g = f.geometry as unknown
  const center = f.geometry_type === 'POINT' ? (g as number[]) : ((g as number[][])?.[0] ?? null)
  if (!center) return
  const arc = editFeatArc.value
  clipBusy.value = true
  try {
    const fp = await fetchTerrainFootprint(sessionId.value, {
      origin: [center[0]!, center[1]!],
      max_range_m: range,
      direction_deg: arc < 360 ? editFeatDir.value : null,
      arc_deg: arc < 360 ? arc : 360,
      steps: arc < 360 ? 24 : 36,
      observer_height_m: 10,
      target_height_m: 2, // 目標/障礙離地高 default 2m（#11）
    })
    if (fp.ring.length >= 3) {
      terrainClips.value = { ...terrainClips.value, [f.id]: fp.ring as number[][] }
      // 持久化到 attributes.viewshed_ring（#43）：重新整理後仍保留裁切；失敗僅保留本地。
      try {
        await editMapFeature(sessionId.value, f.id, { attributes: { viewshed_ring: fp.ring } })
      } catch {
        // 持久化失敗不擋操作：本地裁切仍生效，僅無法存活重新整理。
      }
      toasts.push({
        severity: fp.clipped ? 'success' : 'info',
        title: fp.clipped ? '已套用地形裁切（射界受稜線遮蔽）' : '地形裁切：此扇區全通視',
        timeoutMs: 2500,
      })
    }
  } catch (err) {
    toasts.push({
      severity: 'warn',
      title: '地形裁切不可用',
      detail: '地形服務未就緒，改用理想射界。' + ((err as { message?: string }).message ?? ''),
      timeoutMs: 4000,
    })
  } finally {
    clipBusy.value = false
  }
}
function clearTerrainClip(fid: string) {
  if (!(fid in terrainClips.value)) return
  const next: Record<string, number[][]> = {}
  for (const [k, v] of Object.entries(terrainClips.value)) {
    if (k !== fid) next[k] = v
  }
  terrainClips.value = next
}
// 使用者按「還原理想射界」（#43）：本地移除 + 持久化清除（viewshed_ring=null），才不會重新整理又跑回來。
async function onClearTerrainClip(fid: string) {
  clearTerrainClip(fid)
  try {
    await editMapFeature(sessionId.value, fid, { attributes: { viewshed_ring: null } })
  } catch {
    // 清除持久化失敗不擋操作；地圖已即時還原理想射界。
  }
}
/**
 * #99 本使用者對選取圖形**有沒有編修權**（與後端 `_feature_for_edit` 同一條規則：
 * 全知可編任一；否則只能編本軍的，**共同層 WHITE_CELL 標註對一般指揮官是唯讀**）。
 * 在前端先 gate，才不會拖完才吃 403。有權 ≠ 現在可拖，見 `reshapeArmedId`。
 */
const mayEditSelectedFeature = computed(() => {
  const f = selectedFeature.value
  if (!f || !canDraw.value) return false
  if (canControl.value) return true
  return !!myFaction.value && f.owner_faction === myFaction.value
})

/**
 * #99b 整形須先「解鎖」：只有經右鍵選單（或編輯面板按鈕）明確進入調整狀態的那一個圖形
 * 才畫控制點、才吃拖曳。**單純點選不解鎖**——否則在圖上點一下再手滑就把標註拖歪了，
 * 而地圖上點選是最頻繁的操作。換選別的圖形、取消選取、開始繪製都會自動上鎖。
 */
const reshapeArmedId = ref<string | null>(null)
const canEditSelectedFeature = computed(
  () => mayEditSelectedFeature.value && reshapeArmedId.value === selectedFeatureId.value,
)
function armReshape(id: string | null) {
  reshapeArmedId.value = id
}
watch(selectedFeatureId, (id) => {
  if (id !== reshapeArmedId.value) reshapeArmedId.value = null // 換對象/取消選取 → 自動上鎖
})

/**
 * #99 整形落地：拖頂點/中點/本體後 PATCH 新幾何。
 * 幾何一變舊裁切環就失效 → 同一 PATCH 清 `viewshed_ring`（與 onFeatureMove/rotateFeature 同紀律，#43）。
 * **失敗也要 loadFeatures**：地圖上此刻顯示的是拖曳後的本地預覽，不重載就會停在一個伺服器沒接受的形狀。
 */
async function onFeatureReshape(e: { id: string; geometry: number[][] }) {
  if (!e.geometry?.length) return
  try {
    await editMapFeature(sessionId.value, e.id, {
      geometry: e.geometry,
      attributes: { viewshed_ring: null },
    })
    clearTerrainClip(e.id)
    await loadFeatures()
  } catch (err) {
    toasts.push({
      severity: 'error',
      title: '調整形狀失敗',
      detail: (err as { message?: string }).message,
      timeoutMs: 0,
    })
    await loadFeatures() // 還原成伺服器上的權威幾何
  }
}

/**
 * #99 刪除線/面的一個控制點。低於最少頂點數（線 2、面 3）則拒絕並說明——
 * 硬刪下去會變成退化幾何（2 點的面 `toGeometry` 回 null，該標註會直接從地圖上消失）。
 * 兩條入口共用：右鍵選單、Alt＋點控制點。
 */
async function deleteVertexAt(index: number, featureId?: string) {
  const f = featureId
    ? (mapFeatures.value.find((x) => x.id === featureId) ?? null)
    : selectedFeature.value
  if (!f) return
  const ring = openRing((f.geometry as number[][]) ?? [])
  const next = removeVertex(ring, index, f.geometry_type)
  if (!next) {
    toasts.push({
      severity: 'warn',
      title: '無法刪除控制點',
      detail: `${f.geometry_type === 'POLYGON' ? '面' : '線'}至少需要 ${MIN_VERTICES[f.geometry_type] ?? 2} 個控制點`,
      timeoutMs: 3000,
    })
    return
  }
  await onFeatureReshape({ id: f.id, geometry: next })
}
/** #99 右鍵控制點 → 刪除該頂點。 */
async function ctxDeleteVertex() {
  const idx = ctxMenu.value?.vertexIndex
  closeCtx()
  if (idx != null) await deleteVertexAt(idx)
}
/** #99c Alt＋點控制點 → 刪除該頂點（免開選單的快捷路徑）。 */
async function onFeatureVertexDelete(e: { id: string; index: number }) {
  await deleteVertexAt(e.index, e.id)
}

// 拖放移動點特徵（#11 B2）：MapCanvas emit 新座標 → PATCH 幾何 → 重載。
async function onFeatureMove(e: { id: string; lng: number; lat: number }) {
  const f = mapFeatures.value.find((x) => x.id === e.id)
  if (!f || f.geometry_type !== 'POINT') return
  try {
    // 幾何變動 → 舊裁切環失效：同一 PATCH 一併清除持久化的 viewshed_ring（#43）。
    await editMapFeature(sessionId.value, e.id, {
      geometry: [e.lng, e.lat],
      attributes: { viewshed_ring: null },
    })
    clearTerrainClip(e.id)
    await loadFeatures()
  } catch (err) {
    toasts.push({
      severity: 'error',
      title: '移動失敗',
      detail: (err as { message?: string }).message,
      timeoutMs: 0,
    })
  }
}
// #26 旋轉選取的物件：武器扇區點→調方向角；面/線→頂點繞質心旋轉。
async function rotateFeature(deg: number) {
  const f = selectedFeature.value
  if (!f) return
  if (f.geometry_type === 'POINT') {
    editFeatDir.value = ((((editFeatDir.value + deg) % 360) + 360) % 360)
    if (editFeatArc.value >= 360) editFeatArc.value = 90 // 全向圓→轉成可見扇形才看得到方向
    await saveFeatureEdit()
    return
  }
  const g = f.geometry as number[][]
  if (!Array.isArray(g) || g.length < 2) return
  try {
    clearTerrainClip(f.id)
    // 旋轉幾何 → 舊裁切環失效：同 PATCH 清除持久化的 viewshed_ring（#43）。
    await editMapFeature(sessionId.value, f.id, {
      geometry: rotatePoints(g, deg),
      attributes: { viewshed_ring: null },
    })
    await loadFeatures()
  } catch (err) {
    toasts.push({ severity: 'error', title: '旋轉失敗', detail: (err as { message?: string }).message, timeoutMs: 0 })
  }
}
async function saveFeatureEdit() {
  const fid = selectedFeatureId.value
  if (!fid) return
  const f = mapFeatures.value.find((x) => x.id === fid)
  const attrs: Record<string, unknown> = { ...((f?.attributes ?? {}) as Record<string, unknown>) }
  if (editFeatColor.value) attrs.color = editFeatColor.value
  else delete attrs.color
  if (editFeatWidth.value !== DEFAULT_FEATURE_WIDTH) attrs.width = editFeatWidth.value
  else delete attrs.width
  if (editFeatNotes.value.trim()) attrs.notes = editFeatNotes.value.trim()
  else delete attrs.notes
  if (editFeatHeight.value != null) attrs.height_m = editFeatHeight.value
  else delete attrs.height_m
  if (editFeatSidc.value) attrs.sidc = editFeatSidc.value
  else delete attrs.sidc
  // WP-A3：禁射級別。清空即移除該鍵——但 PATCH 對 attributes 是 merge，刪不掉鍵，
  // 故以 null 明示「取消」，後端 merge 後值為 null，`no_strike.py` 只認非空字串故等同無效。
  if (editFeatZone.value) attrs.zone_class = editFeatZone.value
  else attrs.zone_class = null
  // 武器射向/雷達扇區（#11 C）：張角 <360 才存方向/張角（否則全向圓）。
  if (editFeatArc.value > 0 && editFeatArc.value < 360) {
    attrs.direction_deg = editFeatDir.value
    attrs.arc_deg = editFeatArc.value
  } else {
    delete attrs.direction_deg
    delete attrs.arc_deg
  }
  // 只有射程/方向/張角真的變動才失效地形裁切環；否則（改名/顏色/備註）保留已套用的裁切（#43）。
  const arcChanged =
    editFeatRange.value !== origRange.value ||
    editFeatDir.value !== origDir.value ||
    editFeatArc.value !== origArc.value
  if (arcChanged) attrs.viewshed_ring = null // 射界參數變動 → 一併清除持久化的裁切環
  try {
    const ownerChanged =
      canControl.value && !!editFeatOwner.value && editFeatOwner.value !== f?.owner_faction
    await editMapFeature(sessionId.value, fid, {
      label: editFeatLabel.value.trim() || null,
      influence_radius_m: editFeatRange.value,
      attributes: attrs,
      // 僅全知且確實變更才送——一般角色帶此欄後端會 403，不該因為存個名稱就撞上。
      ...(ownerChanged ? { owner_faction: editFeatOwner.value } : {}),
    })
    if (arcChanged) clearTerrainClip(fid)
    origRange.value = editFeatRange.value
    origDir.value = editFeatDir.value
    origArc.value = editFeatArc.value
    await loadFeatures()
    toasts.push({ severity: 'success', title: '已更新標註', timeoutMs: 2000 })
  } catch (e) {
    toasts.push({
      severity: 'error',
      title: '更新失敗',
      detail: (e as { message?: string }).message,
      timeoutMs: 0,
    })
  }
}
async function removeFeature(fid: string) {
  try {
    await deleteMapFeature(sessionId.value, fid)
    if (selectedFeatureId.value === fid) selectedFeatureId.value = null
    await loadFeatures()
  } catch (e) {
    toasts.push({
      severity: 'error',
      title: '刪除失敗',
      detail: (e as { message?: string }).message,
      timeoutMs: 0,
    })
  }
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

// ---- #28 移動路徑預覽 ----
// 目的地/自訂路徑改變 → 去抖後打 preview 端點，取回距離/tick/油耗/可行性/強穿阻礙。
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
function crossKindLabel(kind: string): string {
  return (
    { OBSTACLE: '障礙', BUILDING: '建築', TERRAIN: '地形' } as Record<string, string>
  )[kind] ?? kind
}
// #80：機動 profile 中文標籤（由編裝導出：徒步/輪型/履帶）。
function mobilityLabel(profile: string): string {
  return (
    { FOOT: '徒步', WHEELED: '輪型', TRACKED: '履帶', BOAT: '舟艇', AIR: '空中' } as Record<
      string,
      string
    >
  )[profile] ?? profile
}
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
  const payload =
    orderType.value === 'MOVE'
      ? {
          to_h3: destH3.value,
          mobility_profile: 'FOOT',
          ...(destLatLng.value
            ? { to_lat: destLatLng.value.lat, to_lng: destLatLng.value.lng }
            : {}),
          // #28 自訂路徑：夾帶 waypoints 讓執行期沿折線前進 + 強穿耗損。
          ...(moveWaypoints.value.length ? { waypoints: moveWaypoints.value } : {}),
        }
      : {
          target_unit_id: targetUnitId.value,
          ...(weaponId.value ? { weapon_id: weaponId.value } : {}),
          ...(ammoType.value ? { ammo_type: ammoType.value } : {}),
          // 聯合火力（未指定單一武器）且政策非 FREE → 夾帶 fire_policy（SPEC_EXTEND P4）。
          ...(!weaponId.value && firePolicy.value !== 'FREE'
            ? { fire_policy: firePolicy.value }
            : {}),
        }
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
      detail: lines.length ? undefined : err.message ?? '系統拒絕此指令',
      lines,
      timeoutMs: 10000, // #7：10 秒後自動關閉
    })
  }
}

async function cancel(id: string) {
  await cancelOrder(sessionId.value, id).catch(() => undefined)
  await refresh()
}

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
  if (trackTimer) clearInterval(trackTimer) // #95 軌跡淡出計時器
})

// 圖層/底圖偏好持久化（#3/#9）：載入 → 存檔（跨換頁/重整保留操作員的 COP 設定：
// 開啟的圖層、底圖、透明度、套疊順序、等高線間距）。
onMounted(() => {
  if (!import.meta.client) return
  try {
    const p = JSON.parse(localStorage.getItem(LAYER_PREFS_KEY) ?? '{}')
    if (typeof p.hex === 'boolean') hex.value = p.hex
    if (typeof p.hillshade === 'boolean') hillshade.value = p.hillshade
    if (typeof p.contour === 'boolean') contour.value = p.contour
    // 底圖：僅在該來源仍存在時還原（線上底圖可能已關閉 → 回退預設）。
    if (typeof p.basemap === 'string' && basemapSources.some((s) => s.id === p.basemap)) {
      basemap.value = p.basemap
    }
    if (p.layerOpacity) layerOpacity.value = { ...layerOpacity.value, ...p.layerOpacity }
    if (Array.isArray(p.layerOrder) && p.layerOrder.length) layerOrder.value = p.layerOrder
    if (typeof p.contourMajor === 'number') contourMajor.value = p.contourMajor
    if (typeof p.contourMinor === 'number') contourMinor.value = p.contourMinor
    if (typeof p.latlngGrid === 'boolean') latlngGrid.value = p.latlngGrid
    if (typeof p.mgrsGrid === 'boolean') mgrsGrid.value = p.mgrsGrid
    if (typeof p.gridStepDeg === 'number') gridStepDeg.value = p.gridStepDeg
    if (typeof p.hexMaxRes === 'number') hexMaxRes.value = p.hexMaxRes
    if (typeof p.hexLimitKm === 'number') hexLimitKm.value = p.hexLimitKm
    if (typeof p.dayNight === 'boolean') dayNight.value = p.dayNight
    if (typeof p.timeOfDay === 'number') timeOfDay.value = p.timeOfDay
    if (typeof p.preciseMove === 'boolean') preciseMove.value = p.preciseMove
    if (p.widgets && typeof p.widgets === 'object') {
      for (const id of Object.keys(widgets.value) as WidgetId[]) {
        const s = p.widgets[id]
        if (!s || typeof s !== 'object') continue
        const cur = widgets.value[id]
        const dock = s.dock === 'left' || s.dock === 'right' || s.dock === 'float' ? s.dock : cur.dock
        widgets.value[id] = {
          open: typeof s.open === 'boolean' ? s.open : cur.open,
          dock,
          x: typeof s.x === 'number' ? s.x : cur.x,
          y: typeof s.y === 'number' ? s.y : cur.y,
          w: typeof s.w === 'number' ? s.w : cur.w,
          h: typeof s.h === 'number' ? s.h : cur.h,
          z: cur.z,
        }
      }
    }
    if (typeof p.hexLineWidth === 'number') hexLineWidth.value = p.hexLineWidth
    if (typeof p.contourMajorWidth === 'number') contourMajorWidth.value = p.contourMajorWidth
    if (typeof p.contourMinorWidth === 'number') contourMinorWidth.value = p.contourMinorWidth
    if (typeof p.hexLineColor === 'string') hexLineColor.value = p.hexLineColor
    if (typeof p.contourColor === 'string') contourColor.value = p.contourColor
    if (typeof p.gridColor === 'string') gridColor.value = p.gridColor
    if (typeof p.gridWidth === 'number') gridWidth.value = p.gridWidth
    if (typeof p.mgrsColor === 'string') mgrsColor.value = p.mgrsColor
  } catch {
    /* 壞資料忽略，用預設 */
  }
})
watch(
  [
    hex,
    hillshade,
    contour,
    basemap,
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
    preciseMove,
    hexLineWidth,
    contourMajorWidth,
    contourMinorWidth,
    hexLineColor,
    contourColor,
    gridColor,
    gridWidth,
    mgrsColor,
    widgets,
  ],
  () => {
    if (!import.meta.client) return
    try {
      localStorage.setItem(
        LAYER_PREFS_KEY,
        JSON.stringify({
          hex: hex.value,
          hillshade: hillshade.value,
          contour: contour.value,
          basemap: basemap.value,
          layerOpacity: layerOpacity.value,
          layerOrder: layerOrder.value,
          contourMajor: contourMajor.value,
          contourMinor: contourMinor.value,
          latlngGrid: latlngGrid.value,
          mgrsGrid: mgrsGrid.value,
          gridStepDeg: gridStepDeg.value,
          hexMaxRes: hexMaxRes.value,
          hexLimitKm: hexLimitKm.value,
          dayNight: dayNight.value,
          timeOfDay: timeOfDay.value,
          preciseMove: preciseMove.value,
          widgets: widgets.value,
          hexLineWidth: hexLineWidth.value,
          contourMajorWidth: contourMajorWidth.value,
          contourMinorWidth: contourMinorWidth.value,
          hexLineColor: hexLineColor.value,
          contourColor: contourColor.value,
          gridColor: gridColor.value,
          gridWidth: gridWidth.value,
          mgrsColor: mgrsColor.value,
        }),
      )
    } catch {
      /* 配額/隱私模式忽略 */
    }
  },
  { deep: true },
)
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
          <div class="map-editor" data-testid="map-editor">
          <div v-if="!drawActive" class="me-tools">
            <label class="me-kind">
              類別
              <select v-model="drawFeatureKind" data-testid="draw-kind">
                <option v-for="k in drawableKinds" :key="k.value" :value="k.value">{{ k.label }}</option>
              </select>
            </label>
            <div class="me-btns">
              <button data-testid="draw-point" @click="startDraw('POINT', drawFeatureKind)">點</button>
              <button data-testid="draw-line" @click="startDraw('LINE', drawFeatureKind)">線</button>
              <button data-testid="draw-polygon" @click="startDraw('POLYGON', drawFeatureKind)">面</button>
              <button data-testid="draw-rect" @click="startDraw('RECTANGLE', drawFeatureKind)">矩形</button>
              <button data-testid="draw-circle" @click="startDraw('CIRCLE', drawFeatureKind)">圓形</button>
            </div>
            <div class="me-attrs">
              <input v-model="drawLabel" class="me-in" data-testid="draw-label" placeholder="名稱（選填）">
              <div class="me-row2">
                <label class="me-color" title="顏色">
                  <input v-model="drawColor" type="color">
                  顏色
                </label>
                <label class="me-h" title="線條粗細（點狀標註不適用）">
                  線寬<input
                    v-model.number="drawWidth"
                    data-testid="draw-width"
                    type="range"
                    min="0.5"
                    max="12"
                    step="0.5"
                  >{{ drawWidth }}
                </label>
                <label v-if="drawFeatureKind === 'OBSTACLE' || drawFeatureKind === 'BUILDING'" class="me-h">
                  高度<input v-model.number="drawHeight" type="number" min="0" step="0.5"> m
                </label>
              </div>
              <NatoSymbolSelect v-model="drawSidc" data-testid="draw-sidc" title="北約符號（僅點）" />
              <input v-model="drawNotes" class="me-in" data-testid="draw-notes" placeholder="備註（選填）">
            </div>
            <div class="me-weapon">
              <select v-model="drawWeaponTemplate" data-testid="draw-weapon-tmpl" @focus="ensureWeaponTemplates">
                <option value="">選武器範本…</option>
                <option v-for="t in weaponTemplates" :key="t.id" :value="t.id">{{ t.name }}</option>
              </select>
              <button data-testid="draw-weapon" :disabled="!drawWeaponTemplate" @click="startWeaponDraw">
                <i class="pi pi-bullseye" /> 武器據點
              </button>
            </div>
          </div>
          <div v-else class="me-drawing">
            <span v-if="drawKind === 'CIRCLE'">繪圓：先點中心，再點邊緣</span>
            <span v-else-if="drawKind === 'RECTANGLE'">繪矩形：點兩個對角</span>
            <span v-else>繪製中 · {{ draftCoords.length }} 點 · 點地圖加點</span>
            <div class="me-btns">
              <button
                v-if="drawKind === 'LINE' || drawKind === 'POLYGON'"
                data-testid="draw-finish"
                @click="finishDraw"
              >完成</button>
              <button data-testid="draw-cancel" @click="cancelDraw">取消</button>
            </div>
          </div>
          <div class="me-list">
            <div class="me-sub">標註 / 工事（{{ mapFeatures.length }}）</div>
            <ul>
              <li
                v-for="f in mapFeatures"
                :key="f.id"
                :class="{ sel: f.id === selectedFeatureId, hidden: hiddenFeatureIds.includes(f.id) }"
                data-testid="feature-row"
                @click="onFeatureClick({ id: f.id })"
              >
                <span class="fdot" :style="{ background: featureDisplayColor(f) }" />
                <span class="fname">{{ f.label || f.kind }}</span>
                <!-- #92 歸屬陣營：共同層標「共同」，否則以該陣營色點+代號標示 -->
                <span
                  class="fown"
                  data-testid="feature-owner"
                  :title="
                    f.owner_faction === 'WHITE_CELL'
                      ? '共同標註（全體可見）'
                      : `${f.owner_faction} 的標註（僅該陣營與白軍可見）`
                  "
                >
                  <template v-if="f.owner_faction === 'WHITE_CELL'">共同</template>
                  <template v-else>
                    <span class="u-dot" :style="{ background: factionColor(f.owner_faction) }" />
                    {{ f.owner_faction }}
                  </template>
                </span>
                <button
                  class="feye"
                  data-testid="feature-toggle-vis"
                  :title="hiddenFeatureIds.includes(f.id) ? '顯示' : '隱藏'"
                  @click.stop="toggleFeatureHidden(f.id)"
                >{{ hiddenFeatureIds.includes(f.id) ? '🚫' : '👁' }}</button>
                <button class="frm" data-testid="feature-delete" @click.stop="removeFeature(f.id)"><i class="pi pi-times" /></button>
              </li>
              <li v-if="!mapFeatures.length" class="empty">（尚無標註）</li>
            </ul>
          </div>
          <!-- 選取特徵的屬性編輯（#11）：名稱/顏色/備註/高度 → PATCH。 -->
          <div v-if="selectedFeature" class="me-edit" data-testid="feature-edit">
            <div class="me-sub">編輯：{{ selectedFeature.kind }}</div>
            <!-- #99 整形操作說明（控制點是地圖上的互動，面板裡看不到 → 需明講怎麼用）。
                 #99b 未解鎖時顯示上鎖狀態＋解鎖鈕，讓「為什麼拖不動」有答案。 -->
            <div v-if="canEditSelectedFeature" class="me-hint" data-testid="reshape-hint">
              <div class="me-hint-row">
                <span>
                  <template v-if="selectedFeature.geometry_type === 'POINT'">
                    <i class="pi pi-arrows-alt" /> 調整中：直接拖曳圖示可移動位置
                  </template>
                  <template v-else>
                    <i class="pi pi-arrows-alt" /> 調整中：拖白點改形狀 · 拖小圈可加點 · 拖線/面本身整體移動 ·
                    <b>Alt＋點白點</b>或<b>右鍵白點</b>刪點
                  </template>
                </span>
                <button class="me-lock" data-testid="reshape-lock" @click="armReshape(null)">
                  <i class="pi pi-lock" /> 完成
                </button>
              </div>
            </div>
            <div
              v-else-if="mayEditSelectedFeature"
              class="me-hint me-hint-locked"
              data-testid="reshape-locked"
            >
              <div class="me-hint-row">
                <span><i class="pi pi-lock" /> 形狀已鎖定（避免誤觸）——右鍵此物件選「編輯形狀」</span>
                <button
                  class="me-lock"
                  data-testid="reshape-unlock"
                  @click="armReshape(selectedFeatureId)"
                >
                  <i class="pi pi-lock-open" /> 調整形狀
                </button>
              </div>
            </div>
            <input v-model="editFeatLabel" class="me-in" data-testid="edit-feat-label" placeholder="名稱">
            <div class="me-row2">
              <label class="me-color"><input v-model="editFeatColor" type="color"> 顏色</label>
              <label class="me-h" title="線條粗細">
                線寬<input
                  v-model.number="editFeatWidth"
                  data-testid="edit-feat-width"
                  type="range"
                  min="0.5"
                  max="12"
                  step="0.5"
                >{{ editFeatWidth }}
              </label>
              <label v-if="selectedFeature.kind === 'OBSTACLE' || selectedFeature.kind === 'BUILDING'" class="me-h">
                高度<input v-model.number="editFeatHeight" type="number" min="0" step="0.5"> m
              </label>
            </div>
            <!-- #92 歸屬變更：僅全知可改（一般角色不顯示；後端亦擋 403）。 -->
            <label v-if="canControl" class="me-own">
              歸屬
              <select v-model="editFeatOwner" data-testid="edit-feat-owner">
                <option value="WHITE_CELL">共同（全體可見）</option>
                <option v-for="f in sessionFactions" :key="f" :value="f">{{ f }}</option>
              </select>
            </label>
            <!-- WP-A3 禁射區：僅全知可設，且只對面有意義（點/線不成區）。 -->
            <label
              v-if="canControl && selectedFeature.geometry_type === 'POLYGON'"
              class="me-own"
            >
              禁射
              <select v-model="editFeatZone" data-testid="edit-feat-zone">
                <option v-for="z in ZONE_CLASSES" :key="z.value" :value="z.value">
                  {{ z.label }}
                </option>
              </select>
            </label>
            <div v-if="editFeatZone" class="me-hint me-hint-zone" data-testid="zone-hint">
              <i class="pi pi-ban" />
              {{
                editFeatZone === 'NO_STRIKE'
                  ? '此區內的目標不得射擊：AI 的交戰令會被護欄 G4 剔除，人員下令一律被拒。'
                  : '此區內的目標需確認才可射擊：AI 令保留但升白軍確認，人員須明確勾選確認且會留痕。'
              }}
            </div>
            <NatoSymbolSelect
              v-if="selectedFeature.geometry_type === 'POINT'"
              v-model="editFeatSidc"
              data-testid="edit-feat-sidc"
            />
            <!-- #26 旋轉：面/線繞質心旋轉；武器點旋轉射向。 -->
            <div class="me-row2 me-rot-row">
              <span class="me-rot-lbl">旋轉</span>
              <button class="me-rot" data-testid="feat-rotate-ccw" @click="rotateFeature(-15)"><i class="pi pi-undo" /> 15°</button>
              <button class="me-rot" data-testid="feat-rotate-cw" @click="rotateFeature(15)"><i class="pi pi-refresh" /> 15°</button>
            </div>
            <!-- 武器射向/雷達扇區（#11 C）：射程 + 方向 + 張角（360=全向圓）。 -->
            <template v-if="selectedFeature.kind === 'WEAPON_EMPLACEMENT' || editFeatRange != null">
              <div class="me-sub">射程 / 射向扇區</div>
              <div class="me-row2">
                <label class="me-h">射程<input
                  v-model.number="editFeatRange"
                  type="number"
                  min="0"
                  max="100000"
                  step="50"
                  style="width: 5.5rem"
                > m</label>
                <label class="me-h">方向<input v-model.number="editFeatDir" type="number" min="0" max="359"> °</label>
              </div>
              <label class="me-h" data-testid="edit-feat-arc">
                張角 {{ editFeatArc }}°（360＝全向）
                <input v-model.number="editFeatArc" type="range" min="10" max="360" step="5" style="width: 100%">
              </label>
              <!-- 地形裁切（#11）：逐方位 LOS 把稜線/反斜面啃出缺口。 -->
              <div class="me-row2">
                <button
                  class="me-clip"
                  :disabled="clipBusy || !editFeatRange"
                  data-testid="apply-terrain-clip"
                  @click="applyTerrainClip"
                >
                  <i class="pi pi-eye" /> {{ clipBusy ? '裁切計算中…' : '地形裁切射界' }}
                </button>
                <button
                  v-if="terrainClips[selectedFeature.id]"
                  class="me-clip me-clip-off"
                  data-testid="clear-terrain-clip"
                  @click="onClearTerrainClip(selectedFeature.id)"
                >
                  還原理想射界
                </button>
              </div>
            </template>
            <input v-model="editFeatNotes" class="me-in" data-testid="edit-feat-notes" placeholder="備註">
            <button class="me-save" data-testid="save-feat-edit" @click="saveFeatureEdit">儲存屬性</button>
          </div>
          </div>
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
.feye {
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.75rem;
  opacity: 0.75;
}
.feye:hover {
  opacity: 1;
}
.map-editor .me-list li.hidden .fname {
  opacity: 0.4;
  text-decoration: line-through;
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

/* 地圖編輯器面板（stage ③b）——浮在地圖左上。 */
.map-editor {
  position: absolute;
  left: 3.5rem;
  top: 1rem;
  z-index: 11;
  width: 14rem;
  padding: 0.6rem 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid #3f3f1e;
  background: rgba(15, 23, 42, 0.96);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  font-size: 0.78rem;
  color: #e2e8f0;
}
.map-editor .me-hd {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.4rem;
}
.map-editor .me-x {
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}
.map-editor .me-kind {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  color: #94a3b8;
  font-size: 0.72rem;
  margin-bottom: 0.35rem;
}
.map-editor select {
  flex: 1;
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.2rem 0.3rem;
  font-size: 0.74rem;
}
.map-editor .me-btns {
  display: flex;
  gap: 0.3rem;
  margin-bottom: 0.35rem;
}
.map-editor .me-btns button,
.map-editor .me-weapon button {
  flex: 1;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #172554;
  color: #e2e8f0;
  cursor: pointer;
  padding: 0.2rem 0.35rem;
  font-size: 0.74rem;
}
.map-editor .me-weapon {
  display: flex;
  gap: 0.3rem;
  margin-bottom: 0.4rem;
}
.map-editor .me-weapon button:disabled {
  opacity: 0.5;
  cursor: default;
}
.map-editor .me-drawing {
  color: #fde68a;
  font-size: 0.74rem;
  margin-bottom: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
/* 繪製/編輯屬性欄（#11） */
.map-editor .me-attrs,
.map-editor .me-edit {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin: 0.3rem 0;
}
.map-editor .me-edit {
  border-top: 1px solid #1e293b;
  padding-top: 0.4rem;
}
.map-editor .me-in {
  padding: 0.25rem 0.4rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.75rem;
}
.map-editor .me-row2 {
  display: flex;
  gap: 0.6rem;
  align-items: center;
}
.map-editor .me-color,
.map-editor .me-h {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.72rem;
  color: #94a3b8;
}
/* #92 歸屬變更下拉（僅全知可見）。 */
.map-editor .me-own {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.72rem;
  color: #94a3b8;
}
.map-editor .me-own select {
  flex: 1;
  padding: 0.2rem 0.3rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.72rem;
}
.map-editor .me-color input {
  width: 1.6rem;
  height: 1.3rem;
  padding: 0;
  border: none;
  background: none;
}
.map-editor .me-h input {
  width: 3rem;
  padding: 0.15rem 0.3rem;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  background: #0a1626;
  color: #e2e8f0;
}
.map-editor .me-rot-row {
  align-items: center;
}
.map-editor .me-rot-lbl {
  color: #94a3b8;
  font-size: 0.72rem;
}
.map-editor .me-rot {
  padding: 0.2rem 0.4rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #cbd5e1;
  cursor: pointer;
  font-size: 0.72rem;
}
.map-editor .me-rot:hover {
  border-color: #2563eb;
}
.map-editor .me-save {
  padding: 0.3rem;
  border: 0;
  border-radius: 0.25rem;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
  font-size: 0.75rem;
}
.map-editor .me-sub {
  color: #64748b;
  font-size: 0.68rem;
  border-top: 1px solid #1e293b;
  padding-top: 0.35rem;
  margin-bottom: 0.25rem;
}
/* #99 整形操作提示 / #99b 鎖定狀態 */
.map-editor .me-hint {
  color: #7dd3fc;
  background: #0c2233;
  border: 1px solid #164e63;
  border-radius: 0.25rem;
  font-size: 0.66rem;
  line-height: 1.35;
  padding: 0.28rem 0.4rem;
  margin-bottom: 0.3rem;
}
.map-editor .me-hint-zone {
  color: #fca5a5;
  background: #2a1215;
  border-color: #7f1d1d;
}
.map-editor .me-hint-locked {
  color: #94a3b8;
  background: #111827;
  border-color: #334155;
}
.map-editor .me-hint-row {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  justify-content: space-between;
}
.map-editor .me-lock {
  flex: none;
  padding: 0.15rem 0.4rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 0.66rem;
  white-space: nowrap;
  cursor: pointer;
}
.map-editor .me-lock:hover {
  border-color: #2563eb;
}
.map-editor .me-clip {
  flex: 1;
  padding: 0.3rem;
  border: 1px solid #0e7490;
  border-radius: 0.25rem;
  background: #0e7490;
  color: #e0f2fe;
  cursor: pointer;
  font-size: 0.72rem;
}
.map-editor .me-clip:disabled {
  opacity: 0.5;
  cursor: default;
}
.map-editor .me-clip-off {
  flex: 0 0 auto;
  background: transparent;
  border-color: #475569;
  color: #94a3b8;
}
.map-editor .me-list ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  max-height: 12rem;
  overflow-y: auto;
}
.map-editor .me-list li {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.15rem 0.25rem;
  border-radius: 0.2rem;
  cursor: pointer;
}
.map-editor .me-list li.sel {
  background: #1e293b;
}
.map-editor .fdot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  flex: none;
}
.map-editor .fname {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* #92 標註歸屬陣營徽章：讓「這是誰畫的、誰看得到」一眼可辨。 */
.map-editor .fown {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  flex: none;
  padding: 0.05rem 0.3rem;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  color: #94a3b8;
  font-size: 0.68rem;
  white-space: nowrap;
}
.map-editor .fown .u-dot {
  display: inline-block;
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
}
.map-editor .frm {
  border: none;
  background: transparent;
  color: #f87171;
  cursor: pointer;
}
.map-editor .empty {
  color: #64748b;
  cursor: default;
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
