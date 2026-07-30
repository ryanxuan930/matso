/**
 * 座標輸入解析——把操作員打進去的字串變成 (lat, lng)。
 *
 * ## 為什麼要接受這麼多種格式
 *
 * 座標會從各種地方抄過來：友軍的無線電通報是度分秒、砲兵的射擊諸元是 MGRS、
 * 上級的電子文件是十進位度。逼操作員先換算再輸入，等於把換算錯誤的責任推給他
 * ——而座標打錯在兵推裡的後果是火力落在別的地方。
 *
 * ## 紀律
 *
 * **看不懂就明說看不懂**，不要猜。解析不出來時回一個帶原因的失敗，
 * 讓畫面能告訴使用者「哪裡不對」；回一個瞎猜的座標比拒絕危險得多。
 *
 * 純函式、不碰地圖、不碰網路——所以測得起來（見 `platform/tests/coord-parse.test.ts`）。
 */
import { forward, toPoint } from 'mgrs'

export interface ParsedCoord {
  lat: number
  lng: number
  /** 判定出來的輸入格式，供畫面回饋「我把它讀成什麼」。 */
  format: 'DECIMAL' | 'DMS' | 'MGRS'
  /**
   * 需要提醒操作員的事（非錯誤，但值得看一眼）。目前只有一種：
   * 輸入的 MGRS 分帶字母與該點的正規分帶不符（見 `parseMgrs`）。
   */
  warning?: string
}

export type CoordParseResult =
  | { ok: true; value: ParsedCoord }
  | { ok: false; reason: string }

const LAT_RANGE = 90
const LNG_RANGE = 180

function inRange(lat: number, lng: number): boolean {
  return (
    Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    Math.abs(lat) <= LAT_RANGE &&
    Math.abs(lng) <= LNG_RANGE
  )
}

/**
 * 度分秒 → 十進位度。`hemi` 是 N/S/E/W。
 *
 * 分與秒**可省**（`24°N`、`24°05'N` 都合法），這是實務上常見的簡寫。
 */
function dmsToDeg(deg: number, min: number, sec: number, hemi: string): number {
  const value = Math.abs(deg) + min / 60 + sec / 3600
  return hemi === 'S' || hemi === 'W' ? -value : value
}

/**
 * 度分秒。支援半球字母在**前**（`N24 05 50`）或在**後**（`24°05'50"N`），
 * 分與秒可省（`24°N`、`24°05'N` 都合法——實務上常見的簡寫）。
 *
 * 做法刻意**不用一條大正規式**：度分秒的寫法變體太多（度符號、冒號、純空白、
 * 前綴/後綴半球），一條 regex 要涵蓋就會長到沒人看得懂，而且改一個變體就打破另一個。
 * 改成「以半球字母切成兩段，每段取最多三個數字」——這正是人眼在做的事。
 */
function parseDms(raw: string): CoordParseResult | null {
  const hemis = [...raw.matchAll(/[NSEWnsew]/g)]
  if (hemis.length !== 2) return null // 沒有剛好兩個半球字母就不是度分秒——不猜
  const [a, b] = hemis
  if (!a || !b) return null
  const ai = a.index ?? 0
  const bi = b.index ?? 0

  // 以兩個半球字母為界切成兩段；每一段的數字屬於「與它相鄰的那個半球字母」。
  // 前綴式（N24…E120…）與後綴式（24…N 120…E）的差別只在數字落在字母的哪一側。
  const prefixStyle = ai < raw.search(/\d/)
  const segments: { hemi: string; text: string }[] = prefixStyle
    ? [
        { hemi: a[0].toUpperCase(), text: raw.slice(ai + 1, bi) },
        { hemi: b[0].toUpperCase(), text: raw.slice(bi + 1) },
      ]
    : [
        { hemi: a[0].toUpperCase(), text: raw.slice(0, ai) },
        { hemi: b[0].toUpperCase(), text: raw.slice(ai + 1, bi) },
      ]

  const parts: { value: number; axis: 'lat' | 'lng' }[] = []
  for (const seg of segments) {
    const nums = (seg.text.match(/\d+(?:\.\d+)?/g) ?? []).slice(0, 3).map(Number)
    const deg = nums[0]
    if (deg === undefined) return { ok: false, reason: `「${seg.hemi}」旁邊找不到數字` }
    const min = nums[1] ?? 0
    const sec = nums[2] ?? 0
    if (min >= 60 || sec >= 60) {
      return { ok: false, reason: '度分秒的分與秒必須小於 60' }
    }
    parts.push({
      value: dmsToDeg(deg, min, sec, seg.hemi),
      axis: seg.hemi === 'N' || seg.hemi === 'S' ? 'lat' : 'lng',
    })
  }
  const lat = parts.find((p) => p.axis === 'lat')?.value
  const lng = parts.find((p) => p.axis === 'lng')?.value
  if (lat === undefined || lng === undefined) {
    return { ok: false, reason: '度分秒需要一個緯度（N/S）與一個經度（E/W）' }
  }
  if (!inRange(lat, lng)) return { ok: false, reason: '度分秒超出有效範圍' }
  return { ok: true, value: { lat, lng, format: 'DMS' } }
}

/** `24.0972, 120.1705` / `24.0972 120.1705`（順序一律緯度在前——與畫面顯示一致）。 */
function parseDecimal(raw: string): CoordParseResult | null {
  const nums = raw.match(/-?\d+(?:\.\d+)?/g)
  if (!nums || nums.length < 2) return null
  const lat = Number(nums[0])
  const lng = Number(nums[1])
  if (!inRange(lat, lng)) {
    return {
      ok: false,
      reason: `十進位度超出範圍（緯度 ±90、經度 ±180）；讀到 ${lat}, ${lng}`,
    }
  }
  return { ok: true, value: { lat, lng, format: 'DECIMAL' } }
}

/** MGRS：`51QTG1234567890`（分帶 + 100km 方格字母 + 偶數位數字）。 */
function parseMgrs(raw: string): CoordParseResult | null {
  const compact = raw.replace(/\s+/g, '').toUpperCase()
  if (!/^\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z]{2}\d*$/.test(compact)) return null
  const digits = compact.replace(/^\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z]{2}/, '')
  if (digits.length % 2 !== 0) {
    return { ok: false, reason: 'MGRS 的數字部分必須是偶數位（東距與北距等長）' }
  }
  try {
    const [lng, lat] = toPoint(compact)
    if (!inRange(lat, lng)) return { ok: false, reason: 'MGRS 換算結果超出有效範圍' }
    // ⚠ **分帶字母與實際位置不符要講出來。**
    // 100 km 方格代號加東北距已經定出位置，分帶字母只是粗略消歧——所以
    // `51QTG1234567890` 與 `51RTG1234567890` 會解析到同一個點，函式庫不會報錯。
    // 但對抄座標的人來說，字母不符通常代表**抄錯了一碼**，而那一碼的代價可能是
    // 火力落在別的地方。這裡不擋（解析結果是有效的），但一定要提醒。
    let warning: string | undefined
    try {
      const canonical = forward([lng, lat], (compact.length - 5) / 2)
      if (canonical !== compact) {
        warning = `輸入的分帶與該點的正規寫法不符：${compact} → ${canonical}。請確認是否抄錯。`
      }
    } catch {
      /* 正規化失敗不影響解析結果本身——不因為提醒功能壞掉就拒絕一個有效座標 */
    }
    return { ok: true, value: { lat, lng, format: 'MGRS', warning } }
  } catch {
    return { ok: false, reason: '無法解析的 MGRS（分帶或方格字母不合法）' }
  }
}

/**
 * 主入口：依序試 MGRS → 度分秒 → 十進位度。
 *
 * **順序是有意義的**：MGRS 最容易辨識（有字母格式），度分秒要有半球字母，
 * 最寬鬆的十進位度放最後——否則 `51QTG12345` 裡的數字會先被當成十進位度吃掉。
 */
export function parseCoordInput(raw: string): CoordParseResult {
  const text = raw.trim()
  if (!text) return { ok: false, reason: '請輸入座標' }
  for (const parse of [parseMgrs, parseDms, parseDecimal]) {
    const result = parse(text)
    if (result) return result
  }
  return {
    ok: false,
    reason: '認不得的格式。支援：十進位度（24.0972, 120.1705）、'
      + '度分秒（24°05\'50"N 120°10\'14"E）、MGRS（51QTG1234567890）',
  }
}

export const COORD_FORMAT_LABELS: Record<ParsedCoord['format'], string> = {
  DECIMAL: '十進位度',
  DMS: '度分秒',
  MGRS: 'MGRS',
}
