/**
 * 單位資訊卡的定位與拖曳（#Fix C / #42）。
 *
 * 兩種定位模式：**錨定**（MapCanvas 投影出選取單位的螢幕座標 → 卡片浮在圖標旁，並夾在
 * 視窗內）與**手動**（使用者拖過一次就脫離錨定，固定於拖到的螢幕座標）。
 * 換單位/取消選取會回到錨定——否則新單位的卡片會出現在上一個單位被拖去的地方。
 */
import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'

interface Point {
  x: number
  y: number
}

export function useUnitCardDrag(selectedId: Ref<string | null>) {
  const unitCardPos = ref<Point | null>(null) // 圖標錨點（MapCanvas 投影）
  const unitCardDrag = ref<Point | null>(null) // 使用者拖曳後的固定位置

  function onSelectScreenPos(p: Point | null) {
    unitCardPos.value = p
  }
  watch(selectedId, () => {
    unitCardDrag.value = null
  })

  // 卡片實際定位：拖曳過 → 手動座標；否則圖標右上方偏移，並夾在視窗內（卡片約 304×320）。
  const unitCardStyle = computed(() => {
    if (unitCardDrag.value) {
      return { left: `${unitCardDrag.value.x}px`, top: `${unitCardDrag.value.y}px` }
    }
    const p = unitCardPos.value
    if (!p) return { display: 'none' }
    const CW = 304 // ≈ 19rem
    const CH = 320
    const vw = import.meta.client ? window.innerWidth : 1280
    const vh = import.meta.client ? window.innerHeight : 800
    let left = p.x + 18 // 圖標右側
    let top = p.y - 10
    if (left + CW > vw - 8) left = p.x - CW - 18 // 右側放不下 → 移到圖標左側
    if (left < 8) left = 8
    top = Math.min(Math.max(56, top), vh - CH - 8)
    return { left: `${left}px`, top: `${top}px` }
  })

  // 以標頭為把手，滑鼠/觸控皆可，夾在視窗內。
  let cardDragging = false
  let cardSX = 0
  let cardSY = 0
  let cardStartX = 0
  let cardStartY = 0

  function cardPoint(e: MouseEvent | TouchEvent): Point {
    const t = 'touches' in e ? e.touches[0] : null
    return t
      ? { x: t.clientX, y: t.clientY }
      : { x: (e as MouseEvent).clientX, y: (e as MouseEvent).clientY }
  }
  function beginCardDrag(e: MouseEvent | TouchEvent) {
    if ((e.target as HTMLElement).closest('.card-close')) return
    const rect = (e.currentTarget as HTMLElement).closest('.unit-card')?.getBoundingClientRect()
    cardStartX = rect ? rect.left : 0
    cardStartY = rect ? rect.top : 0
    const p = cardPoint(e)
    cardSX = p.x
    cardSY = p.y
    unitCardDrag.value = { x: cardStartX, y: cardStartY }
    cardDragging = true
    window.addEventListener('mousemove', onCardDrag)
    window.addEventListener('mouseup', endCardDrag)
    window.addEventListener('touchmove', onCardDrag, { passive: false })
    window.addEventListener('touchend', endCardDrag)
  }
  function onCardDrag(e: MouseEvent | TouchEvent) {
    if (!cardDragging) return
    if ('touches' in e) e.preventDefault()
    const p = cardPoint(e)
    const maxX = window.innerWidth - 80
    const maxY = window.innerHeight - 40
    unitCardDrag.value = {
      x: Math.min(Math.max(0, cardStartX + p.x - cardSX), maxX),
      y: Math.min(Math.max(52, cardStartY + p.y - cardSY), maxY),
    }
  }
  function endCardDrag() {
    cardDragging = false
    window.removeEventListener('mousemove', onCardDrag)
    window.removeEventListener('mouseup', endCardDrag)
    window.removeEventListener('touchmove', onCardDrag)
    window.removeEventListener('touchend', endCardDrag)
  }
  onBeforeUnmount(endCardDrag)

  return { unitCardPos, unitCardDrag, unitCardStyle, onSelectScreenPos, beginCardDrag }
}
