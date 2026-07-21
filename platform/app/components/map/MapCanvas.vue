<script setup lang="ts">
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'
import {
  DEFAULT_ZOOM,
  TAIWAN_CENTER,
  buildGraticule,
  buildOfflineStyle,
} from '~/composables/useMapStyle'
import { hexCellsForBounds } from '~/composables/useHexGrid'

// 由 <ClientOnly> 包裹確保只在 client 掛載；maplibre-gl 於 onMounted 動態 import（絕不進 SSR，
// 因其 module 於 import 時觸及 window/document）。
const props = withDefaults(
  defineProps<{ hexVisible?: boolean; hillshadeVisible?: boolean }>(),
  { hexVisible: false, hillshadeVisible: false },
)

const container = ref<HTMLDivElement | null>(null)
const loaded = ref(false)
let map: MapLibreMap | null = null

const HEX_SRC = 'hexgrid'
const GRAT_SRC = 'graticule'
const HILLSHADE_SRC = 'hillshade'

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

onMounted(async () => {
  if (!container.value) return
  const { Map: MapCtor } = await import('maplibre-gl')
  const tileUrl = useRuntimeConfig().public.tileUrl as string

  map = new MapCtor({
    container: container.value,
    style: buildOfflineStyle(tileUrl),
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
      paint: { 'line-color': '#1e3a5f', 'line-width': 0.5 },
    })
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
    refreshHex()
    loaded.value = true
    ;(window as unknown as { __matsoMap?: MapLibreMap }).__matsoMap = map
  })

  map.on('moveend', refreshHex)
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
