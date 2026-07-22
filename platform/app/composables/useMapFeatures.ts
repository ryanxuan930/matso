// 地圖標註/工事（stage ③b）——CRUD + 純函數建 GeoJSON（供 MapCanvas 渲染）。
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type MapFeature = components['schemas']['MapFeatureView']
// 繪製工具（#11 ATAK 式）：點/線/多邊形 + 圓形/矩形（兩者以 2 點繪製，存為 POLYGON）。
export type DraftKind = 'POINT' | 'LINE' | 'POLYGON' | 'CIRCLE' | 'RECTANGLE'

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
/** 特徵顯示色：自訂 attributes.color 優先，否則類別預設色（#11）。 */
export function featureDisplayColor(f: MapFeature): string {
  const c = (f.attributes as Record<string, unknown> | undefined)?.color
  return typeof c === 'string' && c ? c : featureColor(f.kind)
}

const R_EARTH = 6378137
/** 兩點（[lng,lat]）間近似距離（公尺）——供圓形半徑。 */
export function haversineM(a: number[], b: number[]): number {
  const toR = Math.PI / 180
  const dLat = (b[1]! - a[1]!) * toR
  const dLng = (b[0]! - a[0]!) * toR
  const la1 = a[1]! * toR
  const la2 = b[1]! * toR
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2
  return 2 * R_EARTH * Math.asin(Math.min(1, Math.sqrt(h)))
}
/** 兩對角點 → 軸對齊矩形環（POLYGON 單環，未閉合；#11）。 */
export function rectRing(a: number[], b: number[]): number[][] {
  const [x0, x1] = [Math.min(a[0]!, b[0]!), Math.max(a[0]!, b[0]!)]
  const [y0, y1] = [Math.min(a[1]!, b[1]!), Math.max(a[1]!, b[1]!)]
  return [
    [x0, y0],
    [x1, y0],
    [x1, y1],
    [x0, y1],
  ]
}

// 常用北約 2525C 符號（點特徵可掛，#11）——標籤 → 15 碼 SIDC。空＝不掛符號（走一般圓點）。
export const NATO_SYMBOLS: { sidc: string; label: string }[] = [
  { sidc: '', label: '（無符號 · 圓點）' },
  { sidc: 'SFGPUCI--------', label: '友軍 步兵' },
  { sidc: 'SFGPUCA--------', label: '友軍 裝甲' },
  { sidc: 'SFGPUCF--------', label: '友軍 砲兵' },
  { sidc: 'SFGPUH---------', label: '友軍 指揮所' },
  { sidc: 'SFGPUCR--------', label: '友軍 偵蒐' },
  { sidc: 'SHGPUCI--------', label: '敵軍 步兵' },
  { sidc: 'SHGPUCA--------', label: '敵軍 裝甲' },
  { sidc: 'SHGPUCF--------', label: '敵軍 砲兵' },
  { sidc: 'SNGPU----------', label: '中立' },
  { sidc: 'SUGPU----------', label: '未知' },
]

type FC = { type: 'FeatureCollection'; features: unknown[] }
const EMPTY: FC = { type: 'FeatureCollection', features: [] }

/** 點特徵的 SIDC（attributes.sidc），無則空字串。 */
export function featureSidc(f: MapFeature): string {
  const s = (f.attributes as Record<string, unknown> | undefined)?.sidc
  return typeof s === 'string' ? s : ''
}

export interface FeatureSymbol {
  fc: FC
  icons: { key: string; sidc: string }[]
}
/** 帶 SIDC 的點特徵 → 符號 GeoJSON（icon=SIDC）+ 去重的 icon 規格（供 MapCanvas 生成 milsymbol）。 */
export function featureSymbolFc(features: MapFeature[]): FeatureSymbol {
  const out: unknown[] = []
  const iconMap = new Map<string, { key: string; sidc: string }>()
  for (const f of features) {
    if (f.geometry_type !== 'POINT') continue
    const sidc = featureSidc(f)
    if (!sidc) continue
    iconMap.set(sidc, { key: sidc, sidc })
    out.push({
      type: 'Feature',
      properties: { id: f.id, icon: sidc, label: f.label ?? '' },
      geometry: { type: 'Point', coordinates: f.geometry },
    })
  }
  return { fc: { type: 'FeatureCollection', features: out }, icons: [...iconMap.values()] }
}

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
        color: featureDisplayColor(f),
        gtype: f.geometry_type,
        label: f.label ?? '',
        hasSym: f.geometry_type === 'POINT' && featureSidc(f) !== '',
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

/** 由中心 + 半徑 + 方向 + 張角生成扇形環（武器射向/雷達扇區，#11）。方向：0=正北、順時針度數。 */
export function genSector(
  lng: number,
  lat: number,
  radiusM: number,
  dirDeg: number,
  arcDeg: number,
  steps = 32,
): number[][] {
  const latR = (radiusM / 6378137) * (180 / Math.PI)
  const lngR = latR / Math.cos((lat * Math.PI) / 180)
  const start = dirDeg - arcDeg / 2
  const ring: number[][] = [[lng, lat]]
  for (let i = 0; i <= steps; i++) {
    const b = ((start + (arcDeg * i) / steps) * Math.PI) / 180 // 方位角（北為 0、順時針）
    ring.push([lng + lngR * Math.sin(b), lat + latR * Math.cos(b)])
  }
  ring.push([lng, lat])
  return ring
}

/** 特徵的方向/張角（attributes；武器射向扇區、雷達扇區）。無張角或 ≥360 → 全圓。 */
function sectorParams(f: MapFeature): { dir: number; arc: number } | null {
  const a = f.attributes as Record<string, unknown> | undefined
  const arc = typeof a?.arc_deg === 'number' ? a.arc_deg : NaN
  if (!Number.isFinite(arc) || arc <= 0 || arc >= 360) return null
  const dir = typeof a?.direction_deg === 'number' ? a.direction_deg : 0
  return { dir, arc }
}

/** 影響/射程範圍（武器扇區/射程圓、雷達探測圓/扇區）——點/線第一點為中心 → GeoJSON 多邊形集。 */
export function influenceToFc(features: MapFeature[]): FC {
  const out: unknown[] = []
  for (const f of features) {
    if (!f.influence_radius_m || f.influence_radius_m <= 0) continue
    const g = f.geometry as unknown
    const center =
      f.geometry_type === 'POINT' ? (g as number[]) : ((g as number[][])?.[0] ?? null)
    if (!center) continue
    const sec = sectorParams(f)
    const ring = sec
      ? genSector(center[0]!, center[1]!, f.influence_radius_m, sec.dir, sec.arc)
      : genCircle(center[0]!, center[1]!, f.influence_radius_m)
    out.push({
      type: 'Feature',
      properties: { id: f.id, color: featureDisplayColor(f) },
      geometry: { type: 'Polygon', coordinates: [ring] },
    })
  }
  return { type: 'FeatureCollection', features: out }
}

/** 繪製中的草稿（已點的頂點 + 線/面/圓/矩預覽）→ GeoJSON。 */
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
  if (kind === 'RECTANGLE' && coords.length >= 2) {
    const ring = rectRing(coords[0]!, coords[1]!)
    feats.push({
      type: 'Feature',
      properties: {},
      geometry: { type: 'Polygon', coordinates: [[...ring, ring[0]!]] },
    })
  }
  if (kind === 'CIRCLE' && coords.length >= 2) {
    const ring = genCircle(coords[0]![0]!, coords[0]![1]!, haversineM(coords[0]!, coords[1]!))
    feats.push({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } })
  }
  return { type: 'FeatureCollection', features: feats }
}

/** 兩點草稿 → 存放用 POLYGON 幾何環（圓/矩，#11）。 */
export function shapeToPolygon(kind: DraftKind, coords: number[][]): number[][] | null {
  if (coords.length < 2) return null
  if (kind === 'RECTANGLE') return rectRing(coords[0]!, coords[1]!)
  if (kind === 'CIRCLE') return genCircle(coords[0]![0]!, coords[0]![1]!, haversineM(coords[0]!, coords[1]!))
  return null
}
