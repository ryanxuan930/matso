// 座標網格（#9）：經緯度網格（線+標籤）與 MGRS 標記——純函數，依視野 bbox + 密度生成 GeoJSON。
import { forward } from 'mgrs'

export interface GridBounds {
  west: number
  south: number
  east: number
  north: number
}
type Fc = { type: 'FeatureCollection'; features: unknown[] }

function line(coords: number[][]): unknown {
  return { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } }
}
function label(lng: number, lat: number, text: string): unknown {
  return { type: 'Feature', properties: { label: text }, geometry: { type: 'Point', coordinates: [lng, lat] } }
}
function fmtLng(v: number): string {
  return `${Math.abs(v).toFixed(v % 1 ? 2 : 0)}°${v >= 0 ? 'E' : 'W'}`
}
function fmtLat(v: number): string {
  return `${Math.abs(v).toFixed(v % 1 ? 2 : 0)}°${v >= 0 ? 'N' : 'S'}`
}
// 避免超細密度爆量：限制單軸線數（過密則不畫）。
const MAX_LINES = 200

/** 經緯度網格線 + 邊緣標籤（步距為度）。 */
export function buildLatLngGrid(b: GridBounds, stepDeg: number): { lines: Fc; labels: Fc } {
  const lines: unknown[] = []
  const labels: unknown[] = []
  const step = stepDeg > 0 ? stepDeg : 1
  if ((b.east - b.west) / step > MAX_LINES || (b.north - b.south) / step > MAX_LINES) {
    return { lines: { type: 'FeatureCollection', features: [] }, labels: { type: 'FeatureCollection', features: [] } }
  }
  const startLng = Math.ceil(b.west / step) * step
  for (let lng = startLng; lng <= b.east; lng += step) {
    lines.push(line([[lng, b.south], [lng, b.north]]))
    labels.push(label(lng, b.south + (b.north - b.south) * 0.02, fmtLng(lng)))
  }
  const startLat = Math.ceil(b.south / step) * step
  for (let lat = startLat; lat <= b.north; lat += step) {
    lines.push(line([[b.west, lat], [b.east, lat]]))
    labels.push(label(b.west + (b.east - b.west) * 0.02, lat, fmtLat(lat)))
  }
  return {
    lines: { type: 'FeatureCollection', features: lines },
    labels: { type: 'FeatureCollection', features: labels },
  }
}

/** MGRS 座標標記（於經緯格交點，accuracy 位數控制精度）。 */
export function buildMgrsLabels(b: GridBounds, stepDeg: number, accuracy = 3): Fc {
  const labels: unknown[] = []
  const step = stepDeg > 0 ? stepDeg : 1
  if ((b.east - b.west) / step > MAX_LINES || (b.north - b.south) / step > MAX_LINES) {
    return { type: 'FeatureCollection', features: [] }
  }
  const startLng = Math.ceil(b.west / step) * step
  const startLat = Math.ceil(b.south / step) * step
  for (let lng = startLng; lng <= b.east; lng += step) {
    for (let lat = startLat; lat <= b.north; lat += step) {
      let m: string
      try {
        m = forward([lng, lat], accuracy)
      } catch {
        continue
      }
      labels.push(label(lng, lat, m))
    }
  }
  return { type: 'FeatureCollection', features: labels }
}
