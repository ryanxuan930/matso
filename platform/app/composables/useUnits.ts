// 單位/contact 型別 + fog of war 樣式邏輯（O4.4，SPEC §13.3）——純函數，可測。

// faction＝想定定義字串 id（SPEC §12.1/ADR 006），非封閉集合；WHITE_CELL 為統裁保留字。
export type Faction = string
export type CommsState = 'ONLINE' | 'DEGRADED' | 'OFFLINE'
export type Fidelity = 'DETECTED' | 'CLASSIFIED' | 'IDENTIFIED'
// 觀測者對某陣營的關係（SPEC §12.1）——決定 contact 的 2525 affiliation。
export type Relation = 'ALLIED' | 'NEUTRAL' | 'HOSTILE'

/** 陣營顯示色調色盤（想定 factions[].color 可覆寫；此為預設，讓多陣營視覺可區分）。 */
export const DEFAULT_FACTION_COLORS: Record<string, string> = {
  BLUE: '#3b7dd8',
  RED: '#d83b3b',
  YELLOW: '#d8c53b',
  GREEN: '#3bd86b',
  PURPLE: '#9b3bd8',
}
const _FALLBACK_COLORS = ['#e07b39', '#39b0e0', '#b0e039', '#e039b0', '#39e0c5']

/** 由 faction id 取顯示色：想定 palette 優先 → 預設表 → 確定性 fallback（依 id 雜湊）。 */
export function factionColor(faction: string, palette: Record<string, string> = {}): string {
  const declared = palette[faction] ?? DEFAULT_FACTION_COLORS[faction]
  if (declared) return declared
  let h = 0
  for (const ch of faction) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return _FALLBACK_COLORS[h % _FALLBACK_COLORS.length]!
}

/** 關係 → 2525 affiliation 字母：ALLIED=F(友)、NEUTRAL=N(中)、HOSTILE=H(敵)。 */
export function affiliationForRelation(rel: Relation): string {
  return rel === 'ALLIED' ? 'F' : rel === 'NEUTRAL' ? 'N' : 'H'
}

/** 己方單位（STATE_DIFF 餵入）。 */
export interface OwnUnit {
  id: string
  faction: Faction
  lat: number
  lng: number
  unitType?: string
  comms: CommsState
  lastReportedTick: number
}

/** 敵方 contact（INTEL_UPDATE 餵入；ContactView 投影，已去識別化）。 */
export interface Contact {
  contactId: string
  fidelity: Fidelity
  lat: number
  lng: number
  errorRadiusM: number
  unitType?: string
  designation?: string
  lastSeenTick: number
  faction?: string // IDENTIFIED 才揭露（後端去識別化）——用於顏色與 affiliation
  relation?: Relation // 觀測者對該 contact 陣營的關係（IDENTIFIED 時已知）
}

// 少數 2525C function ID（SPEC 未細列，取常見兵種；DETECTED 未知 → 通用）。
const FUNCTION_ID: Record<string, string> = {
  INFANTRY: 'UCI---',
  ARMOR: 'UCA---',
  ARTILLERY: 'UCF---',
  RECON: 'UCR---',
  HQ: 'UH----',
  AIR: 'MFA---',
}

function functionId(type?: string): string {
  return (type && FUNCTION_ID[type]) || 'U-----'
}

/** 組 15 字元 2525C SIDC：S{affiliation}GP{functionId}…（padEnd 到 15）。 */
export function buildSidc(affiliation: string, type?: string): string {
  return `S${affiliation}GP${functionId(type)}`.padEnd(15, '-').slice(0, 15)
}

/** 己方單位一律以友軍（F）符號呈現（不論觀測者陣營）。 */
export function sidcForOwnUnit(u: OwnUnit): string {
  return buildSidc('F', u.unitType)
}

/**
 * 敵方 contact 依情報等級（SPEC §13.3）+ N 方關係（§12.1）：
 * DETECTED → 未知（U）；CLASSIFIED → 疑敵（S）+ 兵種；
 * IDENTIFIED → 依觀測者對該陣營關係定 affiliation（HOSTILE=H、NEUTRAL=N、ALLIED=F）+ 兵種。
 * relation 未知（如未 IDENTIFIED 或後端未給）時，IDENTIFIED 退回 H（保守視為敵）。
 */
export function sidcForContact(c: Contact): string {
  if (c.fidelity === 'DETECTED') return buildSidc('U')
  if (c.fidelity === 'CLASSIFIED') return buildSidc('S', c.unitType)
  const affiliation = c.relation ? affiliationForRelation(c.relation) : 'H'
  return buildSidc(affiliation, c.unitType)
}

/** 情報時效透明度：愈舊愈淡（下限 0.25）。 */
export function stalenessOpacity(ageTicks: number, maxAgeTicks = 120): number {
  const o = 1 - Math.max(ageTicks, 0) / maxAgeTicks
  return Math.min(1, Math.max(0.25, o))
}

/** OFFLINE 己方單位＝虛影（最後回報位置 + 淡化）。 */
export function ownUnitOpacity(comms: CommsState): number {
  if (comms === 'OFFLINE') return 0.4
  if (comms === 'DEGRADED') return 0.75
  return 1
}

export function isGhost(u: OwnUnit): boolean {
  return u.comms === 'OFFLINE'
}

// ---------------- GeoJSON 特徵 + icon 規格（供 MapLibre symbol 層） ----------------

export type SymbolOpts = Record<string, string>

/** icon 快取鍵 = SIDC + 選項（designation / OFFLINE 文字影響外觀 → 併入鍵）。 */
export function iconKey(sidc: string, options: SymbolOpts = {}): string {
  return `${sidc}|${JSON.stringify(options)}`
}

export interface IconSpec {
  key: string
  sidc: string
  options: SymbolOpts
}

export interface UnitFeature {
  type: 'Feature'
  properties: { icon: string; opacity: number; kind: 'own' | 'contact' }
  geometry: { type: 'Point'; coordinates: [number, number] }
}

export interface UnitRender {
  collection: { type: 'FeatureCollection'; features: UnitFeature[] }
  icons: IconSpec[]
}

/**
 * 己方單位 + 敵方 contact → GeoJSON 特徵 + 去重的 icon 規格（純函數，可測）。
 * OFFLINE 己方＝虛影（additionalInformation 烤「OFFLINE +Nt」+ 淡化）；IDENTIFIED contact 揭露番號；
 * contact 透明度依情報時效遞減。
 */
export function buildUnitFeatures(
  own: OwnUnit[],
  contacts: Contact[],
  currentTick: number,
  palette: Record<string, string> = {},
): UnitRender {
  const features: UnitFeature[] = []
  const iconMap = new Map<string, IconSpec>()

  const push = (
    sidc: string,
    options: SymbolOpts,
    lng: number,
    lat: number,
    opacity: number,
    kind: 'own' | 'contact',
  ) => {
    const key = iconKey(sidc, options)
    if (!iconMap.has(key)) iconMap.set(key, { key, sidc, options })
    features.push({
      type: 'Feature',
      properties: { icon: key, opacity, kind },
      geometry: { type: 'Point', coordinates: [lng, lat] },
    })
  }

  for (const u of own) {
    const options: SymbolOpts = isGhost(u)
      ? { additionalInformation: `OFFLINE +${Math.max(0, currentTick - u.lastReportedTick)}t` }
      : {}
    options.fillColor = factionColor(u.faction, palette) // 多陣營顏色區分（§12.1）
    push(sidcForOwnUnit(u), options, u.lng, u.lat, ownUnitOpacity(u.comms), 'own')
  }
  for (const c of contacts) {
    const options: SymbolOpts =
      c.fidelity === 'IDENTIFIED' && c.designation ? { uniqueDesignation: c.designation } : {}
    // IDENTIFIED 且已知陣營 → 以該陣營顏色渲染（三方混戰時區分不同敵對陣營）。
    if (c.faction) options.fillColor = factionColor(c.faction, palette)
    const opacity = stalenessOpacity(Math.max(0, currentTick - c.lastSeenTick))
    push(sidcForContact(c), options, c.lng, c.lat, opacity, 'contact')
  }

  return { collection: { type: 'FeatureCollection', features }, icons: [...iconMap.values()] }
}
