<script setup lang="ts">
import type { FilterSpecification, GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'
import {
  DEFAULT_OVERLAY_ORDER,
  DEFAULT_ZOOM,
  OVERLAY_LAYER_GROUPS,
  TAIWAN_CENTER,
  basemapOpacityMembers,
  buildBasemapSources,
  buildGraticule,
  buildOfflineStyle,
  openMapTilesDarkLayers,
} from '~/composables/useMapStyle'
import { hexCellsForBounds } from '~/composables/useHexGrid'
import { buildLatLngGrid, buildMgrsLabels } from '~/composables/useCoordGrid'
import { cellToBoundary, cellToLatLng, latLngToCell } from 'h3-js'
import {
  HP_BAR_PREFIX,
  HP_BAR_STEP,
  type Contact,
  type OwnUnit,
  buildUnitFeatures,
} from '~/composables/useUnits'
import {
  HP_BAR_PIXEL_RATIO,
  LOCK_BADGE_ID,
  LOCK_BADGE_PIXEL_RATIO,
  hpBarImage,
  lockBadgeImage,
  symbolImage,
} from '~/composables/useMilsymbol'
import { insertVertex, midpoints, moveVertex, openRing, translateRing } from '~/composables/useMapFeatures'

const emit = defineEmits<{
  mapClick: [{ lng: number; lat: number; h3: string }]
  unitClick: [{ id: string; faction: string; kind: string }]
  featureClick: [{ id: string }] // 點到地圖標註/工事（stage ③b）
  basemapError: [{ id: string }] // 底圖瓦片載入失敗（供上層回退離線）
  // 右鍵選單（#3，ATAK 式移動/攻擊）：螢幕座標 + 經緯 + 游標下的單位（若有）。
  contextMenu: [
    {
      x: number
      y: number
      lng: number
      lat: number
      unitId?: string
      faction?: string
      kind?: string
      featureId?: string // #26 游標下的地圖標註/工事
      vertexIndex?: number // #99 游標下的控制點索引（供「刪除控制點」）
    },
  ]
  featureMove: [{ id: string; lng: number; lat: number }] // 拖放移動點特徵（#11 B2）
  // #99 整形：拖頂點/中點/本體後的新幾何（存放格式的開放環 [[lng,lat],…]）。
  featureReshape: [{ id: string; geometry: number[][] }]
  unitMove: [{ id: string; lng: number; lat: number }] // 地圖狀態編輯：拖放單位到新座標
  // 地圖狀態編輯（多選）：一次移動多個單位（Shift 多選 / 框選後整組拖曳）到各自新座標。
  unitsMove: [{ moves: { id: string; lng: number; lat: number }[] }]
  unitsSelected: [{ count: number }] // 多選數量變更（供「已選 N 個」徽章）
  // 選取單位的螢幕座標（供 Unit 資訊卡懸浮於圖標旁；地圖平移/縮放即時更新；無選取→null）。
  selectScreenPos: [{ x: number; y: number } | null]
}>()

type Fc = { type: 'FeatureCollection'; features: unknown[] }
const _EMPTY_FEAT_FC: Fc = { type: 'FeatureCollection', features: [] }

// 由 <ClientOnly> 包裹確保只在 client 掛載；maplibre-gl 於 onMounted 動態 import（絕不進 SSR，
// 因其 module 於 import 時觸及 window/document）。
const props = withDefaults(
  defineProps<{
    hexVisible?: boolean
    hillshadeVisible?: boolean
    contourVisible?: boolean
    ownUnits?: OwnUnit[]
    contacts?: Contact[]
    currentTick?: number
    selectedId?: string | null // 選取的我方單位（藍色高亮環）
    targetId?: string | null // ENGAGE 鎖定的目標（紅色高亮環）
    basemapId?: string // 當前底圖來源 id（offline / street / satellite / 軍用…）
    destH3?: string | null // MOVE 目的格（res 8）——畫出吸附後的格與格心，讓「點哪→到哪」透明（#4b）
    destPoint?: { lng: number; lat: number } | null // 精確移動落點（#2）——有值時只畫精確點、不畫吸附格
    movePath?: number[][] | null // #28 移動路徑預覽（[[lng,lat],…] 含起點）——畫出規劃路線
    moveForced?: boolean // #28 路徑是否需強穿阻礙（true→琥珀虛線警示）
    moveCrossings?: number[][] | null // #28 穿越阻礙的標記點（[[lng,lat],…]）
    layerOpacity?: Record<string, number> // 各圖層群透明度乘數（#9；basemap/hillshade/contour/hex）
    layerOrder?: string[] // 疊加層套疊順序（上→下，#9）
    contourMajor?: number // 主等高線間距 m（較粗，#8；預設 100）
    contourMinor?: number // 次等高線間距 m（較細，#8；預設 50）
    hexLineWidth?: number // 六角網格線寬 px（#5 線條粗細設定）
    contourMajorWidth?: number // 主等高線線寬 px（#5）
    contourMinorWidth?: number // 次等高線線寬 px（#5）
    // #22 線條顏色：六角/等高線/座標網格/MGRS。
    hexLineColor?: string
    contourColor?: string
    gridColor?: string
    gridWidth?: number // 經緯度網格線寬 px
    mgrsColor?: string
    featureFc?: Fc // 地圖標註/工事（stage ③b）
    featSymbolFc?: Fc // 帶北約符號的點特徵（#11）
    featSymbolIcons?: { key: string; sidc: string }[] // 需生成的 milsymbol icon 規格（#11）
    influenceFc?: Fc // 影響範圍圓
    draftFc?: Fc // 繪製中草稿
    weaponTrackFc?: Fc // #95 武器軌跡（顯示用；由已裁決的交戰事件驅動）
    selectedFeatureId?: string | null // 選取的標註（高亮）
    // #99 選取的標註可被本使用者整形（拖頂點/中點/本體）→ 顯示控制點。
    // 由上層以「有繪製權 ∧ 對此標註有編修權」算出：後端 `_feature_for_edit` 只讓全知或本軍改，
    // 在這裡就 gate 住，才不會讓人拖了半天才吃 403。
    featureEdit?: boolean
    drawActive?: boolean // 繪圖模式：地圖點擊視為加頂點（不選單位/標註）
    targeting?: boolean // 設定目標中（#3）：游標改十字準星，提示「點地圖選落點/目標」
    editUnits?: boolean // 地圖狀態編輯：單位可拖放到新座標（White Cell 布局）
    latlngGrid?: boolean // 經緯度網格（#9）
    mgrsGrid?: boolean // MGRS 標記（#9）
    gridStepDeg?: number // 網格密度（度，#9）
    queryPoint?: { lng: number; lat: number } | null // 座標查詢點（#10）
    hexMaxRes?: number // 六角網格最細解析度上限（設定最小網格）
    hexLimitKm?: number // 交戰範圍：僅計算視野中心此半徑內的格（0=不限）
    dayNight?: boolean // 日照視覺（晨昏/夜間色調，#6）
    timeOfDay?: number // 一日時間 0–24（#6）
  }>(),
  {
    hexVisible: false,
    hillshadeVisible: false,
    contourVisible: false,
    ownUnits: () => [],
    contacts: () => [],
    currentTick: 0,
    selectedId: null,
    targetId: null,
    basemapId: 'offline',
    destH3: null,
    destPoint: null,
    movePath: null,
    moveForced: false,
    moveCrossings: null,
    layerOpacity: () => ({}),
    layerOrder: () => [...DEFAULT_OVERLAY_ORDER],
    contourMajor: 100,
    contourMinor: 50,
    hexLineWidth: 0.5,
    hexLineColor: '#38bdf8',
    contourColor: '#c9a15c',
    gridColor: '#5b7fa6',
    gridWidth: 0.5,
    mgrsColor: '#facc15',
    contourMajorWidth: 1.2,
    contourMinorWidth: 0.5,
    featureFc: () => ({ type: 'FeatureCollection', features: [] }),
    featSymbolFc: () => ({ type: 'FeatureCollection', features: [] }),
    featSymbolIcons: () => [],
    influenceFc: () => ({ type: 'FeatureCollection', features: [] }),
    draftFc: () => ({ type: 'FeatureCollection', features: [] }),
    weaponTrackFc: () => ({ type: 'FeatureCollection', features: [] }),
    selectedFeatureId: null,
    featureEdit: false,
    drawActive: false,
    targeting: false,
    editUnits: false,
    latlngGrid: false,
    mgrsGrid: false,
    gridStepDeg: 0.5,
    queryPoint: null,
    hexMaxRes: 8,
    hexLimitKm: 0,
    dayNight: false,
    timeOfDay: 12,
  },
)

/** 日照視覺（#6）：依一日時間對地圖畫布套 CSS filter（正午亮、夜間暗藍、晨昏偏暖）。 */
function applyDayNight() {
  const canvas = map?.getCanvas()
  if (!canvas) return
  if (!props.dayNight) {
    canvas.style.filter = ''
    return
  }
  const t = props.timeOfDay ?? 12
  const day = Math.max(0, Math.cos(((t - 13) / 24) * 2 * Math.PI)) // 約 13:00 最亮
  const brightness = (0.5 + 0.5 * day).toFixed(2)
  const saturate = (0.65 + 0.35 * day).toFixed(2)
  const dawnDusk = (t >= 5 && t <= 8) || (t >= 16.5 && t <= 20) ? 0.35 : 0 // 晨昏暖色
  canvas.style.filter = `brightness(${brightness}) saturate(${saturate}) sepia(${dawnDusk})`
}

// 可抽換底圖來源（由 runtimeConfig 注入；#2）。
const _cfg = useRuntimeConfig().public
const basemapSources = buildBasemapSources({
  tileUrl: _cfg.tileUrl as string,
  satelliteUrl: _cfg.satelliteUrl as string | undefined,
  basemaps: _cfg.basemaps as ReturnType<typeof buildBasemapSources> | undefined,
  onlineBasemaps: _cfg.onlineBasemaps as boolean,
})
let basemapErrorHandled = false // 每次切底圖重置，避免 404 洪水重複 emit
// style 尚未 load 完成前不得增刪圖層：addSource/addLayer 會丟 "Style is not done loading"，
// 且 getStyle() 回 undefined。此時略過即可——load handler 會以當下最新的 basemapId 再套一次，
// 故載入中的切換不會遺失。**不可改用 map.isStyleLoaded()**：它是「fully loaded」語意，任何來源
// 尚在載瓦片時也回 false（實測加完 source 的下一拍即為 false），會誤擋正常的切底圖。
let styleReady = false

/** 移除現有底圖（raster 'basemap' 或 vector 'basemap-*' 圖層）+ 來源。 */
function removeBasemap() {
  if (!map || !styleReady) return
  const ids = (map.getStyle().layers ?? [])
    .map((l) => l.id)
    .filter((id) => id === 'basemap' || id.startsWith('basemap-'))
  for (const id of ids) map.removeLayer(id)
  if (map.getSource('basemap')) map.removeSource('basemap')
}

/** 套用底圖來源：raster → 單一 raster 層；vector → OpenMapTiles 深色圖層組（皆置於 graticule 之下）。 */
function applyBasemap(id: string) {
  if (!map || !styleReady) return // 見 styleReady 註解：load 後會以最新值再套一次
  basemapErrorHandled = false // 重新武裝回退偵測
  removeBasemap()
  const src = basemapSources.find((s) => s.id === id)
  if (!src || !src.tiles) return // offline 或未知 → 僅背景
  if (src.type === 'raster') {
    map.addSource('basemap', {
      type: 'raster',
      tiles: src.tiles,
      tileSize: src.tileSize ?? 256,
      minzoom: src.minzoom ?? 0,
      maxzoom: src.maxzoom ?? 22,
      attribution: src.attribution ?? '',
    })
    map.addLayer({ id: 'basemap', type: 'raster', source: 'basemap' }, GRAT_SRC)
  } else if (src.type === 'vector') {
    map.addSource('basemap', {
      type: 'vector',
      tiles: src.tiles,
      minzoom: src.minzoom ?? 0,
      maxzoom: src.maxzoom ?? 14,
      attribution: src.attribution ?? '',
    })
    for (const layer of openMapTilesDarkLayers('basemap')) map.addLayer(layer, GRAT_SRC)
  }
  applyOpacity('basemap') // 底圖重建後重套透明度（#9）
}

const NONE = '__matso_none__' // 過濾器 sentinel：無選取時不匹配任何 feature

const container = ref<HTMLDivElement | null>(null)
const loaded = ref(false)
let map: MapLibreMap | null = null
let dragFeatId: string | null = null // 拖放移動中的點特徵 id（#11 B2）
let dragUnitId: string | null = null // 拖放移動中的單位 id（地圖狀態編輯）
// 地圖狀態編輯 · 多選：選取單位集合 + 整組拖曳/框選狀態。
const selectedUnitIds = new Set<string>()
let groupDrag: { start: { lng: number; lat: number }; origs: { id: string; lng: number; lat: number }[] } | null = null
let boxStart: { x: number; y: number } | null = null
let rubberBand: HTMLDivElement | null = null // 框選矩形（螢幕座標 overlay）
// 拖曳事件的結構化型別（避免引入 maplibre 具名事件型別）。
type _LngLatEvt = { lngLat: { lng: number; lat: number } }
type _PointEvt = { point: { x: number; y: number }; lngLat: { lng: number; lat: number }; originalEvent: MouseEvent; preventDefault: () => void }

// 多選高亮環 filter：只畫在 selectedUnitIds 內的單位。
function _multiSelFilter(): unknown[] {
  return ['in', ['get', 'id'], ['literal', Array.from(selectedUnitIds)]]
}
function refreshMultiSelect(): void {
  if (map?.getLayer('unit-multiselect-ring')) {
    map.setFilter('unit-multiselect-ring', _multiSelFilter() as never)
  }
  emit('unitsSelected', { count: selectedUnitIds.size })
}
function clearMultiSelect(): void {
  if (!selectedUnitIds.size) return
  selectedUnitIds.clear()
  refreshMultiSelect()
}
function onFeatDragMove(e: _LngLatEvt): void {
  if (!dragFeatId || !map) return
  ;(map.getSource(FEAT_DRAG_SRC) as GeoJSONSource | undefined)?.setData({
    type: 'Feature',
    properties: {},
    geometry: { type: 'Point', coordinates: [e.lngLat.lng, e.lngLat.lat] },
  } as never)
}
function onFeatDrop(e: _LngLatEvt): void {
  if (!dragFeatId || !map) return
  const id = dragFeatId
  dragFeatId = null
  map.getCanvas().style.cursor = ''
  map.off('mousemove', onFeatDragMove)
  ;(map.getSource(FEAT_DRAG_SRC) as GeoJSONSource | undefined)?.setData(_EMPTY_FEAT_FC as never)
  emit('featureMove', { id, lng: e.lngLat.lng, lat: e.lngLat.lat })
}

// ---- #99 整形（控制點編輯）----
// 頂點多於此數就不畫中點：圓形存放環有 48 個頂點，再加 48 顆中點會糊成一片。
const MAX_MIDPOINT_VERTS = 24
type _RingSel = { id: string; gtype: 'LINE' | 'POLYGON'; ring: number[][] }
// 拖曳中的整形狀態（頂點拖曳／本體平移共用；null＝未在整形）。
let reshape: _RingSel | null = null
let dragVertIdx = -1 // 拖曳中的頂點索引（-1＝在拖本體）
let bodyDragStart: { lng: number; lat: number; ring: number[][] } | null = null

/** 由已渲染的 featureFc 取出選取圖形的開放環（POINT 走既有整點拖曳，回 null）。 */
function selectedRing(): _RingSel | null {
  const id = props.selectedFeatureId
  if (!id) return null
  for (const raw of (props.featureFc?.features ?? []) as unknown[]) {
    const f = raw as { properties?: { id?: unknown }; geometry?: { type?: string; coordinates?: unknown } }
    if (String(f?.properties?.id ?? '') !== String(id)) continue
    const g = f.geometry
    if (g?.type === 'LineString') {
      return { id: String(id), gtype: 'LINE', ring: openRing(g.coordinates as number[][]) }
    }
    if (g?.type === 'Polygon') {
      const ring = ((g.coordinates as number[][][]) ?? [])[0] ?? []
      return { id: String(id), gtype: 'POLYGON', ring: openRing(ring) }
    }
    return null
  }
  return null
}

/** 控制點 GeoJSON：實頂點（mid=false）+ 邊中點（mid=true，拖了就插新頂點）。 */
function vertexFc(sel: _RingSel | null): Fc {
  if (!sel || !props.featureEdit || props.drawActive) return _EMPTY_FEAT_FC
  const feats: unknown[] = sel.ring.map((c, i) => ({
    type: 'Feature',
    properties: { i, mid: false },
    geometry: { type: 'Point', coordinates: c },
  }))
  if (sel.ring.length <= MAX_MIDPOINT_VERTS) {
    for (const m of midpoints(sel.ring, sel.gtype === 'POLYGON')) {
      feats.push({
        type: 'Feature',
        properties: { i: m.i, mid: true },
        geometry: { type: 'Point', coordinates: m.pt },
      })
    }
  }
  return { type: 'FeatureCollection', features: feats }
}

function syncVertices(): void {
  ;(map?.getSource(VERT_SRC) as GeoJSONSource | undefined)?.setData(
    vertexFc(reshape ?? selectedRing()) as never,
  )
}

/**
 * 整形拖曳中的即時預覽：只覆寫該圖形的幾何，其餘照 props 原樣。
 * 放開後由上層 PATCH→重載，props 一變 syncFeatures 就以權威幾何收斂；PATCH 失敗時上層亦重載，
 * 故不會停在拖歪的形狀上。
 */
function previewReshape(): void {
  const src = map?.getSource(FEAT_SRC) as GeoJSONSource | undefined
  const rs = reshape
  if (!src || !rs) return
  const feats = ((props.featureFc?.features ?? []) as unknown[]).map((raw) => {
    const f = raw as { properties?: { id?: unknown } }
    if (String(f?.properties?.id ?? '') !== rs.id) return raw
    const geometry =
      rs.gtype === 'POLYGON'
        ? { type: 'Polygon', coordinates: [[...rs.ring, rs.ring[0]!]] }
        : { type: 'LineString', coordinates: rs.ring }
    return { ...(raw as object), geometry }
  })
  src.setData({ type: 'FeatureCollection', features: feats } as never)
}

/** 結束整形：還原游標/事件，把新幾何交給上層（存放格式＝開放環）。重入安全（rs 為 null 即不 emit）。 */
function endReshape(): void {
  const rs = reshape
  reshape = null
  dragVertIdx = -1
  bodyDragStart = null
  if (map) map.getCanvas().style.cursor = ''
  map?.off('mousemove', onVertexDragMove)
  map?.off('mousemove', onBodyDragMove)
  window.removeEventListener('mouseup', endReshape)
  if (rs) emit('featureReshape', { id: rs.id, geometry: rs.ring })
}

/**
 * 保險絲：`map.once('mouseup')` 只收得到「canvas 容器上」的放開。
 * 浮動小工具就疊在地圖上，把控制點拖到工具視窗上方才放手是很常見的——
 * 沒有這條 window 級 fallback，整形狀態會卡住、圖形之後一直跟著游標跑。
 * 容器上的放開會冒泡到 window，故正常情況兩者都觸發，由 endReshape 的重入保護吸收。
 */
function armReshapeFallback(): void {
  window.addEventListener('mouseup', endReshape)
}

function onVertexDragMove(e: _LngLatEvt): void {
  if (!reshape || dragVertIdx < 0) return
  reshape = { ...reshape, ring: moveVertex(reshape.ring, dragVertIdx, [e.lngLat.lng, e.lngLat.lat]) }
  previewReshape()
  syncVertices()
}
function onVertexDrop(e: _LngLatEvt): void {
  if (!reshape) return
  onVertexDragMove(e) // 以放開處為最終位置
  endReshape()
}
function onBodyDragMove(e: _LngLatEvt): void {
  if (!reshape || !bodyDragStart) return
  const d = bodyDragStart
  reshape = { ...reshape, ring: translateRing(d.ring, e.lngLat.lng - d.lng, e.lngLat.lat - d.lat) }
  previewReshape()
  syncVertices()
}
function onBodyDrop(e: _LngLatEvt): void {
  if (!reshape) return
  onBodyDragMove(e)
  endReshape()
}

const HEX_SRC = 'hexgrid'
const GRAT_SRC = 'graticule'
const HILLSHADE_SRC = 'hillshade'
const CONTOUR_SRC = 'contours'
const UNITS_SRC = 'units'
const DEST_SRC = 'move-dest'
const MOVE_PATH_SRC = 'move-path' // #28 移動路徑預覽（線 + 強穿標記）
const TRACK_SRC = 'weapon-tracks' // #95 武器軌跡（短暫顯示後淡出）
const FEAT_SRC = 'mapfeatures' // 標註/工事幾何（stage ③b）
const FEAT_SYM_SRC = 'mapfeatsym' // 帶北約符號的點特徵（#11）
const FEAT_DRAG_SRC = 'mapfeatdrag' // 拖放移動預覽（#11 B2）
const VERT_SRC = 'mapfeatverts' // #99 選取圖形的控制點（頂點 + 邊中點）
const INFL_SRC = 'mapinfluence' // 影響範圍圓
const DRAFT_SRC = 'mapdraft' // 繪製中草稿
const FEAT_NONE = '__matso_feat_none__'
const GRIDL_SRC = 'coordgrid-lines' // 經緯度網格線（#9）
const GRIDT_SRC = 'coordgrid-labels' // 經緯度標籤
const MGRS_SRC = 'mgrs-labels' // MGRS 標記
const QUERY_SRC = 'coord-query' // 座標查詢點（#10）
let hasGlyphs = false // tileUrl 存在時才有字型 → 才畫標籤

/** 依視野 bbox + 密度重建座標網格（#9），moveend + 開關/密度變更時呼叫。 */
function refreshGrid() {
  if (!map) return
  const b = map.getBounds()
  const bounds = { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() }
  const step = props.gridStepDeg && props.gridStepDeg > 0 ? props.gridStepDeg : 0.5
  const ll = props.latlngGrid ? buildLatLngGrid(bounds, step) : { lines: EMPTY_FC, labels: EMPTY_FC }
  ;(map.getSource(GRIDL_SRC) as GeoJSONSource | undefined)?.setData(ll.lines as never)
  ;(map.getSource(GRIDT_SRC) as GeoJSONSource | undefined)?.setData(ll.labels as never)
  const mgrs = props.mgrsGrid ? buildMgrsLabels(bounds, step, 4) : EMPTY_FC
  ;(map.getSource(MGRS_SRC) as GeoJSONSource | undefined)?.setData(mgrs as never)
}

function syncQuery() {
  const p = props.queryPoint
  const fc = p
    ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [p.lng, p.lat] } }] }
    : EMPTY_FC
  ;(map?.getSource(QUERY_SRC) as GeoJSONSource | undefined)?.setData(fc as never)
}

function syncFeatures() {
  ;(map?.getSource(FEAT_SRC) as GeoJSONSource | undefined)?.setData((props.featureFc ?? _EMPTY_FEAT_FC) as never)
  ;(map?.getSource(INFL_SRC) as GeoJSONSource | undefined)?.setData((props.influenceFc ?? _EMPTY_FEAT_FC) as never)
  ;(map?.getSource(DRAFT_SRC) as GeoJSONSource | undefined)?.setData((props.draftFc ?? _EMPTY_FEAT_FC) as never)
  ;(map?.getSource(TRACK_SRC) as GeoJSONSource | undefined)?.setData((props.weaponTrackFc ?? _EMPTY_FEAT_FC) as never)
  syncVertices() // #99 幾何/選取變動 → 控制點跟著重算
  // 北約符號點特徵（#11）：生成/快取 milsymbol icon（去重）→ setData。
  if (map) {
    for (const spec of props.featSymbolIcons ?? []) {
      if (map.hasImage(spec.key)) continue
      const img = symbolImage(spec.key, spec.sidc, {})
      if (img) {
        try {
          map.addImage(spec.key, img)
        } catch {
          /* skip 壞 icon */
        }
      }
    }
    ;(map.getSource(FEAT_SYM_SRC) as GeoJSONSource | undefined)?.setData(
      (props.featSymbolFc ?? _EMPTY_FEAT_FC) as never,
    )
  }
}

const EMPTY_FC = { type: 'FeatureCollection' as const, features: [] }

/** MOVE 目的地標記：精確移動（destPoint）→ 只畫精確點；否則→ 吸附六角格 + 格心（#4b/#2）。 */
function destFeatures(
  h3: string | null,
  point: { lng: number; lat: number } | null,
): { type: 'FeatureCollection'; features: unknown[] } {
  if (point) {
    // 精確移動：只畫精確落點（粉色，見 move-dest-center），不畫吸附格。
    return {
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [point.lng, point.lat] } },
      ],
    }
  }
  if (!h3) return EMPTY_FC
  const ring = cellToBoundary(h3, true) // [lng,lat][]
  const [clat, clng] = cellToLatLng(h3)
  return {
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[...ring, ring[0]!]] } },
      { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [clng, clat] } },
    ],
  }
}

function syncDest() {
  const src = map?.getSource(DEST_SRC) as GeoJSONSource | undefined
  src?.setData(destFeatures(props.destH3, props.destPoint ?? null) as never)
}

/** #28 移動路徑預覽：畫規劃路線（forced→琥珀虛線）+ 強穿標記點。 */
function syncMovePath() {
  if (!map) return
  const src = map.getSource(MOVE_PATH_SRC) as GeoJSONSource | undefined
  if (!src) return
  const path = props.movePath ?? []
  const feats: unknown[] = []
  if (path.length >= 2) {
    feats.push({
      type: 'Feature',
      properties: { forced: !!props.moveForced },
      geometry: { type: 'LineString', coordinates: path },
    })
  }
  for (const c of props.moveCrossings ?? []) {
    feats.push({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: c } })
  }
  src.setData({ type: 'FeatureCollection', features: feats } as never)
  // 強穿→琥珀虛線警示；可行→青實線。
  if (map.getLayer('move-path-line')) {
    map.setPaintProperty('move-path-line', 'line-color', props.moveForced ? '#f59e0b' : '#38bdf8')
    map.setPaintProperty(
      'move-path-line',
      'line-dasharray',
      props.moveForced ? [2, 1.4] : [1, 0],
    )
  }
}

// ---- 圖層透明度 + 套疊順序（#9）----
const PIN_TOP = 'move-dest-fill' // 疊加層維持在此之下（目的標記/血量環/選取環/單位釘在最上）

/** 解析圖層群 → maplibre 子層（basemap 依當前來源型別動態）。 */
function groupMembers(key: string) {
  if (key === 'basemap') {
    const src = basemapSources.find((s) => s.id === props.basemapId)
    return basemapOpacityMembers((src?.type as 'raster' | 'vector' | 'offline') ?? 'offline')
  }
  return OVERLAY_LAYER_GROUPS.find((g) => g.key === key)?.members ?? []
}

/** 套用單一群透明度＝各子層 base × 乘數（預設 1）。 */
function applyOpacity(key: string) {
  if (!map) return
  const factor = props.layerOpacity?.[key] ?? 1
  for (const m of groupMembers(key)) {
    if (map.getLayer(m.id)) map.setPaintProperty(m.id, m.prop, Number((m.base * factor).toFixed(3)))
  }
}
function applyAllOpacity() {
  for (const k of ['basemap', 'hillshade', 'contour', 'hex']) applyOpacity(k)
}

/** 依 layerOrder（上→下）重排疊加層，全維持於 PIN_TOP 之下。 */
function applyOrder() {
  if (!map || !map.getLayer(PIN_TOP)) return
  const order = props.layerOrder?.length ? props.layerOrder : DEFAULT_OVERLAY_ORDER
  for (const key of [...order].reverse()) {
    // 由最下群開始，逐一插到 PIN_TOP 之下 → 陣列末端（最上群）最後插＝最上層。
    for (const m of OVERLAY_LAYER_GROUPS.find((g) => g.key === key)?.members ?? []) {
      if (map.getLayer(m.id)) map.moveLayer(m.id, PIN_TOP)
    }
  }
}

/** 主/次等高線 filter：elev 為 major/minor 倍數（minor 排除 major 倍數避免疊線，#8）。 */
function contourFilter(which: 'major' | 'minor'): FilterSpecification {
  const major = Math.max(1, Math.round(props.contourMajor ?? 100))
  const minor = Math.max(1, Math.round(props.contourMinor ?? 50))
  const elev = ['to-number', ['get', 'elev']]
  if (which === 'major') return ['==', ['%', elev, major], 0] as unknown as FilterSpecification
  return ['all', ['==', ['%', elev, minor], 0], ['!=', ['%', elev, major], 0]] as unknown as FilterSpecification
}
function applyContourFilters() {
  if (map?.getLayer('contours-line-major')) map.setFilter('contours-line-major', contourFilter('major'))
  if (map?.getLayer('contours-line-minor')) map.setFilter('contours-line-minor', contourFilter('minor'))
  if (map?.getLayer('contours-label')) map.setFilter('contours-label', contourFilter('major'))
}
/** 線條粗細（#5）：六角網格 + 主/次等高線線寬即時套用。 */
function applyLineStyles() {
  if (!map) return
  const setW = (id: string, w: number) => {
    if (map?.getLayer(id)) map.setPaintProperty(id, 'line-width', Math.max(0.1, w))
  }
  const setC = (id: string, c: string) => {
    if (map?.getLayer(id)) map.setPaintProperty(id, 'line-color', c)
  }
  const setTC = (id: string, c: string) => {
    if (map?.getLayer(id)) map.setPaintProperty(id, 'text-color', c)
  }
  setW('hexgrid-line', props.hexLineWidth ?? 0.5)
  setW('contours-line-major', props.contourMajorWidth ?? 1.2)
  setW('contours-line-minor', props.contourMinorWidth ?? 0.5)
  setW('coordgrid-line', props.gridWidth ?? 0.5)
  // #22 顏色（等高線主/次共用同色，靠 line-opacity 分粗細層次）。
  setC('hexgrid-line', props.hexLineColor ?? '#38bdf8')
  setC('contours-line-major', props.contourColor ?? '#c9a15c')
  setC('contours-line-minor', props.contourColor ?? '#c9a15c')
  setC('coordgrid-line', props.gridColor ?? '#5b7fa6')
  setTC('coordgrid-label', props.gridColor ?? '#5b7fa6')
  setTC('mgrs-label', props.mgrsColor ?? '#facc15')
}

function refreshHex() {
  if (!map) return
  const b = map.getBounds()
  const c = map.getCenter()
  const limitKm = props.hexLimitKm ?? 0
  const fc = hexCellsForBounds(
    { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() },
    map.getZoom(),
    {
      maxRes: props.hexMaxRes ?? 8,
      limit: limitKm > 0 ? { lng: c.lng, lat: c.lat, radiusKm: limitKm } : undefined,
    },
  )
  const src = map.getSource(HEX_SRC) as GeoJSONSource | undefined
  src?.setData(fc)
}

function setLayerVisibility(id: string, visible: boolean) {
  if (map?.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
}

/** 選取拖曳用：所有單位當前座標（我方 + 敵情 contact）——編輯模式白軍可調任一陣營單位。 */
function allUnitPositions(): { id: string; lng: number; lat: number }[] {
  const out: { id: string; lng: number; lat: number }[] = []
  for (const u of props.ownUnits) out.push({ id: u.id, lng: u.lng, lat: u.lat })
  for (const c of props.contacts) out.push({ id: c.contactId, lng: c.lng, lat: c.lat })
  return out
}

/** 依 props 的單位/contact 重建 symbol 特徵：生成/快取 milsymbol icon（去重 addImage）→ setData。 */
function syncUnits(posOverride?: Map<string, { lng: number; lat: number }>) {
  if (!map) return
  // 整組拖曳即時跟隨：以覆寫座標重建，讓真圖標 + 高亮環跟著游標移動（暫停中無 STATE_DIFF 覆蓋）。
  // 我方 + 敵情 contact 皆套覆寫（編輯模式可同時拖曳兩陣營單位）。
  const ov = posOverride && posOverride.size ? posOverride : null
  const own = ov
    ? props.ownUnits.map((u) => {
        const o = ov.get(u.id)
        return o ? { ...u, lng: o.lng, lat: o.lat } : u
      })
    : props.ownUnits
  const contacts = ov
    ? props.contacts.map((c) => {
        const o = ov.get(c.contactId)
        return o ? { ...c, lng: o.lng, lat: o.lat } : c
      })
    : props.contacts
  const { collection, icons } = buildUnitFeatures(own, contacts, props.currentTick)
  for (const spec of icons) {
    if (map.hasImage(spec.key)) continue
    const img = symbolImage(spec.key, spec.sidc, spec.options)
    if (!img) continue
    try {
      map.addImage(spec.key, img) // 單一壞 icon 不應中斷整批（symbol 層會略過缺圖特徵）
    } catch {
      /* skip */
    }
  }
  const src = map.getSource(UNITS_SRC) as GeoJSONSource | undefined
  src?.setData(collection)
}

onMounted(async () => {
  if (!container.value) return
  const { Map: MapCtor, NavigationControl, ScaleControl } = await import('maplibre-gl')
  const tileUrl = useRuntimeConfig().public.tileUrl as string

  map = new MapCtor({
    container: container.value,
    // tileUrl 存在時掛上本地字型 glyphs（供標籤：MGRS/經緯格網/等高線高度）。
    style: buildOfflineStyle(tileUrl ? `${tileUrl}/fonts/{fontstack}/{range}.pbf` : undefined),
    center: TAIWAN_CENTER,
    zoom: DEFAULT_ZOOM,
    attributionControl: false,
  })
  ;(window as unknown as { __matsoMap?: MapLibreMap }).__matsoMap = map
  // 縮放 + 指北針（top-left，避開右上 LayerToggles）+ 比例尺（bottom-right）。
  map.addControl(new NavigationControl({ showZoom: true, showCompass: true, visualizePitch: true }), 'top-left')
  map.addControl(new ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-right')
  // 底圖瓦片載入失敗（如街道 tileserver 不可達）→ emit 一次，供上層回退離線格線。
  map.on('error', (e) => {
    const err = e as { error?: Error; sourceId?: string }
    console.error('MAPERR', err.error?.message)
    if (err.sourceId === 'basemap' && !basemapErrorHandled) {
      basemapErrorHandled = true
      emit('basemapError', { id: props.basemapId })
    }
  })

  map.on('load', () => {
    if (!map) return
    styleReady = true // 自此可安全增刪圖層；下方 applyBasemap 會補套載入中被略過的切換
    map.addSource(GRAT_SRC, { type: 'geojson', data: buildGraticule() })
    map.addLayer({
      id: 'graticule',
      type: 'line',
      source: GRAT_SRC,
      paint: { 'line-color': '#33608f', 'line-width': 0.8, 'line-opacity': 0.8 },
    })
    applyBasemap(props.basemapId) // 底圖置於 graticule 之下（可抽換，#2）
    if (tileUrl) {
      map.addSource(HILLSHADE_SRC, {
        type: 'raster',
        tiles: [`${tileUrl}/data/hillshade/{z}/{x}/{y}.png`],
        tileSize: 256,
      })
      map.addLayer({
        id: 'hillshade',
        type: 'raster',
        source: HILLSHADE_SRC,
        layout: { visibility: props.hillshadeVisible ? 'visible' : 'none' },
        paint: { 'raster-opacity': 0.5 },
      })
      // 等高線（#3）——tileserver 服務 gdal_contour 產的向量瓦片（source-layer=contour）。
      // minzoom 9：等高線僅於戰術縮放顯示，避免低縮放時載入 25m 密集等高線的巨大 overview 瓦片。
      map.addSource(CONTOUR_SRC, {
        type: 'vector',
        tiles: [`${tileUrl}/data/contours/{z}/{x}/{y}.pbf`],
        minzoom: 9,
        maxzoom: 14,
      })
      // 次等高線（細）先加 → 主等高線（粗）疊其上（#8，間距可設定；filter 依 elev%interval）。
      map.addLayer({
        id: 'contours-line-minor',
        type: 'line',
        source: CONTOUR_SRC,
        'source-layer': 'contour',
        layout: { visibility: props.contourVisible ? 'visible' : 'none' },
        filter: contourFilter('minor'),
        paint: { 'line-color': props.contourColor, 'line-width': props.contourMinorWidth, 'line-opacity': 0.5 },
      })
      map.addLayer({
        id: 'contours-line-major',
        type: 'line',
        source: CONTOUR_SRC,
        'source-layer': 'contour',
        layout: { visibility: props.contourVisible ? 'visible' : 'none' },
        filter: contourFilter('major'),
        paint: { 'line-color': props.contourColor, 'line-width': props.contourMajorWidth, 'line-opacity': 0.8 },
      })
      // 等高線高度標籤（#11，沿線放置；主等高線；需 glyphs）。
      if (tileUrl) {
        map.addLayer({
          id: 'contours-label',
          type: 'symbol',
          source: CONTOUR_SRC,
          'source-layer': 'contour',
          layout: {
            visibility: props.contourVisible ? 'visible' : 'none',
            'symbol-placement': 'line',
            'text-field': ['concat', ['to-string', ['get', 'elev']], ' m'],
            'text-font': ['Noto Sans Regular'],
            'text-size': 10,
            'symbol-spacing': 300,
          },
          filter: contourFilter('major'),
          paint: { 'text-color': '#e6c88a', 'text-halo-color': '#0a1626', 'text-halo-width': 1.4 },
        })
      }
    }
    map.addSource(HEX_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
    map.addLayer({
      id: 'hexgrid-fill',
      type: 'fill',
      source: HEX_SRC,
      layout: { visibility: props.hexVisible ? 'visible' : 'none' },
      paint: { 'fill-color': '#38bdf8', 'fill-opacity': 0.06 },
    })
    map.addLayer({
      id: 'hexgrid-line',
      type: 'line',
      source: HEX_SRC,
      layout: { visibility: props.hexVisible ? 'visible' : 'none' },
      paint: { 'line-color': props.hexLineColor, 'line-width': props.hexLineWidth, 'line-opacity': 0.5 },
    })
    // 座標網格（#9）：經緯度網格線 + 標籤 + MGRS 標記（標籤需 glyphs → 僅 tileUrl 時加）。
    hasGlyphs = !!tileUrl
    map.addSource(GRIDL_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'coordgrid-line',
      type: 'line',
      source: GRIDL_SRC,
      paint: { 'line-color': props.gridColor, 'line-width': props.gridWidth, 'line-opacity': 0.6 },
    })
    map.addSource(GRIDT_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addSource(MGRS_SRC, { type: 'geojson', data: EMPTY_FC })
    if (hasGlyphs) {
      map.addLayer({
        id: 'coordgrid-label',
        type: 'symbol',
        source: GRIDT_SRC,
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'text-allow-overlap': false,
        },
        paint: { 'text-color': props.gridColor, 'text-halo-color': '#0a1626', 'text-halo-width': 1.4 },
      })
      map.addLayer({
        id: 'mgrs-label',
        type: 'symbol',
        source: MGRS_SRC,
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 10,
          'text-allow-overlap': false,
        },
        paint: { 'text-color': props.mgrsColor, 'text-halo-color': '#0a1626', 'text-halo-width': 1.4 },
      })
    }
    // 座標查詢點（#10）：十字標記。
    map.addSource(QUERY_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'coord-query-pt',
      type: 'circle',
      source: QUERY_SRC,
      paint: {
        'circle-radius': 5,
        'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-color': '#f472b6',
        'circle-stroke-width': 2.5,
      },
    })
    // 地圖標註/工事（stage ③b）：影響範圍圓（最下）→ 面/線/點 → 選取高亮 → 草稿。
    map.addSource(INFL_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'mapinfluence-fill',
      type: 'fill',
      source: INFL_SRC,
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.1 },
    })
    map.addSource(FEAT_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'mapfeat-fill',
      type: 'fill',
      source: FEAT_SRC,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.18 },
    })
    map.addLayer({
      id: 'mapfeat-line',
      type: 'line',
      source: FEAT_SRC,
      filter: ['match', ['geometry-type'], ['LineString', 'Polygon'], true, false],
      // #96 線寬資料驅動（attributes.width；缺值由 featureLineWidth 給預設 2）。
      paint: { 'line-color': ['get', 'color'], 'line-width': ['get', 'width'] },
    })
    map.addLayer({
      id: 'mapfeat-point',
      type: 'circle',
      source: FEAT_SRC,
      // 帶北約符號的點改由符號層渲染，此處只畫無符號的圓點（#11）。
      filter: ['all', ['==', ['geometry-type'], 'Point'], ['!=', ['get', 'hasSym'], true]],
      paint: {
        'circle-radius': 6,
        'circle-color': ['get', 'color'],
        'circle-stroke-color': '#0a1626',
        'circle-stroke-width': 1.5,
      },
    })
    // 北約符號點特徵（#11）：milsymbol icon（資料驅動 icon-image）。
    map.addSource(FEAT_SYM_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'mapfeat-symbol',
      type: 'symbol',
      source: FEAT_SYM_SRC,
      layout: {
        'icon-image': ['get', 'icon'],
        'icon-size': 1, // #24 與單位符號同尺寸（原 0.5 太小）
        'icon-allow-overlap': true,
      },
    })
    // 拖放移動預覽（#11 B2）：拖曳中在游標處顯示白環。
    map.addSource(FEAT_DRAG_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'mapfeat-drag',
      type: 'circle',
      source: FEAT_DRAG_SRC,
      paint: {
        'circle-radius': 8,
        'circle-color': 'rgba(255,255,255,0.25)',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    })
    // 選取高亮（線/面白外框 + 點白環）。
    map.addLayer({
      id: 'mapfeat-sel-line',
      type: 'line',
      source: FEAT_SRC,
      filter: ['==', ['get', 'id'], props.selectedFeatureId ?? FEAT_NONE],
      paint: { 'line-color': '#ffffff', 'line-width': 3, 'line-opacity': 0.9 },
    })
    map.addLayer({
      id: 'mapfeat-sel-point',
      type: 'circle',
      source: FEAT_SRC,
      filter: [
        'all',
        ['==', ['geometry-type'], 'Point'],
        ['==', ['get', 'id'], props.selectedFeatureId ?? FEAT_NONE],
      ],
      paint: {
        'circle-radius': 10,
        'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2.5,
      },
    })
    // #99 控制點：中點（空心小點＝可拖出新頂點）在下，實頂點（實心白方點）在上。
    map.addSource(VERT_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'mapfeat-midpoint',
      type: 'circle',
      source: VERT_SRC,
      filter: ['==', ['get', 'mid'], true],
      paint: {
        'circle-radius': 4,
        'circle-color': 'rgba(255,255,255,0.35)',
        'circle-stroke-color': '#22d3ee',
        'circle-stroke-width': 1.5,
      },
    })
    map.addLayer({
      id: 'mapfeat-vertex',
      type: 'circle',
      source: VERT_SRC,
      filter: ['!=', ['get', 'mid'], true],
      paint: {
        'circle-radius': 6,
        'circle-color': '#ffffff',
        'circle-stroke-color': '#0ea5e9',
        'circle-stroke-width': 2,
      },
    })
    // 特徵名稱標籤（#11；需 glyphs → 僅 tileUrl 時加）。
    if (hasGlyphs) {
      map.addLayer({
        id: 'mapfeat-label',
        type: 'symbol',
        source: FEAT_SRC,
        filter: ['!=', ['get', 'label'], ''],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'text-offset': [0, 0.9],
          'text-anchor': 'top',
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': ['get', 'color'],
          'text-halo-color': '#0a1626',
          'text-halo-width': 1.4,
        },
      })
    }
    // 繪製中草稿（amber）。
    map.addSource(DRAFT_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'mapdraft-fill',
      type: 'fill',
      source: DRAFT_SRC,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': '#eab308', 'fill-opacity': 0.15 },
    })
    map.addLayer({
      id: 'mapdraft-line',
      type: 'line',
      source: DRAFT_SRC,
      filter: ['==', ['geometry-type'], 'LineString'],
      paint: { 'line-color': '#facc15', 'line-width': 2, 'line-dasharray': [2, 1] },
    })
    map.addLayer({
      id: 'mapdraft-point',
      type: 'circle',
      source: DRAFT_SRC,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: { 'circle-radius': 4, 'circle-color': '#facc15', 'circle-stroke-color': '#0a1626', 'circle-stroke-width': 1 },
    })
    // MOVE 目的格（#4b）：吸附後的六角格 + 格心，讓使用者看見單位「實際會到的位置」。
    map.addSource(DEST_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'move-dest-fill',
      type: 'fill',
      source: DEST_SRC,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': '#eab308', 'fill-opacity': 0.14 },
    })
    map.addLayer({
      id: 'move-dest-line',
      type: 'line',
      source: DEST_SRC,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'line-color': '#facc15', 'line-width': 1.6, 'line-dasharray': [2, 1] },
    })
    map.addLayer({
      id: 'move-dest-center',
      type: 'circle',
      source: DEST_SRC,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 4,
        'circle-color': '#facc15',
        'circle-stroke-color': '#0a1626',
        'circle-stroke-width': 1.5,
      },
    })
    // #28 移動路徑預覽：規劃路線（可行＝青、強穿＝琥珀虛線）+ 強穿標記。
    map.addSource(MOVE_PATH_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'move-path-line',
      type: 'line',
      source: MOVE_PATH_SRC,
      filter: ['==', ['geometry-type'], 'LineString'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': '#38bdf8',
        'line-width': 2.4,
        'line-opacity': 0.9,
      },
    })
    // #95 武器軌跡：射手→目標直線，HIT 亮橘、MISS 灰藍虛線；透明度由 feature 帶（淡出）。
    // **純顯示**：由後端已裁決的 ENGAGEMENT_RESOLVED 驅動，不參與任何判定。
    map.addSource(TRACK_SRC, { type: 'geojson', data: EMPTY_FC })
    map.addLayer({
      id: 'weapon-track',
      type: 'line',
      source: TRACK_SRC,
      layout: { 'line-cap': 'round' },
      paint: {
        'line-color': ['match', ['get', 'status'], 'HIT', '#fb923c', '#94a3b8'],
        'line-width': ['match', ['get', 'status'], 'HIT', 2.6, 1.4],
        'line-opacity': ['get', 'opacity'],
        'line-dasharray': ['match', ['get', 'status'], 'HIT', ['literal', [1, 0]], ['literal', [2, 2]]],
      },
    })
    map.addLayer({
      id: 'move-path-cross',
      type: 'circle',
      source: MOVE_PATH_SRC,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 6,
        'circle-color': '#ef4444',
        'circle-stroke-color': '#fef2f2',
        'circle-stroke-width': 1.6,
        'circle-opacity': 0.95,
      },
    })
    // 單位 symbol 層（milsymbol icon；資料驅動 icon-image + icon-opacity）
    map.addSource(UNITS_SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
    // 高亮環（選取＝藍、目標＝紅）——置於 symbol 層下方，繞在單位符號外圈；以 filter 依 id 只顯示一個。
    map.addLayer({
      id: 'unit-selected-ring',
      type: 'circle',
      source: UNITS_SRC,
      filter: ['==', ['get', 'id'], props.selectedId ?? NONE],
      paint: {
        'circle-radius': 20,
        'circle-color': 'rgba(59,130,246,0.12)',
        'circle-stroke-color': '#3b82f6',
        'circle-stroke-width': 3,
      },
    })
    map.addLayer({
      id: 'unit-target-ring',
      type: 'circle',
      source: UNITS_SRC,
      filter: ['==', ['get', 'id'], props.targetId ?? NONE],
      paint: {
        'circle-radius': 20,
        'circle-color': 'rgba(239,68,68,0.14)',
        'circle-stroke-color': '#ef4444',
        'circle-stroke-width': 3,
      },
    })
    // 血量環（#5）：我方單位常駐，環色依血量帶（綠/琥珀/紅）。contact 無血量（fog of war）→ 不畫。
    map.addLayer({
      id: 'unit-health-ring',
      type: 'circle',
      source: UNITS_SRC,
      filter: ['all', ['==', ['get', 'kind'], 'own'], ['has', 'health']],
      minzoom: 8,
      paint: {
        'circle-radius': 15,
        'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-width': 2.5,
        'circle-stroke-opacity': 0.9,
        'circle-stroke-color': [
          'step', ['to-number', ['get', 'health']],
          '#ef4444', 34, '#f59e0b', 67, '#22c55e',
        ],
      },
    })
    // 地圖狀態編輯 · 多選高亮環（青色）：繞在被 Shift 多選/框選的單位外圈，置於符號層下方。
    map.addLayer({
      id: 'unit-multiselect-ring',
      type: 'circle',
      source: UNITS_SRC,
      filter: _multiSelFilter() as never,
      paint: {
        'circle-radius': 18,
        'circle-color': 'rgba(34,211,238,0.14)',
        'circle-stroke-color': '#22d3ee',
        'circle-stroke-width': 2.5,
      },
    })
    map.addLayer({
      id: 'units',
      type: 'symbol',
      source: UNITS_SRC,
      layout: {
        'icon-image': ['get', 'icon'],
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
      paint: { 'icon-opacity': ['get', 'opacity'] },
    })
    // 血條（#94）：圖標上方一條，離線免 glyphs（同鎖頭徽章的做法，見 useMilsymbol）。
    // 我方/友軍常駐；contact 僅在後端有給血量時才有（未偵獲細節時不會有 hpIcon → 不畫）。
    for (let pct = 0; pct <= 100; pct += HP_BAR_STEP) {
      const id = `${HP_BAR_PREFIX}${pct}`
      if (map.hasImage(id)) continue
      const img = hpBarImage(pct)
      if (img) map.addImage(id, img, { pixelRatio: HP_BAR_PIXEL_RATIO })
    }
    map.addLayer({
      id: 'unit-hp-bar',
      type: 'symbol',
      source: UNITS_SRC,
      filter: ['has', 'hpIcon'],
      minzoom: 8, // 與血量環同步：拉遠時單位密集，血條會糊成一片
      layout: {
        'icon-image': ['get', 'hpIcon'],
        'icon-offset': [0, -26], // 置於符號上方（符號 24px，pixelRatio 2）
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
      paint: { 'icon-opacity': ['get', 'opacity'] },
    })
    // 固定單位鎖頭徽章（指揮部等）：canvas 生成 ImageData（離線免 glyphs），疊在符號右上角。
    // 只我方（kind='own' + fixed）——不洩漏敵方編成（fog of war）。
    const lockImg = lockBadgeImage()
    if (lockImg && !map.hasImage(LOCK_BADGE_ID)) {
      map.addImage(LOCK_BADGE_ID, lockImg, { pixelRatio: LOCK_BADGE_PIXEL_RATIO })
    }
    map.addLayer({
      id: 'unit-fixed-lock',
      type: 'symbol',
      source: UNITS_SRC,
      filter: ['all', ['==', ['get', 'kind'], 'own'], ['==', ['get', 'fixed'], true]],
      layout: {
        'icon-image': LOCK_BADGE_ID,
        'icon-offset': [10, -10], // 右上角（icon 座標；正 y 朝下）
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
      paint: { 'icon-opacity': ['get', 'opacity'] },
    })
    refreshHex()
    syncUnits()
    syncDest()
    syncMovePath() // #28 移動路徑預覽
    syncFeatures() // stage ③b 標註/工事
    refreshGrid() // #9 座標網格
    syncQuery() // #10 查詢點
    applyDayNight() // #6 日照視覺
    applyAllOpacity() // #9 初始透明度
    applyOrder() // #9 初始套疊順序
    // 修正水合競態：地圖若於容器尺寸未定時初始化，canvas 會停在 400×300 預設（畫面空白）。
    // 載入完成後強制重量測，並延遲再測一次以捕捉版面在下一幀才穩定的情況。
    map.resize()
    requestAnimationFrame(() => map?.resize())
    setTimeout(() => map?.resize(), 300)
    loaded.value = true
    ;(window as unknown as { __matsoMap?: MapLibreMap }).__matsoMap = map
  })

  map.on('moveend', refreshHex)
  map.on('moveend', refreshGrid)
  // 繪圖模式 → 每次點擊都當加頂點（mapClick）；否則：點單位→unitClick、點標註→featureClick、點空白→mapClick。
  map.on('click', (e) => {
    if (props.drawActive) {
      emit('mapClick', { lng: e.lngLat.lng, lat: e.lngLat.lat, h3: latLngToCell(e.lngLat.lat, e.lngLat.lng, 8) })
      return
    }
    const hit = map?.queryRenderedFeatures(e.point, { layers: ['units'] })?.[0]
    const p = hit?.properties
    if (p && p.id != null) {
      emit('unitClick', { id: String(p.id), faction: String(p.faction ?? ''), kind: String(p.kind ?? '') })
      return
    }
    const featLayers = ['mapfeat-point', 'mapfeat-line', 'mapfeat-fill'].filter((l) => map?.getLayer(l))
    const fhit = featLayers.length ? map?.queryRenderedFeatures(e.point, { layers: featLayers })?.[0] : undefined
    if (fhit?.properties?.id != null) {
      emit('featureClick', { id: String(fhit.properties.id) })
      return
    }
    // 編輯模式下點空白（未按 Shift）→ 清空多選。
    if (props.editUnits && !e.originalEvent.shiftKey) clearMultiSelect()
    emit('mapClick', { lng: e.lngLat.lng, lat: e.lngLat.lat, h3: latLngToCell(e.lngLat.lat, e.lngLat.lng, 8) })
  })
  // 滑過單位符號時游標變手指（可點示意）；設定目標中則維持十字準星（#3）。
  map.on('mouseenter', 'units', () => { if (map && !props.targeting) map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', 'units', () => { if (map && !props.targeting) map.getCanvas().style.cursor = '' })
  // Unit 資訊卡懸浮：地圖移動/縮放時即時更新選取單位的螢幕座標（#Fix C）。
  map.on('move', emitSelectPos)
  // 拖放移動點特徵（#11 B2）：在選取的點特徵上按下 → 拖曳 → 放開，emit 新座標由上層 PATCH。
  // **不可在這裡 filter getLayer**（#99 修）：本區在 onMounted 同步跑，而圖層是在 `map.on('load')`
  // 裡才加的——註冊當下 getLayer 一律 undefined，過濾完就是空陣列，等於這兩層的 mousedown
  // **從來沒被註冊過**（實測 _delegatedListeners.mousedown 只有 units/fill/line，點特徵拖不動）。
  // maplibre 的委派監聽器是在「事件發生時」才查圖層，對尚未存在的圖層註冊是安全的。
  const featLayers = () => ['mapfeat-point', 'mapfeat-symbol']
  const onFeatDown = (e: {
    features?: { properties?: { id?: unknown; gtype?: unknown } | null }[]
    preventDefault: () => void
  }) => {
    const props0 = e.features?.[0]?.properties
    const id = props0?.id
    if (!id || String(id) !== String(props.selectedFeatureId)) return // 只拖選取者
    if (!props.featureEdit) return // #99 無編修權就別讓人拖了才吃 403
    if (props0?.gtype && props0.gtype !== 'POINT') return // 僅點特徵可拖
    e.preventDefault() // 阻止地圖平移
    dragFeatId = String(id)
    if (map) map.getCanvas().style.cursor = 'grabbing'
    map?.on('mousemove', onFeatDragMove)
    map?.once('mouseup', onFeatDrop)
  }
  for (const l of featLayers()) {
    map.on('mousedown', l, onFeatDown)
    map.on('mouseenter', l, () => {
      if (map && !dragFeatId) map.getCanvas().style.cursor = 'move'
    })
    map.on('mouseleave', l, () => {
      if (map && !dragFeatId && !props.targeting) map.getCanvas().style.cursor = ''
    })
  }
  // ---- #99 整形：拖控制點改形狀、拖圖形本體整體平移 ----
  const onVertexDown = (e: {
    features?: { properties?: { i?: unknown; mid?: unknown } | null }[]
    lngLat: { lng: number; lat: number }
    preventDefault: () => void
  }) => {
    if (!props.featureEdit || props.drawActive || !map) return
    const sel = selectedRing()
    const p = e.features?.[0]?.properties
    if (!sel || !p) return
    const idx = Number(p.i)
    if (!Number.isFinite(idx)) return
    e.preventDefault() // 阻止地圖平移
    if (p.mid === true) {
      // 中點：先插入實頂點，接著拖的就是這顆新頂點（Google/Leaflet 慣例）。
      reshape = { ...sel, ring: insertVertex(sel.ring, idx, [e.lngLat.lng, e.lngLat.lat]) }
      dragVertIdx = idx + 1
    } else {
      reshape = sel
      dragVertIdx = idx
    }
    map.getCanvas().style.cursor = 'grabbing'
    map.on('mousemove', onVertexDragMove)
    map.once('mouseup', onVertexDrop)
    armReshapeFallback()
  }
  for (const l of ['mapfeat-vertex', 'mapfeat-midpoint']) {
    map.on('mousedown', l, onVertexDown)
    map.on('mouseenter', l, () => {
      if (map && !reshape && props.featureEdit) map.getCanvas().style.cursor = 'grab'
    })
    map.on('mouseleave', l, () => {
      if (map && !reshape && !props.targeting) map.getCanvas().style.cursor = ''
    })
  }
  // 拖圖形本體＝整體平移；限選取者，否則在圖上任何線/面按下都不能平移地圖了。
  const onBodyDown = (e: {
    features?: { properties?: { id?: unknown } | null }[]
    point: { x: number; y: number }
    lngLat: { lng: number; lat: number }
    preventDefault: () => void
  }) => {
    if (!props.featureEdit || props.drawActive || !map) return
    // **控制點優先**：控制點畫在線/面之上，同一次 mousedown 兩個委派監聽器都會收到。
    // 不在此擋掉的話，拖頂點會變成整體平移（實測：三個頂點同時位移）。
    // 用命中查詢而非「註冊順序」來決定優先權——順序是隱性契約，改天調換註冊位置就再度失效。
    const onHandle = map.queryRenderedFeatures(e.point, {
      layers: ['mapfeat-vertex', 'mapfeat-midpoint'].filter((l) => map?.getLayer(l)),
    })
    if (onHandle.length) return
    const id = e.features?.[0]?.properties?.id
    if (id == null || String(id) !== String(props.selectedFeatureId)) return
    const sel = selectedRing()
    if (!sel) return
    e.preventDefault()
    reshape = sel
    dragVertIdx = -1
    bodyDragStart = { lng: e.lngLat.lng, lat: e.lngLat.lat, ring: sel.ring }
    map.getCanvas().style.cursor = 'grabbing'
    map.on('mousemove', onBodyDragMove)
    map.once('mouseup', onBodyDrop)
    armReshapeFallback()
  }
  const onBodyEnter = (e: { features?: { properties?: { id?: unknown } | null }[] }) => {
    // 只有選取者能拖 → 只在游標下的就是選取者時才給「可移動」暗示。
    const id = e.features?.[0]?.properties?.id
    if (!map || reshape || props.targeting || !props.featureEdit) return
    if (id != null && String(id) === String(props.selectedFeatureId)) {
      map.getCanvas().style.cursor = 'move'
    }
  }
  for (const l of ['mapfeat-fill', 'mapfeat-line']) {
    map.on('mousedown', l, onBodyDown)
    map.on('mouseenter', l, onBodyEnter)
    map.on('mouseleave', l, () => {
      if (map && !reshape && !props.targeting) map.getCanvas().style.cursor = ''
    })
  }
  // 地圖狀態編輯：editUnits 模式下拖放單位到新座標（重用 FEAT_DRAG_SRC 作落點預覽點）。
  const onUnitDragMove = (e: _LngLatEvt): void => {
    if (!dragUnitId || !map) return
    ;(map.getSource(FEAT_DRAG_SRC) as GeoJSONSource | undefined)?.setData({
      type: 'Feature',
      properties: {},
      geometry: { type: 'Point', coordinates: [e.lngLat.lng, e.lngLat.lat] },
    } as never)
  }
  const onUnitDrop = (e: _LngLatEvt): void => {
    if (!dragUnitId || !map) return
    const id = dragUnitId
    dragUnitId = null
    map.getCanvas().style.cursor = ''
    map.off('mousemove', onUnitDragMove)
    ;(map.getSource(FEAT_DRAG_SRC) as GeoJSONSource | undefined)?.setData(_EMPTY_FEAT_FC as never)
    emit('unitMove', { id, lng: e.lngLat.lng, lat: e.lngLat.lat })
  }
  // 整組拖曳（多選）：以按下處為錨，各選取單位依相同經緯位移平移；真圖標 + 高亮環即時跟隨游標。
  const _groupOverride = (e: _LngLatEvt): Map<string, { lng: number; lat: number }> => {
    const dLng = e.lngLat.lng - groupDrag!.start.lng
    const dLat = e.lngLat.lat - groupDrag!.start.lat
    return new Map(groupDrag!.origs.map((o) => [o.id, { lng: o.lng + dLng, lat: o.lat + dLat }]))
  }
  const onGroupDragMove = (e: _LngLatEvt): void => {
    if (!groupDrag || !map) return
    syncUnits(_groupOverride(e)) // 圖標跟著游標
  }
  const onGroupDrop = (e: _LngLatEvt): void => {
    if (!groupDrag || !map) return
    const ov = _groupOverride(e)
    const moves = [...ov.entries()].map(([id, p]) => ({ id, lng: p.lng, lat: p.lat }))
    groupDrag = null
    map.getCanvas().style.cursor = ''
    map.off('mousemove', onGroupDragMove)
    syncUnits(ov) // 保持在放開位置，待 cop reposition→refetch 後 props watch 以權威座標重繪（不回彈）
    if (moves.length) emit('unitsMove', { moves })
  }
  const startGroupDrag = (e: _LngLatEvt): void => {
    const origs = allUnitPositions().filter((u) => selectedUnitIds.has(u.id))
    if (!origs.length || !map) return
    groupDrag = { start: { lng: e.lngLat.lng, lat: e.lngLat.lat }, origs }
    map.getCanvas().style.cursor = 'grabbing'
    map.on('mousemove', onGroupDragMove)
    map.once('mouseup', onGroupDrop)
  }
  const onUnitDown = (e: {
    features?: { properties?: { id?: unknown } | null }[]
    preventDefault: () => void
    originalEvent?: MouseEvent
    lngLat?: { lng: number; lat: number }
  }) => {
    if (!props.editUnits) return // 僅編輯模式可拖曳單位
    if (e.originalEvent?.shiftKey) return // Shift 多選由下方通用 mousedown 處理（避免雙重切換）
    const id = e.features?.[0]?.properties?.id
    if (id == null) return
    const sid = String(id)
    e.preventDefault() // 阻止地圖平移
    // 拖曳已多選的成員 → 整組移動；否則清空多選、單獨拖曳此單位（原行為）。
    if (selectedUnitIds.has(sid) && selectedUnitIds.size > 1 && e.lngLat) {
      startGroupDrag({ lngLat: e.lngLat })
      return
    }
    clearMultiSelect()
    dragUnitId = sid
    if (map) map.getCanvas().style.cursor = 'grabbing'
    map?.on('mousemove', onUnitDragMove)
    map?.once('mouseup', onUnitDrop)
  }
  map.on('mousedown', 'units', onUnitDown)
  // 框選（匡選）：編輯模式下 Shift+於空白處拖曳 → 矩形選取範圍內單位（累加至多選）。
  const onBoxMove = (e: _PointEvt): void => {
    if (!boxStart || !rubberBand) return
    const a = boxStart
    const b = e.point
    rubberBand.style.left = `${Math.min(a.x, b.x)}px`
    rubberBand.style.top = `${Math.min(a.y, b.y)}px`
    rubberBand.style.width = `${Math.abs(a.x - b.x)}px`
    rubberBand.style.height = `${Math.abs(a.y - b.y)}px`
    rubberBand.style.display = 'block'
  }
  const onBoxUp = (e: _PointEvt): void => {
    if (!boxStart || !map) return
    const a = boxStart
    boxStart = null
    map.off('mousemove', onBoxMove)
    map.dragPan.enable()
    if (rubberBand) rubberBand.style.display = 'none'
    const b = e.point
    if (Math.abs(a.x - b.x) < 3 && Math.abs(a.y - b.y) < 3) return // 幾乎沒動→視為點擊，不選
    const minX = Math.min(a.x, b.x)
    const maxX = Math.max(a.x, b.x)
    const minY = Math.min(a.y, b.y)
    const maxY = Math.max(a.y, b.y)
    for (const u of allUnitPositions()) {
      const pt = map.project([u.lng, u.lat])
      if (pt.x >= minX && pt.x <= maxX && pt.y >= minY && pt.y <= maxY) selectedUnitIds.add(u.id)
    }
    refreshMultiSelect()
  }
  map.on('mousedown', (e: _PointEvt) => {
    if (!props.editUnits || !map) return
    if (!e.originalEvent.shiftKey) return // 所有 Shift 互動集中於此（平移仍是無 Shift 拖曳）
    // Shift+點單位 → 加入/移除多選（改在通用 mousedown 處理，不依賴會被 boxZoom 干擾的 layer 事件）。
    const hit = map.queryRenderedFeatures(e.point, { layers: ['units'] })[0]
    const hitId = hit?.properties?.id
    if (hitId != null) {
      const sid = String(hitId)
      if (selectedUnitIds.has(sid)) selectedUnitIds.delete(sid)
      else selectedUnitIds.add(sid)
      refreshMultiSelect()
      e.preventDefault()
      return
    }
    // Shift+空白處拖曳 → 框選矩形。
    boxStart = e.point
    map.dragPan.disable()
    e.preventDefault()
    if (!rubberBand) {
      rubberBand = document.createElement('div')
      // inline 樣式：div 掛在地圖容器（非 scoped Vue 元素），scoped CSS 不會套用。
      rubberBand.style.cssText =
        'position:absolute;border:1.5px solid #22d3ee;background:rgba(34,211,238,0.12);' +
        'pointer-events:none;z-index:5;display:none;'
      map.getContainer().appendChild(rubberBand)
    }
    rubberBand.style.left = `${e.point.x}px`
    rubberBand.style.top = `${e.point.y}px`
    rubberBand.style.width = '0px'
    rubberBand.style.height = '0px'
    rubberBand.style.display = 'block'
    map.on('mousemove', onBoxMove)
    map.once('mouseup', onBoxUp)
  })
  map.on('mouseenter', 'units', () => {
    if (map && props.editUnits && !dragUnitId) map.getCanvas().style.cursor = 'move'
  })
  // 右鍵選單（#3）：阻止瀏覽器選單，emit 螢幕座標 + 經緯 + 游標下單位（供 ATAK 式移動/攻擊）。
  map.on('contextmenu', (e) => {
    const hit = map?.queryRenderedFeatures(e.point, { layers: ['units'] })?.[0]
    const p = hit?.properties
    const flayers = ['mapfeat-point', 'mapfeat-symbol', 'mapfeat-fill', 'mapfeat-line'].filter(
      (l) => map?.getLayer(l),
    )
    const fhit = flayers.length ? map?.queryRenderedFeatures(e.point, { layers: flayers })?.[0] : null
    // #99 右鍵控制點 → 帶索引，供上層「刪除控制點」（刪點的最少頂點檢查與提示在上層）。
    const vhit = map?.getLayer('mapfeat-vertex')
      ? map.queryRenderedFeatures(e.point, { layers: ['mapfeat-vertex'] })?.[0]
      : null
    const vi = Number(vhit?.properties?.i)
    emit('contextMenu', {
      x: e.point.x,
      y: e.point.y,
      lng: e.lngLat.lng,
      lat: e.lngLat.lat,
      ...(p && p.id != null
        ? { unitId: String(p.id), faction: String(p.faction ?? ''), kind: String(p.kind ?? '') }
        : {}),
      ...(fhit?.properties?.id != null ? { featureId: String(fhit.properties.id) } : {}),
      ...(props.featureEdit && Number.isFinite(vi) ? { vertexIndex: vi } : {}),
    })
  })
})

/** 設定目標中（#3）：整個畫布游標改十字準星，離開則回復。 */
function applyTargetingCursor() {
  const c = map?.getCanvas()
  if (c) c.style.cursor = props.targeting ? 'crosshair' : ''
}

onBeforeUnmount(() => {
  window.removeEventListener('mouseup', endReshape) // #99 拖曳中被卸載也不留 listener
  map?.remove()
  map = null
  styleReady = false
})

watch(
  () => props.hexVisible,
  (v) => {
    setLayerVisibility('hexgrid-fill', v)
    setLayerVisibility('hexgrid-line', v)
  },
)
watch(() => props.hillshadeVisible, (v) => setLayerVisibility('hillshade', v))
watch(() => props.contourVisible, (v) => {
  setLayerVisibility('contours-line-major', v)
  setLayerVisibility('contours-line-minor', v)
  setLayerVisibility('contours-label', v)
})
watch(() => props.basemapId, (v) => applyBasemap(v))
watch([() => props.destH3, () => props.destPoint], syncDest)
watch(
  [() => props.movePath, () => props.moveForced, () => props.moveCrossings],
  syncMovePath,
  { deep: true },
)
watch(
  [
    () => props.featureFc,
    () => props.featSymbolFc,
    () => props.featSymbolIcons,
    () => props.influenceFc,
    () => props.draftFc,
    () => props.weaponTrackFc,
  ],
  syncFeatures,
  { deep: true },
)
watch([() => props.latlngGrid, () => props.mgrsGrid, () => props.gridStepDeg], refreshGrid)
watch([() => props.hexMaxRes, () => props.hexLimitKm], refreshHex)
watch([() => props.dayNight, () => props.timeOfDay], applyDayNight)
watch(() => props.targeting, applyTargetingCursor) // #3 十字準星
// 地圖狀態編輯：進入時停用 MapLibre 內建 boxZoom（Shift+拖曳預設是縮放框，會攔截多選/框選、
// 干擾 Shift 事件）；離開時還原並清空多選（避免殘留高亮環）。
watch(
  () => props.editUnits,
  (on) => {
    if (map) {
      if (on) map.boxZoom.disable()
      else map.boxZoom.enable()
    }
    if (!on) clearMultiSelect()
  },
)
watch(() => props.queryPoint, syncQuery)
watch(
  () => props.selectedFeatureId,
  (v) => {
    if (map?.getLayer('mapfeat-sel-line'))
      map.setFilter('mapfeat-sel-line', ['==', ['get', 'id'], v ?? FEAT_NONE])
    if (map?.getLayer('mapfeat-sel-point'))
      map.setFilter('mapfeat-sel-point', [
        'all',
        ['==', ['geometry-type'], 'Point'],
        ['==', ['get', 'id'], v ?? FEAT_NONE],
      ])
    syncVertices() // #99 換選取對象 → 控制點跟著換（無選取→清空）
  },
)
// #99 失去編修權或進入繪製模式 → 立即收掉控制點（vertexFc 已含此判斷）。
watch([() => props.featureEdit, () => props.drawActive], syncVertices)
watch(() => props.layerOpacity, applyAllOpacity, { deep: true }) // #9 透明度
watch(() => props.layerOrder, applyOrder, { deep: true }) // #9 套疊順序
watch([() => props.contourMajor, () => props.contourMinor], applyContourFilters) // #8 等高線間距
watch(
  [
    () => props.hexLineWidth,
    () => props.contourMajorWidth,
    () => props.contourMinorWidth,
    () => props.gridWidth,
    () => props.hexLineColor,
    () => props.contourColor,
    () => props.gridColor,
    () => props.mgrsColor,
  ],
  applyLineStyles,
) // #5/#22 線條粗細 + 顏色
// 選取單位的螢幕座標 → emit（供 Unit 資訊卡懸浮於圖標旁）。地圖平移/縮放時亦即時重算。
function emitSelectPos() {
  const v = props.selectedId
  if (!v || !map) {
    emit('selectScreenPos', null)
    return
  }
  const u =
    props.ownUnits.find((o) => o.id === v) ?? props.contacts.find((c) => c.contactId === v)
  if (!u) {
    emit('selectScreenPos', null)
    return
  }
  const p = map.project([u.lng, u.lat])
  emit('selectScreenPos', { x: p.x, y: p.y })
}
watch(
  () => props.selectedId,
  (v) => {
    if (map?.getLayer('unit-selected-ring'))
      map.setFilter('unit-selected-ring', ['==', ['get', 'id'], v ?? NONE])
    // 選取單位時把地圖飛到該單位（放大到至少 z11），讓使用者立刻看到是哪個圖標。
    if (v && map) {
      const u =
        props.ownUnits.find((o) => o.id === v) ??
        props.contacts.find((c) => c.contactId === v)
      if (u) map.flyTo({ center: [u.lng, u.lat], zoom: Math.max(map.getZoom(), 11), duration: 600 })
    }
    emitSelectPos()
  },
)
watch(
  () => props.targetId,
  (v) => {
    if (map?.getLayer('unit-target-ring'))
      map.setFilter('unit-target-ring', ['==', ['get', 'id'], v ?? NONE])
  },
)
watch([() => props.ownUnits, () => props.contacts, () => props.currentTick], syncUnits, {
  deep: true,
})
</script>

<template>
  <div ref="container" class="map-canvas" :data-map-loaded="loaded" data-testid="map-canvas" />
</template>

<style scoped>
.map-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
</style>
