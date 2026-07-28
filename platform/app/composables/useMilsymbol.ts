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
