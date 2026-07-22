import type { StyleSpecification } from 'maplibre-gl'

// Taiwan 戰區預設視野中心（SPEC §13.2）。
export const TAIWAN_CENTER: [number, number] = [121.0, 23.7] // [lng, lat]
export const DEFAULT_ZOOM = 7

/**
 * 建離線自足的 MapLibre 樣式（O4.2）——**只含 background**。底圖（街道/衛星/軍用）由 MapCanvas
 * 依 basemap 來源動態加為 raster 層（可抽換），不寫死於 style；斷網 / 無底圖時仍顯背景。
 * 不引用任何外部 CDN 的 style/glyphs/sprite（air-gapped，SPEC §12/§20）。
 */
export function buildOfflineStyle(): StyleSpecification {
  return {
    version: 8,
    name: 'matso-offline',
    sources: {},
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#0a1626' } }],
  }
}

// ---------------- 可抽換底圖來源（#2；未來可接軍方街道 / 衛星影像）----------------

/**
 * 底圖來源定義。`offline`＝純離線格線（無瓦片）；`raster`＝XYZ 瓦片模板（街道 / 衛星 / 軍用影像）。
 * 全部由設定注入（runtimeConfig.public），故未來接軍方資料只需加設定、不動程式碼。
 */
export interface BasemapSource {
  id: string
  label: string
  type: 'offline' | 'raster' | 'vector'
  tiles?: string[]
  tileSize?: number
  minzoom?: number
  maxzoom?: number
  attribution?: string
}

/**
 * OpenMapTiles 向量圖層 → 深色無文字樣式（無 symbol 層 → 不需 glyphs，air-gapped）。
 * 回傳的圖層皆引用 sourceId；MapCanvas 以 'basemap-' 前綴管理增刪。與 COP 深色底一致。
 */
export function openMapTilesDarkLayers(sourceId: string): StyleSpecification['layers'] {
  const s = sourceId
  return [
    { id: 'basemap-landcover', type: 'fill', source: s, 'source-layer': 'landcover', paint: { 'fill-color': '#14291d', 'fill-opacity': 0.6 } },
    { id: 'basemap-landuse', type: 'fill', source: s, 'source-layer': 'landuse', paint: { 'fill-color': '#182430', 'fill-opacity': 0.5 } },
    { id: 'basemap-park', type: 'fill', source: s, 'source-layer': 'park', paint: { 'fill-color': '#153021', 'fill-opacity': 0.5 } },
    { id: 'basemap-water', type: 'fill', source: s, 'source-layer': 'water', paint: { 'fill-color': '#0d2c47' } },
    { id: 'basemap-waterway', type: 'line', source: s, 'source-layer': 'waterway', paint: { 'line-color': '#123a5a', 'line-width': 1 } },
    { id: 'basemap-transportation', type: 'line', source: s, 'source-layer': 'transportation', paint: { 'line-color': '#3a4f66', 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.3, 12, 1.6] } },
    { id: 'basemap-building', type: 'fill', source: s, 'source-layer': 'building', minzoom: 13, paint: { 'fill-color': '#243141', 'fill-opacity': 0.7 } },
    { id: 'basemap-boundary', type: 'line', source: s, 'source-layer': 'boundary', paint: { 'line-color': '#4a5a6a', 'line-width': 0.6, 'line-dasharray': [2, 2] } },
  ]
}

export interface BasemapConfig {
  tileUrl?: string // 本地 tileserver（街道，OpenMapTiles）
  satelliteUrl?: string // 衛星 raster XYZ 模板（商用 / 軍用影像）
  basemaps?: BasemapSource[] // 額外自訂來源（NUXT_PUBLIC_BASEMAPS，JSON）——軍方接入的抽換點
  onlineBasemaps?: boolean // 啟用 Google/Esri 線上光柵底圖（需外網，非 air-gapped）
}

/** 恆有的離線來源（無瓦片時的可靠回退）。 */
export const OFFLINE_SOURCE: BasemapSource = { id: 'offline', label: '離線格線', type: 'offline' }

/**
 * 線上 XYZ 光柵底圖（**需外網，非 air-gapped**；由 onlineBasemaps 開關，預設關）。
 * 注意 Esri 走 {z}/{y}/{x} 順序（與 Google/OSM 的 {z}/{x}/{y} 不同）。
 */
export const ONLINE_RASTER_SOURCES: BasemapSource[] = [
  { id: 'google-satellite', label: 'Google 衛星', type: 'raster', tileSize: 256, maxzoom: 20,
    tiles: ['https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}'], attribution: '© Google' },
  { id: 'google-hybrid', label: 'Google 混合', type: 'raster', tileSize: 256, maxzoom: 20,
    tiles: ['https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}'], attribution: '© Google' },
  { id: 'esri-satellite', label: 'Esri 衛星', type: 'raster', tileSize: 256, maxzoom: 19,
    tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: '© Esri, Maxar, Earthstar Geographics' },
  { id: 'esri-topo', label: 'Esri 地形街道', type: 'raster', tileSize: 256, maxzoom: 19,
    tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}'],
    attribution: '© Esri' },
]

/**
 * 由設定組出底圖來源清單：離線 + 街道（tileUrl）+ 衛星（satelliteUrl）+ 自訂（basemaps）。
 * 清單順序即 UI 切換順序；離線恆在首（預設）。
 */
export function buildBasemapSources(cfg: BasemapConfig): BasemapSource[] {
  const out: BasemapSource[] = [OFFLINE_SOURCE]
  if (cfg.tileUrl) {
    // 街道＝本地 tileserver 的 OpenMapTiles **向量**瓦片（tileserver-gl-light 可服務；深色無文字樣式）。
    out.push({
      id: 'street',
      label: '街道',
      type: 'vector',
      // tileserver-gl-light 以 mbtiles 內部名（OpenMapTiles schema = 'v3'）服務向量瓦片。
      tiles: [`${cfg.tileUrl}/data/v3/{z}/{x}/{y}.pbf`],
      minzoom: 0,
      maxzoom: 14,
      attribution: '© OpenMapTiles © OpenStreetMap contributors',
    })
  }
  if (cfg.satelliteUrl) {
    out.push({
      id: 'satellite',
      label: '衛星',
      type: 'raster',
      tiles: [cfg.satelliteUrl],
      tileSize: 256,
      attribution: '衛星影像',
    })
  }
  if (cfg.onlineBasemaps) out.push(...ONLINE_RASTER_SOURCES) // Google/Esri（需外網）
  for (const b of cfg.basemaps ?? []) out.push(b) // 軍方 / 自訂來源
  return out
}

export interface LineFeatureCollection {
  type: 'FeatureCollection'
  features: Array<{
    type: 'Feature'
    properties: Record<string, never>
    geometry: { type: 'LineString'; coordinates: number[][] }
  }>
}

/**
 * 經緯網格（graticule）GeoJSON——離線底圖佐證（無 tile server 也有可視參考）。
 * 台灣戰區範圍每 stepDeg 一線。
 */
export function buildGraticule(stepDeg = 1): LineFeatureCollection {
  const [w, e, s, n] = [118, 123, 21, 27]
  const features: LineFeatureCollection['features'] = []
  const line = (coordinates: number[][]) =>
    ({ type: 'Feature' as const, properties: {}, geometry: { type: 'LineString' as const, coordinates } })
  for (let lng = w; lng <= e; lng += stepDeg) {
    features.push(line([[lng, s], [lng, n]]))
  }
  for (let lat = s; lat <= n; lat += stepDeg) {
    features.push(line([[w, lat], [e, lat]]))
  }
  return { type: 'FeatureCollection', features }
}
