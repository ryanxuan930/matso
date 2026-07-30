import ms from 'milsymbol'
import type { SymbolOptions } from 'milsymbol'
import type { SymbolOpts } from '~/composables/useUnits'

// milsymbol → ImageData 快取（MapLibre addImage 直接吃 ImageData）。key = iconKey（SIDC + 選項）。
const cache = new Map<string, ImageData>()

/**
 * 每張符號的 **icon-offset 補償量**（key → [dx, dy]，單位 px）。
 *
 * ## 為什麼需要這個
 *
 * milsymbol 的**錨點不在圖片中心**：APP-6A 的語義是「符號框的中心才是真實位置」，
 * 而加了左右兩欄的文字修飾之後，圖片會往有文字的那一側長出去，錨點因此偏離圖片中心。
 * MapLibre 的 `icon-anchor` 預設是 `center`，於是**圖片中心被畫在真實座標上、
 * 符號本體卻偏開了**。
 *
 * 實測（milsymbol 3.0.4，size 24）：裸符號 dx=0；`OFFLINE +12t` **dx=+37.9**；
 * 6 字中文番號 dx=-16.1；10 字番號 dx=-48.0。也就是說**現況的離線虛影已經畫錯位置**
 * ——z12 下約 1.4 km——而高亮環/血條都畫在真點上，符號會滑出自己的環外。
 *
 * 補償量 = 圖片中心 − 錨點。units 層沒有設 `icon-size`（預設 1）、`addImage` 也沒給
 * pixelRatio（預設 1），所以這個值可以直接當 `icon-offset` 用。
 */
const anchorOffsets = new Map<string, [number, number]>()

/** 某張符號的 icon-offset 補償量；沒生成過就回 [0,0]（不補償勝過亂補償）。 */
export function symbolOffset(key: string): [number, number] {
  return anchorOffsets.get(key) ?? [0, 0]
}

const ICON_SIZE = 24

/**
 * 由 SIDC + 選項生成 MIL-STD-2525 符號的 ImageData（含烤入的文字：番號 / OFFLINE 經過時間）。
 * 快取避免重複生成（500 單位共用少數符號）。canvas 生成僅於 client。
 */
export function symbolImage(key: string, sidc: string, options: SymbolOpts): ImageData | null {
  const hit = cache.get(key)
  if (hit) return hit
  const sym = new ms.Symbol(sidc, { size: ICON_SIZE, ...(options as SymbolOptions) })
  const canvas = sym.asCanvas()
  const ctx = canvas.getContext('2d')
  if (!ctx || canvas.width === 0 || canvas.height === 0) return null
  const img = ctx.getImageData(0, 0, canvas.width, canvas.height)
  // 錨點補償（見 `anchorOffsets` 說明）。getAnchor() 給的是「真實位置」在圖片內的座標，
  // 我們要把圖片推到讓那一點落在地理座標上，所以補償量是「圖片中心 − 錨點」。
  const anchor = sym.getAnchor()
  anchorOffsets.set(key, [canvas.width / 2 - anchor.x, canvas.height / 2 - anchor.y])
  cache.set(key, img)
  return img
}

// 固定單位鎖頭徽章：離線以 canvas 繪製 ImageData（免 glyphs，air-gapped 仍可渲染）。
// 暗色外框 + 琥珀鎖身，疊在單位符號右上角。pixelRatio 2 → 顯示約 14 CSS px。
export const LOCK_BADGE_ID = 'unit-fixed-lock-badge'
export const LOCK_BADGE_PIXEL_RATIO = 2
let lockBadge: ImageData | null | undefined

export function lockBadgeImage(): ImageData | null {
  if (lockBadge !== undefined) return lockBadge
  if (typeof document === 'undefined') return null // SSR 保護（僅 client 生成）
  const s = 28
  const canvas = document.createElement('canvas')
  canvas.width = s
  canvas.height = s
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    lockBadge = null
    return null
  }
  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'
  // 鎖環（shackle）：先暗色粗描邊作外框，再琥珀色細描（半圓開口朝下）。
  const arc = (w: number, color: string) => {
    ctx.strokeStyle = color
    ctx.lineWidth = w
    ctx.beginPath()
    ctx.arc(14, 13, 5, Math.PI, 2 * Math.PI)
    ctx.stroke()
  }
  arc(7, '#0a1626')
  arc(3.2, '#fde68a')
  // 鎖身（body）：暗色外框 + 琥珀圓角矩形。
  const roundRect = (x: number, y: number, w: number, h: number, r: number) => {
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()
  }
  ctx.fillStyle = '#0a1626'
  roundRect(4.5, 11.5, 19, 15, 4)
  ctx.fill()
  ctx.fillStyle = '#fbbf24'
  roundRect(6, 13, 16, 12, 3)
  ctx.fill()
  // 鑰匙孔（keyhole）：暗色小圓 + 短豎。
  ctx.fillStyle = '#0a1626'
  ctx.beginPath()
  ctx.arc(14, 17.5, 2, 0, 2 * Math.PI)
  ctx.fill()
  ctx.fillRect(13.2, 17.5, 1.6, 4)
  lockBadge = ctx.getImageData(0, 0, s, s)
  return lockBadge
}

// ---- 血量條（#94）：圖標上方的血條 ----
//
// **為何用 canvas 圖而非 text-field**：MapLibre 的 symbol 文字需要 glyphs，而純離線模式
// （無 tileUrl）style 不含 glyphs → 文字層會整個不出來。同鎖頭徽章的紀律：canvas 生成
// ImageData，air-gapped 也一定畫得出來。
//
// 以 5% 為一桶（21 張圖）而非每個整數一張：肉眼分辨不出 1% 差異，卻能把 addImage 從 101
// 降到 21 次。桶號同時作為 icon-image 的鍵。
export const HP_BAR_PIXEL_RATIO = 2
const HP_BAR_W = 40
const HP_BAR_H = 8

/** 單一桶的血條 ImageData：暗底 + 依血量帶著色的填充（與 healthColor 同色帶）。 */
export function hpBarImage(bucket: number): ImageData | null {
  if (typeof document === 'undefined') return null // SSR 保護
  const canvas = document.createElement('canvas')
  canvas.width = HP_BAR_W
  canvas.height = HP_BAR_H
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  const r = 2
  const rounded = (x: number, y: number, w: number, h: number) => {
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()
  }
  ctx.fillStyle = 'rgba(10,22,38,0.85)' // 暗底：確保在亮色底圖（衛星）上仍讀得出來
  rounded(0, 0, HP_BAR_W, HP_BAR_H)
  ctx.fill()
  const inner = HP_BAR_W - 4
  const filled = Math.round((inner * bucket) / 100)
  if (filled > 0) {
    ctx.fillStyle = bucket < 34 ? '#ef4444' : bucket < 67 ? '#f59e0b' : '#22c55e'
    ctx.fillRect(2, 2, filled, HP_BAR_H - 4)
  }
  return ctx.getImageData(0, 0, HP_BAR_W, HP_BAR_H)
}

// SIDC → PNG data URL（供 <img> 內嵌預覽，如北約符號選單）。快取避免重複生成。
const urlCache = new Map<string, string>()
export function symbolDataUrl(sidc: string, size = 26): string {
  const key = `${sidc}@${size}`
  const hit = urlCache.get(key)
  if (hit) return hit
  try {
    const sym = new ms.Symbol(sidc, { size })
    const url = sym.asCanvas().toDataURL('image/png')
    urlCache.set(key, url)
    return url
  } catch {
    return ''
  }
}
