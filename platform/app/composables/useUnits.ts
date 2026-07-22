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
  health?: number // 0–100 HP（僅我方；供地圖血量環 + 資訊卡 #5）。fog of war：contact 無血量。
}

/** 編制層級 → 中文（兵-伍-班-排…，#5.3；與想定編輯器同義）。 */
export const UNIT_LEVEL_LABELS: Record<string, string> = {
  INDIVIDUAL: '兵', FIRETEAM: '伍', SQUAD: '班', PLATOON: '排', COMPANY: '連',
  BATTALION: '營', BRIGADE: '旅', DIVISION: '師', CORPS: '軍', THEATER: '戰區',
}
export function unitLevelLabel(l?: string): string {
  return (l && UNIT_LEVEL_LABELS[l]) || l || '—'
}
/** 通聯狀態 → 中文。 */
export function commsLabel(c?: string): string {
  return c === 'ONLINE' ? '即時通聯' : c === 'DEGRADED' ? '通聯不良' : c === 'OFFLINE' ? '失聯' : c || '—'
}
/** 血量 → 顏色帶（綠/琥珀/紅）——地圖血量環與資訊卡共用。 */
export function healthColor(pct: number): string {
  return pct < 34 ? '#ef4444' : pct < 67 ? '#f59e0b' : '#22c55e'
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
  health?: number // 敵情血量（活模擬 STATE_DIFF ground truth）——供地圖血量環/摧毀顯示
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

/** 被摧毀（health≤0）的單位淡化顯示（乘 0.3），讓戰損在地圖上一望即知（補充 2a）。 */
export function destroyedFade(health: number | undefined, base: number): number {
  return health != null && health <= 0 ? base * 0.3 : base
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
  // id/faction 供地圖點選命中與高亮（選取藍環 / 目標紅環）與 ENGAGE 目標鎖定（O4.5 UX 改版）。
  // health 僅我方（供血量環 #5）；contact 依 fog of war 不帶血量。
  properties: {
    id: string
    faction: string
    icon: string
    opacity: number
    kind: 'own' | 'contact'
    health?: number
  }
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
    id: string,
    faction: string,
    sidc: string,
    options: SymbolOpts,
    lng: number,
    lat: number,
    opacity: number,
    kind: 'own' | 'contact',
    health?: number,
  ) => {
    const key = iconKey(sidc, options)
    if (!iconMap.has(key)) iconMap.set(key, { key, sidc, options })
    features.push({
      type: 'Feature',
      properties: { id, faction, icon: key, opacity, kind, ...(health != null ? { health } : {}) },
      geometry: { type: 'Point', coordinates: [lng, lat] },
    })
  }

  for (const u of own) {
    const options: SymbolOpts = isGhost(u)
      ? { additionalInformation: `OFFLINE +${Math.max(0, currentTick - u.lastReportedTick)}t` }
      : {}
    options.fillColor = factionColor(u.faction, palette) // 多陣營顏色區分（§12.1）
    push(u.id, u.faction, sidcForOwnUnit(u), options, u.lng, u.lat, destroyedFade(u.health, ownUnitOpacity(u.comms)), 'own', u.health)
  }
  for (const c of contacts) {
    const options: SymbolOpts =
      c.fidelity === 'IDENTIFIED' && c.designation ? { uniqueDesignation: c.designation } : {}
    // IDENTIFIED 且已知陣營 → 以該陣營顏色渲染（三方混戰時區分不同敵對陣營）。
    if (c.faction) options.fillColor = factionColor(c.faction, palette)
    const opacity = stalenessOpacity(Math.max(0, currentTick - c.lastSeenTick))
    push(c.contactId, c.faction ?? '', sidcForContact(c), options, c.lng, c.lat, destroyedFade(c.health, opacity), 'contact', c.health)
  }

  return { collection: { type: 'FeatureCollection', features }, icons: [...iconMap.values()] }
}
