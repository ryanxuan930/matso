import ms from 'milsymbol'
import type { SymbolOptions } from 'milsymbol'
import type { SymbolOpts } from '~/composables/useUnits'

// milsymbol → ImageData 快取（MapLibre addImage 直接吃 ImageData）。key = iconKey（SIDC + 選項）。
const cache = new Map<string, ImageData>()

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
