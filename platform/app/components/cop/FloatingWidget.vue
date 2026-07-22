<script setup lang="ts">
// 浮動工具視窗（#12）：可拖拉（標題列）、可縮放（右下角）、可關閉。幾何由父層持有並持久化。
export interface WidgetGeom {
  x: number
  y: number
  w: number
  h: number
}

const props = withDefaults(
  defineProps<{
    title: string
    geom: WidgetGeom
    z?: number
    minW?: number
    minH?: number
    resizable?: boolean
  }>(),
  { z: 10, minW: 200, minH: 120, resizable: true },
)
const emit = defineEmits<{
  'update:geom': [WidgetGeom]
  close: []
  focus: []
}>()

const TOP_GUARD = 52 // 不讓視窗蓋到頂端工具列
let dragging = false
let resizing = false
let sx = 0
let sy = 0
let start: WidgetGeom = { x: 0, y: 0, w: 0, h: 0 }

// 滑鼠 + 觸控座標統一取用（桌機滑鼠為主，兼顧觸控/平板）。
function coords(e: MouseEvent | TouchEvent): { x: number; y: number } {
  const t = 'touches' in e ? e.touches[0] : null
  return t ? { x: t.clientX, y: t.clientY } : { x: (e as MouseEvent).clientX, y: (e as MouseEvent).clientY }
}
function addMoveListeners() {
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', endDrag)
  window.addEventListener('touchmove', onMove, { passive: false })
  window.addEventListener('touchend', endDrag)
}
function removeMoveListeners() {
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', endDrag)
  window.removeEventListener('touchmove', onMove)
  window.removeEventListener('touchend', endDrag)
}

function beginHeader(e: MouseEvent | TouchEvent) {
  // 標題列上的按鈕（關閉/actions）不觸發拖拉
  if ((e.target as HTMLElement).closest('.fw-close, .fw-actions')) return
  emit('focus')
  dragging = true
  const p = coords(e)
  sx = p.x
  sy = p.y
  start = { ...props.geom }
  addMoveListeners()
}
function beginResize(e: MouseEvent | TouchEvent) {
  emit('focus')
  resizing = true
  const p = coords(e)
  sx = p.x
  sy = p.y
  start = { ...props.geom }
  e.stopPropagation()
  addMoveListeners()
}
function onMove(e: MouseEvent | TouchEvent) {
  if (!dragging && !resizing) return
  if ('touches' in e) e.preventDefault() // 觸控拖拉時不捲動頁面
  const p = coords(e)
  const dx = p.x - sx
  const dy = p.y - sy
  if (dragging) {
    const maxX = window.innerWidth - 80
    const maxY = window.innerHeight - 40
    emit('update:geom', {
      ...props.geom,
      x: Math.min(Math.max(0, start.x + dx), maxX),
      y: Math.min(Math.max(TOP_GUARD, start.y + dy), maxY),
    })
  } else if (resizing) {
    const maxW = window.innerWidth - props.geom.x - 8
    const maxH = window.innerHeight - props.geom.y - 8
    emit('update:geom', {
      ...props.geom,
      w: Math.min(Math.max(props.minW, start.w + dx), maxW),
      h: Math.min(Math.max(props.minH, start.h + dy), maxH),
    })
  }
}
function endDrag() {
  dragging = false
  resizing = false
  removeMoveListeners()
}
onBeforeUnmount(removeMoveListeners) // 拖拉中被關閉也要清掉全域監聽
</script>

<template>
  <div
    class="fw"
    :style="{ left: geom.x + 'px', top: geom.y + 'px', width: geom.w + 'px', height: geom.h + 'px', zIndex: z }"
    @mousedown="emit('focus')"
  >
    <div class="fw-hd" @mousedown="beginHeader" @touchstart="beginHeader">
      <span class="fw-title">{{ title }}</span>
      <span class="fw-actions"><slot name="actions" /></span>
      <button class="fw-close" title="關閉" @click="emit('close')">✕</button>
    </div>
    <div class="fw-body"><slot /></div>
    <div
      v-if="resizable"
      class="fw-resize"
      title="拖拉調整大小"
      @mousedown="beginResize"
      @touchstart="beginResize"
    />
  </div>
</template>

<style scoped>
.fw {
  position: absolute;
  display: flex;
  flex-direction: column;
  background: rgba(9, 14, 24, 0.94);
  border: 1px solid #24344a;
  border-radius: 0.5rem;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5);
  overflow: hidden;
  min-width: 0;
  backdrop-filter: blur(2px);
}
.fw-hd {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.5rem;
  background: #14243a;
  border-bottom: 1px solid #24344a;
  cursor: grab;
  user-select: none;
  touch-action: none;
}
.fw-hd:active {
  cursor: grabbing;
}
.fw-title {
  font-size: 0.78rem;
  font-weight: 600;
  color: #cbd5e1;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fw-actions {
  display: flex;
  align-items: center;
  gap: 0.25rem;
}
.fw-close {
  border: 0;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  font-size: 0.8rem;
  line-height: 1;
  padding: 0.1rem 0.25rem;
  border-radius: 0.2rem;
}
.fw-close:hover {
  background: #7f1d1d;
  color: #fff;
}
.fw-body {
  flex: 1;
  overflow: auto;
  padding: 0.5rem;
  min-height: 0;
}
.fw-resize {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  touch-action: none;
  background: linear-gradient(135deg, transparent 50%, #3b5573 50%, #3b5573 60%, transparent 60%, transparent 72%, #3b5573 72%, #3b5573 82%, transparent 82%);
}
</style>
