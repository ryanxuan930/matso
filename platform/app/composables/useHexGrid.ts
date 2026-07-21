import { cellToBoundary, polygonToCells } from 'h3-js'

export interface Bounds {
  west: number
  south: number
  east: number
  north: number
}

export interface HexFeatureCollection {
  type: 'FeatureCollection'
  features: Array<{
    type: 'Feature'
    properties: { h3: string }
    geometry: { type: 'Polygon'; coordinates: number[][][] }
  }>
}

/** 依縮放層級選 H3 解析度（拉遠→粗，避免 cell 數爆量）。SPEC 預設 res 8，此處為顯示層。 */
export function resForZoom(zoom: number): number {
  if (zoom < 6) return 3
  if (zoom < 8) return 4
  if (zoom < 10) return 5
  if (zoom < 12) return 6
  return 7
}

/**
 * 視野 bbox 內的 H3 cell → GeoJSON（客戶端計算，離線；O4.2 hex 層）。
 * 以 h3-js polygonToCells（GeoJSON [lng,lat] 序）取 cell，cellToBoundary 取邊界多邊形。
 * 每個 boundary 補回首點閉合環。
 */
export function hexCellsForBounds(bounds: Bounds, zoom: number): HexFeatureCollection {
  const res = resForZoom(zoom)
  // GeoJSON 序 [lng,lat] 的封閉環（順時針/逆時針皆可）
  const loop: number[][] = [
    [bounds.west, bounds.south],
    [bounds.east, bounds.south],
    [bounds.east, bounds.north],
    [bounds.west, bounds.north],
    [bounds.west, bounds.south],
  ]
  const cells = polygonToCells([loop], res, true)
  return {
    type: 'FeatureCollection',
    features: cells.map((h3) => {
      const boundary = cellToBoundary(h3, true) // [lng,lat][]
      const ring = [...boundary, boundary[0]!] // 閉合
      return {
        type: 'Feature' as const,
        properties: { h3 },
        geometry: { type: 'Polygon' as const, coordinates: [ring] },
      }
    }),
  }
}
