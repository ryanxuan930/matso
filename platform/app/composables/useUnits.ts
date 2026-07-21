// 單位/contact 型別 + fog of war 樣式邏輯（O4.4，SPEC §13.3）——純函數，可測。

// faction＝想定定義字串 id（SPEC §12.1/ADR 006），非封閉集合；WHITE_CELL 為統裁保留字。
export type Faction = string
export type CommsState = 'ONLINE' | 'DEGRADED' | 'OFFLINE'
export type Fidelity = 'DETECTED' | 'CLASSIFIED' | 'IDENTIFIED'

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
 * 敵方 contact 依情報等級（SPEC §13.3）：
 * DETECTED → 未知（U，黃色菱形，無兵種）；CLASSIFIED → 疑敵（S）+ 兵種；IDENTIFIED → 敵軍（H）+ 兵種。
 */
export function sidcForContact(c: Contact): string {
  if (c.fidelity === 'DETECTED') return buildSidc('U')
  if (c.fidelity === 'CLASSIFIED') return buildSidc('S', c.unitType)
  return buildSidc('H', c.unitType)
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
    push(sidcForOwnUnit(u), options, u.lng, u.lat, ownUnitOpacity(u.comms), 'own')
  }
  for (const c of contacts) {
    const options: SymbolOpts =
      c.fidelity === 'IDENTIFIED' && c.designation ? { uniqueDesignation: c.designation } : {}
    const opacity = stalenessOpacity(Math.max(0, currentTick - c.lastSeenTick))
    push(sidcForContact(c), options, c.lng, c.lat, opacity, 'contact')
  }

  return { collection: { type: 'FeatureCollection', features }, icons: [...iconMap.values()] }
}
