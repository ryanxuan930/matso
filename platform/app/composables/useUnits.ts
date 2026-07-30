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

// ---- 血條桶號（#94）----
// 圖標上方血條以 5% 為一桶：肉眼分辨不出 1% 差異，卻能把 addImage 從 101 次降到 21 次。
// 純算部分放這裡（本模組無任何相依）；canvas 繪製在 useMilsymbol。
export const HP_BAR_PREFIX = 'unit-hp-bar-'
export const HP_BAR_STEP = 5

/** 血量 → 桶號（0/5/…/100）。圖層 icon-image 與 addImage 共用同一套鍵。 */
export function hpBucket(pct: number): number {
  return Math.round(Math.min(100, Math.max(0, pct)) / HP_BAR_STEP) * HP_BAR_STEP
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
  /** APP-6A Field T「Unique Designation」——番號。**沒有它地圖上的單位就是無名的方塊**。 */
  designation?: string
  unitType?: string
  /** 編制層級（APP-6A Field B 階層符號的來源；後端 `UnitView.unit_level` 已供應）。 */
  unitLevel?: string
  comms: CommsState
  lastReportedTick: number
  health?: number // 0–100 HP（僅我方；供地圖血量環 + 資訊卡 #5）。fog of war：contact 無血量。
  isFixed?: boolean // 固定單位（指揮部等）：地圖符號加鎖頭徽章（只我方；不洩漏敵方編成）。
}

/** 編制層級 → 中文（兵-伍-班-排…，#5.3；與想定編輯器同義）。 */
export const UNIT_LEVEL_LABELS: Record<string, string> = {
  INDIVIDUAL: '兵', FIRETEAM: '伍', SQUAD: '班', SECTION: '組', PLATOON: '排',
  COMPANY: '連', BATTALION: '營', REGIMENT: '團', BRIGADE: '旅', DIVISION: '師',
  CORPS: '軍', ARMY: '軍團', ARMY_GROUP: '集團軍', THEATER: '戰區',
}
export function unitLevelLabel(l?: string): string {
  return (l && UNIT_LEVEL_LABELS[l]) || l || '—'
}
/** 通聯狀態 → 中文。 */
export function commsLabel(c?: string): string {
  return c === 'ONLINE' ? '即時通聯' : c === 'DEGRADED' ? '通聯不良' : c === 'OFFLINE' ? '失聯' : c || '—'
}
/**
 * 姿態（WP-C1）→ 中文 + 說明。**已就位**的那一級（轉換要時間，期間仍算前一級）。
 * 括號裡的數字是被命中率修正——防禦方的準備工作值不值得，指揮官要看得到才決定得了。
 */
export const POSTURE_LABELS: Record<string, { text: string; hint: string }> = {
  MOVING: { text: '行進', hint: '無掩蔽（被命中率 ×1.0）' },
  HASTY: { text: '臨時掩蔽', hint: '就地臥倒/利用地物（×0.85，即時生效）' },
  DEFENSE: { text: '準備陣地', hint: '構築中的陣地（×0.7，需 30 分鐘）' },
  DUG_IN: { text: '掘壕固守', hint: '完整工事（×0.5，需 4 小時）' },
}
export function postureLabel(p?: string): string {
  return (p && POSTURE_LABELS[p]?.text) || p || '—'
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
  /** 編制層級（CLASSIFIED+ 揭露）。後端過去把它塞在 `unit_type` 裡，已改名分家。 */
  echelon?: string
  designation?: string
  lastSeenTick: number
  faction?: string // IDENTIFIED 才揭露（後端去識別化）——用於顏色與 affiliation
  relation?: Relation // 觀測者對該 contact 陣營的關係（IDENTIFIED 時已知）
  health?: number // 敵情血量（活模擬 STATE_DIFF ground truth）——供地圖血量環/摧毀顯示
}

/**
 * 兵科 → 2525C function ID（SIDC 第 5–10 位）。
 *
 * 每一個代碼都**實測過** milsymbol 3.0.4 會畫出獨特的圖示——不是只驗 `isValid()`：
 * 有些代碼合法卻與通用框畫得一模一樣，那種等於沒設定。
 *
 * `UNKNOWN`／查不到 → `U-----` 通用框。這是**中性預設**，沒指定兵科的單位外觀不變。
 */
const FUNCTION_ID: Record<string, string> = {
  INFANTRY: 'UCI---',
  ARMOR: 'UCA---',
  RECON: 'UCR---',
  ARTILLERY: 'UCF---',
  AIR_DEFENSE: 'UCD---',
  ENGINEER: 'UCE---',
  MISSILE: 'UCM---',
  AVIATION: 'UCV---',
  SIGNAL: 'UUS---',
  INTEL: 'UUM---',
  SUPPLY: 'USS---',
  MEDICAL: 'USM---',
  MAINTENANCE: 'USX---',
  TRANSPORT: 'UST---',
  HQ: 'UH----',
  AIR: 'MFA---', // 既有值，保留相容
}

/** 兵科 → 中文（ORBAT 編輯器與資訊卡共用）。 */
export const BRANCH_LABELS: Record<string, string> = {
  UNKNOWN: '未指定',
  INFANTRY: '步兵',
  ARMOR: '裝甲',
  RECON: '偵搜',
  ARTILLERY: '砲兵',
  AIR_DEFENSE: '防空',
  ENGINEER: '工兵',
  MISSILE: '飛彈',
  AVIATION: '航空',
  SIGNAL: '通信',
  INTEL: '情報',
  SUPPLY: '補給',
  MEDICAL: '衛勤',
  MAINTENANCE: '保養',
  TRANSPORT: '運輸',
  HQ: '指揮部',
}
export const BRANCH_OPTIONS = Object.keys(BRANCH_LABELS)

function functionId(type?: string): string {
  return (type && FUNCTION_ID[type]) || 'U-----'
}

/**
 * 編制層級 → 2525C SIDC 第 12 位（APP-6A Field B「Size Indicator」/ Table IV）。
 *
 * ⚠ `INDIVIDUAL` 對應 `'-'`（不畫階層符號）：**APP-6A Table IV 最小的一級是 Team/Crew**，
 * 標準裡沒有「個人」這一級，硬塞 `'A'` 是造假。
 *
 * `N`（Command）仍未用到——那是編制模型還沒有的層級，不是填錯。
 */
export const ECHELON_BY_LEVEL: Record<string, string> = {
  INDIVIDUAL: '-', // Table IV 沒有這一級
  FIRETEAM: 'A', // Team/Crew
  SQUAD: 'B',
  SECTION: 'C',
  PLATOON: 'D',
  COMPANY: 'E',
  BATTALION: 'F',
  REGIMENT: 'G', // Regiment/Group
  BRIGADE: 'H',
  DIVISION: 'I',
  CORPS: 'J',
  ARMY: 'K',
  ARMY_GROUP: 'L', // Army Group/Front
  THEATER: 'M', // Region
}

export function echelonFor(unitLevel?: string): string {
  return (unitLevel && ECHELON_BY_LEVEL[unitLevel]) || '-'
}

/**
 * 組 15 字元 2525C SIDC。**明確排到 15 位，不再靠 padEnd**。
 *
 * `S` + affiliation + `G`(ground) + `P`(present) + functionId(6) + mod1(1) + echelon(1) + `---`
 *
 * 過去是 `\`S${aff}GP${fn}\`.padEnd(15,'-')`，於是第 11–12 位**永遠是 `--`**
 * ——地圖上沒有任何階層標記（連/營/旅的橫槓一條都沒有）。
 *
 * `mod1`（第 11 位，修飾指示）目前恆為 `'-'`：HQ(`A`)/TF(`B,D,E,G`)/
 * Feint-Dummy(`C,D,F,G`) 三者本系統都還沒有資料來源。
 * ⚠ **不可以拿 `is_fixed` 去頂替 HQ 或 Installation**——`is_fixed` 的定義是
 * 「不接受 MOVE 令」，涵蓋指揮部/後勤點/固定陣地三種完全不同的東西，
 * 映成設施會把彈藥堆積所畫成師部。鎖頭徽章維持既有做法（非 APP-6A，本系統 UI 擴充）。
 */
export function buildSidc(
  affiliation: string,
  type?: string,
  echelon = '-',
  mod1 = '-',
): string {
  return `S${affiliation}GP${functionId(type)}${mod1}${echelon}---`
}

/** 己方單位一律以友軍（F）符號呈現（不論觀測者陣營）。 */
export function sidcForOwnUnit(u: OwnUnit): string {
  return buildSidc('F', u.unitType, echelonFor(u.unitLevel))
}

/**
 * 敵方 contact 依情報等級（SPEC §13.3）+ N 方關係（§12.1）：
 * DETECTED → 未知（U）；CLASSIFIED → 疑敵（S）+ 兵種；
 * IDENTIFIED → 依觀測者對該陣營關係定 affiliation（HOSTILE=H、NEUTRAL=N、ALLIED=F）+ 兵種。
 * relation 未知（如未 IDENTIFIED 或後端未給）時，IDENTIFIED 退回 H（保守視為敵）。
 */
export function sidcForContact(c: Contact): string {
  if (c.fidelity === 'DETECTED') return buildSidc('U')
  // 階層（Field B）與兵科（Field A）都閘在 CLASSIFIED+（後端 `intel/service.py`）。
  // 前端這裡再判一次是**刻意的縱深防禦**：未達等級時後端就不給值，這裡也不畫。
  const echelon = echelonFor(c.echelon)
  if (c.fidelity === 'CLASSIFIED') return buildSidc('S', c.unitType, echelon)
  const affiliation = c.relation ? affiliationForRelation(c.relation) : 'H'
  return buildSidc(affiliation, c.unitType, echelon)
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
    /** 錨點補償（見 useMilsymbol 的 anchorOffsets）；由 MapCanvas 在圖標生成後回填。 */
    iconOffset?: [number, number]
    opacity: number
    kind: 'own' | 'contact'
    health?: number
    fixed?: boolean // 固定單位（指揮部等）：驅動地圖鎖頭徽章層（只我方）
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
/**
 * 符號詳細度（APP-6A §506.1 明文授權：「C4I 系統對顯示資訊量的需求不同，本標準允許彈性；
 * 指揮官可以選擇對友軍只顯示極少資訊、對威脅顯示最多」）。**預設少顯示是行使標準給的選項，
 * 不是實作偷懶。**
 *
 * 這同時是**演算法複雜度的開關**，不只是視覺潔癖：`iconKey()` 以 SIDC + options JSON 為鍵，
 * 每個相異組合就是一張 rasterize 後 addImage 的點陣圖。
 * - `MIN`：純 SIDC + 陣營色 → 圖檔數是 **O(詞彙量)**（affiliation × 兵種 × 階層 × 陣營色，
 *   實務約 30–150 張），與單位數無關。
 * - `STD`：加番號（Field T）→ 每個單位的番號都不同，圖檔數立刻變成 **O(單位數)**。
 *   500 單位就是 500 張圖。這是為了看得到名字必須付的代價。
 */
export type SymbolDetail = 'MIN' | 'STD' | 'FULL'

/** 超過這個可視符號數就強制降級到 MIN（否則 500 張圖 + 字牆）。 */
export const AUTO_DEMOTE_ABOVE = 300

/**
 * 失聯時長分桶（**5 tick 一桶**）。
 *
 * ⚠ 這裡曾經直接烤 `OFFLINE +${currentTick - lastReportedTick}t`——那個字串**每 tick 都變**，
 * 於是每個失聯單位每 tick 都產生一張新圖標。`useMilsymbol` 的 cache 是無上限 Map、
 * `map.addImage` 也從不 removeImage，長局 CPX 會慢慢吃光顯存，
 * 而圖集撐爆的症狀是**圖標無聲消失**、不報錯、極難歸因。分桶與 #94 的 `hpBucket` 同紀律。
 */
export function staleBucket(ageTicks: number): number {
  return Math.max(0, Math.floor(ageTicks / 5) * 5)
}

export function buildUnitFeatures(
  own: OwnUnit[],
  contacts: Contact[],
  currentTick: number,
  palette: Record<string, string> = {},
  detail: SymbolDetail = 'STD',
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
    fixed?: boolean,
  ) => {
    const key = iconKey(sidc, options)
    if (!iconMap.has(key)) iconMap.set(key, { key, sidc, options })
    features.push({
      type: 'Feature',
      properties: {
        id, faction, icon: key, opacity, kind,
        ...(health != null ? { health } : {}),
        // #94 血條圖標鍵：以 5% 為桶（見 hpBucket），讓圖層直接 ['get','hpIcon'] 取圖。
        ...(health != null ? { hpIcon: `${HP_BAR_PREFIX}${hpBucket(health)}` } : {}),
        ...(fixed ? { fixed: true } : {}),
      },
      geometry: { type: 'Point', coordinates: [lng, lat] },
    })
  }

  // MIN 級只留 SIDC + 陣營色 → 圖檔數與單位數無關（見 SymbolDetail 說明）。
  const withText = detail !== 'MIN'
  for (const u of own) {
    const options: SymbolOpts = {}
    // APP-6A Field H「Additional Information」——僅失聯時填（DEGRADED 已用透明度表達）。
    // 時長**分桶**，不可直接烤 tick 差值（見 staleBucket）。
    if (isGhost(u) && withText) {
      options.additionalInformation = `OFFLINE +${staleBucket(currentTick - u.lastReportedTick)}t`
    }
    // APP-6A Field T「Unique Designation」。己方單位**過去完全沒帶這個選項**——
    // 只有 IDENTIFIED 的敵情 contact 有，所以自己的部隊在地圖上是一排無名方塊。
    if (u.designation && withText) options.uniqueDesignation = u.designation
    options.fillColor = factionColor(u.faction, palette) // 多陣營顏色區分（§12.1）
    push(u.id, u.faction, sidcForOwnUnit(u), options, u.lng, u.lat, destroyedFade(u.health, ownUnitOpacity(u.comms)), 'own', u.health, u.isFixed)
  }
  for (const c of contacts) {
    // ⚠ `fidelity==='IDENTIFIED' && designation` 這個雙重判斷是**刻意的縱深防禦**。
    // 後端 `intel/service.py` 已經閘在 IDENTIFIED 才給 designation，但不要因為
    // 「後端已經擋了」就把前端這層拿掉。
    const options: SymbolOpts =
      c.fidelity === 'IDENTIFIED' && c.designation && withText
        ? { uniqueDesignation: c.designation }
        : {}
    // IDENTIFIED 且已知陣營 → 以該陣營顏色渲染（三方混戰時區分不同敵對陣營）。
    if (c.faction) options.fillColor = factionColor(c.faction, palette)
    const opacity = stalenessOpacity(Math.max(0, currentTick - c.lastSeenTick))
    push(c.contactId, c.faction ?? '', sidcForContact(c), options, c.lng, c.lat, destroyedFade(c.health, opacity), 'contact', c.health)
  }

  return { collection: { type: 'FeatureCollection', features }, icons: [...iconMap.values()] }
}
