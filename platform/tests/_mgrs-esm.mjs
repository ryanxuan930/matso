/**
 * `mgrs` 的 ESM 轉接層——**只給 node:test 用**，正式路徑不經過這裡。
 *
 * 為什麼需要它：`mgrs` 是 CommonJS 套件。Vite/Nuxt 會做 interop，所以 app 裡
 * `import { toPoint } from 'mgrs'` 是好的（`useCoordGrid.ts` 一直這樣用）；
 * 但在原生 Node ESM 底下具名匯入會直接 SyntaxError。
 *
 * 這裡刻意**載入真的 mgrs**（不是測試替身）——座標換算是這條功能的核心，
 * 用假的來測等於什麼都沒測。
 */
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const mgrs = require('mgrs')

export const toPoint = mgrs.toPoint
export const forward = mgrs.forward
export const inverse = mgrs.inverse
export default mgrs
