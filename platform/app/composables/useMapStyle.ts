import type { StyleSpecification } from 'maplibre-gl'

// Taiwan 戰區預設視野中心（SPEC §13.2）。
export const TAIWAN_CENTER: [number, number] = [121.0, 23.7] // [lng, lat]
export const DEFAULT_ZOOM = 7

/**
 * 建離線自足的 MapLibre 樣式（O4.2）。
 *
 * **離線第一**：一定含 background 層 → 無網路 / 無 tile server 也能渲染。不引用任何外部
 * CDN 的 style/glyphs/sprite（air-gapped，SPEC §12/§20）。無文字層 → 不需 glyphs。
 * tileUrl 有設時才加基礎底圖 raster 來源（斷網時 MapLibre 靜默忽略載不到的瓦片，仍顯背景）。
 *
 * hex / graticule / hillshade 等層由 MapCanvas 動態加為 GeoJSON / raster 來源（可開關）。
 */
export function buildOfflineStyle(tileUrl: string): StyleSpecification {
  const sources: StyleSpecification['sources'] = {}
  const layers: StyleSpecification['layers'] = [
    { id: 'background', type: 'background', paint: { 'background-color': '#0a1626' } },
  ]

  if (tileUrl) {
    sources.basemap = {
      type: 'raster',
      tiles: [`${tileUrl}/styles/basic-preview/{z}/{x}/{y}.png`],
      tileSize: 256,
      attribution: 'OpenMapTiles（本地離線 tileserver）',
    }
    layers.push({ id: 'basemap', type: 'raster', source: 'basemap' })
  }

  return { version: 8, name: 'matso-offline', sources, layers }
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
