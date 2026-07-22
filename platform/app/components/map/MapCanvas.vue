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
import { latLngToCell } from 'h3-js'
import { type Contact, type OwnUnit, buildUnitFeatures } from '~/composables/useUnits'
import { symbolImage } from '~/composables/useMilsymbol'

const emit = defineEmits<{
  mapClick: [{ lng: number; lat: number; h3: string }]
  unitClick: [{ id: string; faction: string; kind: string }]
}>()

// 由 <ClientOnly> 包裹確保只在 client 掛載；maplibre-gl 於 onMounted 動態 import（絕不進 SSR，
// 因其 module 於 import 時觸及 window/document）。
const props = withDefaults(
  defineProps<{
    hexVisible?: boolean
    hillshadeVisible?: boolean
    ownUnits?: OwnUnit[]
    contacts?: Contact[]
    currentTick?: number
    selectedId?: string | null // 選取的我方單位（藍色高亮環）
    targetId?: string | null // ENGAGE 鎖定的目標（紅色高亮環）
    basemapId?: string // 當前底圖來源 id（offline / street / satellite / 軍用…）
  }>(),
  {
    hexVisible: false,
    hillshadeVisible: false,
    ownUnits: () => [],
    contacts: () => [],
    currentTick: 0,
    selectedId: null,
    targetId: null,
    basemapId: 'offline',
  },
)

// 可抽換底圖來源（由 runtimeConfig 注入；#2）。
const _cfg = useRuntimeConfig().public
const basemapSources = buildBasemapSources({
  tileUrl: _cfg.tileUrl as string,
  satelliteUrl: _cfg.satelliteUrl as string | undefined,
  basemaps: _cfg.basemaps as ReturnType<typeof buildBasemapSources> | undefined,
})

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
const UNITS_SRC = 'units'

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
  const { Map: MapCtor } = await import('maplibre-gl')
  const tileUrl = useRuntimeConfig().public.tileUrl as string

  map = new MapCtor({
    container: container.value,
    style: buildOfflineStyle(),
    center: TAIWAN_CENTER,
    zoom: DEFAULT_ZOOM,
    attributionControl: false,
  })
  ;(window as unknown as { __matsoMap?: MapLibreMap }).__matsoMap = map
  map.on('error', (e) => console.error('MAPERR', (e as { error?: Error }).error?.message))

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
watch(() => props.basemapId, (v) => applyBasemap(v))
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
