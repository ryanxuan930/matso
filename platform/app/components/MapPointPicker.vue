<script setup lang="ts">
// 地圖點選座標（#8）——想定編輯器單位初始位置以點擊地圖取代手填經緯度。
// air-gapped：maplibre-gl 於 onMounted 動態 import（絕不進 SSR），樣式走 buildOfflineStyle；
// 底圖（街道/衛星/軍用）由 runtimeConfig.public 注入並比照 MapCanvas.applyBasemap 動態加層。
// 使用端須以 <ClientOnly> 包裹。
import type { Map as MapLibreMap, Marker as MapLibreMarker } from 'maplibre-gl'
import {
  DEFAULT_ZOOM,
  TAIWAN_CENTER,
  buildBasemapSources,
  buildGraticule,
  buildOfflineStyle,
  openMapTilesDarkLayers,
} from '~/composables/useMapStyle'

export interface LngLat { lng: number; lat: number }

const props = withDefaults(
  defineProps<{
    modelValue?: LngLat | null
  }>(),
  { modelValue: null },
)
const emit = defineEmits<{ 'update:modelValue': [LngLat] }>()

const container = ref<HTMLDivElement | null>(null)
const loaded = ref(false)
let map: MapLibreMap | null = null
let marker: MapLibreMarker | null = null
let MarkerCtor: typeof MapLibreMarker | null = null

const GRAT_SRC = 'graticule'

// 可抽換底圖來源（由 runtimeConfig 注入；#2，與 MapCanvas 相同）。
const cfg = useRuntimeConfig().public
const basemapSources = buildBasemapSources({
  tileUrl: cfg.tileUrl as string,
  satelliteUrl: cfg.satelliteUrl as string | undefined,
  basemaps: cfg.basemaps as ReturnType<typeof buildBasemapSources> | undefined,
  onlineBasemaps: cfg.onlineBasemaps as boolean,
})
// 有本地底圖來源時預設顯示第一個非離線來源（街道/衛星）；否則純離線格線。
const defaultBasemapId = basemapSources.find((s) => s.id !== 'offline')?.id ?? 'offline'

function isValid(v?: LngLat | null): v is LngLat {
  return !!v && Number.isFinite(v.lng) && Number.isFinite(v.lat)
}
function initialCenter(): [number, number] {
  return isValid(props.modelValue) ? [props.modelValue.lng, props.modelValue.lat] : TAIWAN_CENTER
}

/** 套用底圖來源：raster → 單一 raster 層；vector → OpenMapTiles 深色圖層組（皆置於 graticule 之下）。 */
function applyBasemap(id: string) {
  if (!map) return
  const src = basemapSources.find((s) => s.id === id)
  if (!src || !src.tiles) return // offline 或未知 → 僅背景 + 格線
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

function setMarker(lng: number, lat: number) {
  if (!map || !MarkerCtor) return
  if (!marker) marker = new MarkerCtor({ color: '#f59e0b' }).setLngLat([lng, lat]).addTo(map)
  else marker.setLngLat([lng, lat])
}

const round6 = (n: number) => Math.round(n * 1e6) / 1e6

onMounted(async () => {
  if (!container.value) return
  const { Map: MapCtor, Marker, NavigationControl } = await import('maplibre-gl')
  MarkerCtor = Marker
  const tileUrl = cfg.tileUrl as string

  map = new MapCtor({
    container: container.value,
    style: buildOfflineStyle(tileUrl ? `${tileUrl}/fonts/{fontstack}/{range}.pbf` : undefined),
    center: initialCenter(),
    zoom: isValid(props.modelValue) ? 10 : DEFAULT_ZOOM,
    attributionControl: false,
  })
  map.addControl(new NavigationControl({ showZoom: true, showCompass: false }), 'top-left')

  map.on('load', () => {
    if (!map) return
    map.addSource(GRAT_SRC, { type: 'geojson', data: buildGraticule() })
    map.addLayer({
      id: 'graticule',
      type: 'line',
      source: GRAT_SRC,
      paint: { 'line-color': '#33608f', 'line-width': 0.8, 'line-opacity': 0.8 },
    })
    applyBasemap(defaultBasemapId) // 底圖置於 graticule 之下（可抽換，#2）
    if (isValid(props.modelValue)) setMarker(props.modelValue.lng, props.modelValue.lat)
    loaded.value = true
  })

  // 點擊地圖 → 落一枚標記並回傳座標（六位小數，約 0.1m 精度）。
  map.on('click', (e) => {
    const lng = round6(e.lngLat.lng)
    const lat = round6(e.lngLat.lat)
    setMarker(lng, lat)
    emit('update:modelValue', { lng, lat })
  })
  map.getCanvas().style.cursor = 'crosshair'
})

onBeforeUnmount(() => {
  marker?.remove()
  marker = null
  map?.remove()
  map = null
})

// 外部（手填欄位）改動 → 同步標記位置。
watch(
  () => props.modelValue,
  (v) => {
    if (isValid(v)) setMarker(v.lng, v.lat)
  },
  { deep: true },
)
</script>

<template>
  <div class="map-point-picker" data-testid="map-point-picker">
    <div ref="container" class="mpp-canvas" :data-map-loaded="loaded" />
    <p class="mpp-hint">點地圖設定初始位置</p>
  </div>
</template>

<style scoped>
.map-point-picker {
  position: relative;
  width: 100%;
  height: 260px;
  border: 1px solid #1e293b;
  border-radius: 0.35rem;
  overflow: hidden;
}
.mpp-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.mpp-hint {
  position: absolute;
  left: 0.4rem;
  bottom: 0.4rem;
  margin: 0;
  padding: 0.1rem 0.4rem;
  font-size: 0.75rem;
  color: #cbd5e1;
  background: rgba(10, 22, 38, 0.72);
  border-radius: 0.25rem;
  pointer-events: none;
}
</style>
