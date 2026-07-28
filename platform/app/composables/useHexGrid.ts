import { cellToBoundary, cellToLatLng, polygonToCells } from 'h3-js'

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

/** 依縮放層級選 H3 解析度（拉遠→粗，避免 cell 數爆量）。SPEC 預設 res 8，此處為顯示層。
 * 高縮放（z≥13）對齊移動格 res 8：使用者看到/點選的格＝MOVE 單位吸附的目的格（#4b）。
 * maxRes 上限（可設定最小網格＝最細解析度）——限制運算量。 */
export function resForZoom(zoom: number, maxRes = 8): number {
  let r = 3
  if (zoom >= 6) r = 4
  if (zoom >= 8) r = 5
  if (zoom >= 10) r = 6
  if (zoom >= 12) r = 7
  if (zoom >= 13) r = 8
  return Math.min(r, maxRes)
}

/** 兩點大圓距離（km）——用於「交戰範圍」限制。 */
function haversineKm(aLng: number, aLat: number, bLng: number, bLat: number): number {
  const R = 6371
  const dLat = ((bLat - aLat) * Math.PI) / 180
  const dLng = ((bLng - aLng) * Math.PI) / 180
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((aLat * Math.PI) / 180) * Math.cos((bLat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)))
}

export interface HexOpts {
  maxRes?: number // 最細解析度上限（設定最小網格）
  limit?: { lng: number; lat: number; radiusKm: number } // 交戰範圍：僅計算此圓內的格（降低運算量）
}

/**
 * 視野 bbox 內的 H3 cell → GeoJSON（客戶端計算，離線；O4.2 hex 層）。
 * 以 h3-js polygonToCells（GeoJSON [lng,lat] 序）取 cell，cellToBoundary 取邊界多邊形。
 * 每個 boundary 補回首點閉合環。
 */
export function hexCellsForBounds(bounds: Bounds, zoom: number, opts: HexOpts = {}): HexFeatureCollection {
  const res = resForZoom(zoom, opts.maxRes ?? 8)
  // GeoJSON 序 [lng,lat] 的封閉環（順時針/逆時針皆可）
  const loop: number[][] = [
    [bounds.west, bounds.south],
    [bounds.east, bounds.south],
    [bounds.east, bounds.north],
    [bounds.west, bounds.north],
    [bounds.west, bounds.south],
  ]
  let cells = polygonToCells([loop], res, true)
  // 交戰範圍限制：僅保留圓心 radiusKm 內的格（降低運算量，#trailing）。
  const lim = opts.limit
  if (lim && lim.radiusKm > 0) {
    cells = cells.filter((h3) => {
      const [clat, clng] = cellToLatLng(h3)
      return haversineKm(lim.lng, lim.lat, clng, clat) <= lim.radiusKm
    })
  }
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
