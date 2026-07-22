// 地圖標註/工事（stage ③b）——CRUD + 純函數建 GeoJSON（供 MapCanvas 渲染）。
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type MapFeature = components['schemas']['MapFeatureView']
export type DraftKind = 'POINT' | 'LINE' | 'POLYGON'

// ---- CRUD ----
export function fetchMapFeatures(sessionId: string): Promise<MapFeature[]> {
  return apiFetch<MapFeature[]>(`/sessions/${sessionId}/map-features`)
}
export interface FeatureCreate {
  kind: string
  geometry_type: string
  geometry: unknown
  owner_faction?: string | null
  label?: string | null
  influence_radius_m?: number | null
  weapon_template_id?: string | null
  attributes?: Record<string, unknown>
}
export function createMapFeature(sessionId: string, body: FeatureCreate): Promise<MapFeature> {
  return apiFetch<MapFeature>(`/sessions/${sessionId}/map-features`, { method: 'POST', body })
}
export function editMapFeature(
  sessionId: string,
  fid: string,
  body: Partial<FeatureCreate>,
): Promise<MapFeature> {
  return apiFetch<MapFeature>(`/sessions/${sessionId}/map-features/${fid}`, { method: 'PATCH', body })
}
export async function deleteMapFeature(sessionId: string, fid: string): Promise<void> {
  await apiFetch<unknown>(`/sessions/${sessionId}/map-features/${fid}`, { method: 'DELETE' })
}

// ---- 顯示 ----
export const FEATURE_KINDS = [
  { value: 'OBSTACLE', label: '障礙', color: '#ef4444' },
  { value: 'BUILDING', label: '建築', color: '#94a3b8' },
  { value: 'WEAPON_EMPLACEMENT', label: '武器據點', color: '#f59e0b' },
  { value: 'CONTROL_MEASURE', label: '控制措施', color: '#38bdf8' },
  { value: 'TERRAIN', label: '地形', color: '#a16207' },
  { value: 'ANNOTATION', label: '標註', color: '#22d3ee' },
]
export function featureColor(kind: string): string {
  return FEATURE_KINDS.find((k) => k.value === kind)?.color ?? '#22d3ee'
}

type FC = { type: 'FeatureCollection'; features: unknown[] }
const EMPTY: FC = { type: 'FeatureCollection', features: [] }

/** MapFeature 的存放幾何（POINT=[lng,lat]、LINE=[[lng,lat]…]、POLYGON=單環 [[lng,lat]…]）→ GeoJSON geometry。 */
function toGeometry(f: MapFeature): { type: string; coordinates: unknown } | null {
  const g = f.geometry as unknown
  if (f.geometry_type === 'POINT') return { type: 'Point', coordinates: g }
  if (f.geometry_type === 'LINE') return { type: 'LineString', coordinates: g }
  if (f.geometry_type === 'POLYGON') {
    const ring = (g as number[][]) ?? []
    if (ring.length < 3) return null
    return { type: 'Polygon', coordinates: [[...ring, ring[0]!]] }
  }
  return null
}

/** 由所有 feature 組 GeoJSON（含 color/kind/id 供分層渲染）。 */
export function featuresToFc(features: MapFeature[]): FC {
  const out: unknown[] = []
  for (const f of features) {
    const geometry = toGeometry(f)
    if (!geometry) continue
    out.push({
      type: 'Feature',
      properties: {
        id: f.id,
        kind: f.kind,
        owner: f.owner_faction,
        color: featureColor(f.kind),
        gtype: f.geometry_type,
      },
      geometry,
    })
  }
  return { type: 'FeatureCollection', features: out }
}

/** 依 radius（公尺）在中心生成近似圓多邊形環（無 turf 相依）。 */
export function genCircle(lng: number, lat: number, radiusM: number, steps = 48): number[][] {
  const ring: number[][] = []
  const latR = (radiusM / 6378137) * (180 / Math.PI)
  const lngR = latR / Math.cos((lat * Math.PI) / 180)
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * 2 * Math.PI
    ring.push([lng + lngR * Math.cos(t), lat + latR * Math.sin(t)])
  }
  return ring
}

/** 影響範圍圓（點/線的第一點為中心）→ GeoJSON 多邊形集。 */
export function influenceToFc(features: MapFeature[]): FC {
  const out: unknown[] = []
  for (const f of features) {
    if (!f.influence_radius_m || f.influence_radius_m <= 0) continue
    const g = f.geometry as unknown
    const center =
      f.geometry_type === 'POINT' ? (g as number[]) : ((g as number[][])?.[0] ?? null)
    if (!center) continue
    out.push({
      type: 'Feature',
      properties: { id: f.id, color: featureColor(f.kind) },
      geometry: { type: 'Polygon', coordinates: [genCircle(center[0]!, center[1]!, f.influence_radius_m)] },
    })
  }
  return { type: 'FeatureCollection', features: out }
}

/** 繪製中的草稿（已點的頂點 + 線/面預覽）→ GeoJSON。 */
export function draftToFc(kind: DraftKind | null, coords: number[][]): FC {
  if (!kind || !coords.length) return EMPTY
  const feats: unknown[] = coords.map((c) => ({
    type: 'Feature',
    properties: { vertex: true },
    geometry: { type: 'Point', coordinates: c },
  }))
  if (kind === 'LINE' && coords.length >= 2) {
    feats.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } })
  }
  if (kind === 'POLYGON' && coords.length >= 3) {
    feats.push({
      type: 'Feature',
      properties: {},
      geometry: { type: 'Polygon', coordinates: [[...coords, coords[0]!]] },
    })
  } else if (kind === 'POLYGON' && coords.length === 2) {
    feats.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } })
  }
  return { type: 'FeatureCollection', features: feats }
}
