<script setup lang="ts">
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'
import {
  DEFAULT_ZOOM,
  TAIWAN_CENTER,
  buildBasemapSources,
  buildGraticule,
  buildOfflineStyle,
  openMapTilesDarkLayers,
} from '~/composables/useMapStyle'
import { hexCellsForBounds } from '~/composables/useHexGrid'
import { cellToBoundary, cellToLatLng, latLngToCell } from 'h3-js'
import { type Contact, type OwnUnit, buildUnitFeatures } from '~/composables/useUnits'
import { symbolImage } from '~/composables/useMilsymbol'

const emit = defineEmits<{
  mapClick: [{ lng: number; lat: number; h3: string }]
  unitClick: [{ id: string; faction: string; kind: string }]
  basemapError: [{ id: string }] // 底圖瓦片載入失敗（供上層回退離線）
}>()

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
  },
)

// 可抽換底圖來源（由 runtimeConfig 注入；#2）。
const _cfg = useRuntimeConfig().public
const basemapSources = buildBasemapSources({
  tileUrl: _cfg.tileUrl as string,
  satelliteUrl: _cfg.satelliteUrl as string | undefined,
  basemaps: _cfg.basemaps as ReturnType<typeof buildBasemapSources> | undefined,
  onlineBasemaps: _cfg.onlineBasemaps as boolean,
})
let basemapErrorHandled = false // 每次切底圖重置，避免 404 洪水重複 emit

/** 移除現有底圖（raster 'basemap' 或 vector 'basemap-*' 圖層）+ 來源。 */
function removeBasemap() {
  if (!map) return
  const ids = (map.getStyle().layers ?? [])
    .map((l) => l.id)
    .filter((id) => id === 'basemap' || id.startsWith('basemap-'))
  for (const id of ids) map.removeLayer(id)
  if (map.getSource('basemap')) map.removeSource('basemap')
}

/** 套用底圖來源：raster → 單一 raster 層；vector → OpenMapTiles 深色圖層組（皆置於 graticule 之下）。 */
function applyBasemap(id: string) {
  if (!map) return
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
}

const NONE = '__matso_none__' // 過濾器 sentinel：無選取時不匹配任何 feature

const container = ref<HTMLDivElement | null>(null)
const loaded = ref(false)
let map: MapLibreMap | null = null

const HEX_SRC = 'hexgrid'
const GRAT_SRC = 'graticule'
const HILLSHADE_SRC = 'hillshade'
const CONTOUR_SRC = 'contours'
const UNITS_SRC = 'units'
const DEST_SRC = 'move-dest'

const EMPTY_FC = { type: 'FeatureCollection' as const, features: [] }

/** MOVE 目的格（res 8）→ 吸附後的六角格多邊形 + 格心點（#4b：讓「點哪→實際到哪」透明）。 */
function destFeatures(h3: string | null): { type: 'FeatureCollection'; features: unknown[] } {
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
  src?.setData(destFeatures(props.destH3) as never)
}

function refreshHex() {
  if (!map) return
  const b = map.getBounds()
  const fc = hexCellsForBounds(
    { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() },
    map.getZoom(),
  )
  const src = map.getSource(HEX_SRC) as GeoJSONSource | undefined
  src?.setData(fc)
}

function setLayerVisibility(id: string, visible: boolean) {
  if (map?.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
}

/** 依 props 的單位/contact 重建 symbol 特徵：生成/快取 milsymbol icon（去重 addImage）→ setData。 */
function syncUnits() {
  if (!map) return
  const { collection, icons } = buildUnitFeatures(props.ownUnits, props.contacts, props.currentTick)
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
    style: buildOfflineStyle(),
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
      map.addSource(CONTOUR_SRC, {
        type: 'vector',
        tiles: [`${tileUrl}/data/contours/{z}/{x}/{y}.pbf`],
        minzoom: 0,
        maxzoom: 14,
      })
      map.addLayer({
        id: 'contours-line',
        type: 'line',
        source: CONTOUR_SRC,
        'source-layer': 'contour',
        layout: { visibility: props.contourVisible ? 'visible' : 'none' },
        paint: { 'line-color': '#a98b57', 'line-width': 0.6, 'line-opacity': 0.55 },
      })
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
      paint: { 'line-color': '#38bdf8', 'line-width': 0.5, 'line-opacity': 0.5 },
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
    refreshHex()
    syncUnits()
    syncDest()
    loaded.value = true
    ;(window as unknown as { __matsoMap?: MapLibreMap }).__matsoMap = map
  })

  map.on('moveend', refreshHex)
  // 點單位符號 → unitClick（選我方 / 鎖敵方目標）；點空白 → mapClick（MOVE 目標點）。
  map.on('click', (e) => {
    const hit = map?.queryRenderedFeatures(e.point, { layers: ['units'] })?.[0]
    const p = hit?.properties
    if (p && p.id != null) {
      emit('unitClick', { id: String(p.id), faction: String(p.faction ?? ''), kind: String(p.kind ?? '') })
      return
    }
    emit('mapClick', { lng: e.lngLat.lng, lat: e.lngLat.lat, h3: latLngToCell(e.lngLat.lat, e.lngLat.lng, 8) })
  })
  // 滑過單位符號時游標變手指（可點示意）
  map.on('mouseenter', 'units', () => { if (map) map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', 'units', () => { if (map) map.getCanvas().style.cursor = '' })
})

onBeforeUnmount(() => {
  map?.remove()
  map = null
})

watch(
  () => props.hexVisible,
  (v) => {
    setLayerVisibility('hexgrid-fill', v)
    setLayerVisibility('hexgrid-line', v)
  },
)
watch(() => props.hillshadeVisible, (v) => setLayerVisibility('hillshade', v))
watch(() => props.contourVisible, (v) => setLayerVisibility('contours-line', v))
watch(() => props.basemapId, (v) => applyBasemap(v))
watch(() => props.destH3, syncDest)
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
