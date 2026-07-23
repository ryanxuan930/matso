<script setup lang="ts">
// 浮動 / 停靠工具視窗（#12）：可拖拉、縮放、關閉；拖到最左/右緣自動停靠成側欄（Photoshop 式）。
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
    docked?: boolean
  }>(),
  { z: 10, minW: 200, minH: 120, resizable: true, docked: false },
)
const emit = defineEmits<{
  'update:geom': [WidgetGeom]
  close: []
  focus: []
  grab: [WidgetGeom] // 拖曳開始：回報目前螢幕座標，供父層先轉為浮動
  drop: [WidgetGeom] // 拖曳結束：回報最終座標，供父層判斷靠左/靠右停靠
}>()

const rootEl = ref<HTMLElement | null>(null)
const TOP_GUARD = 52 // 不讓視窗蓋到頂端工具列
let dragging = false
let resizing = false
let sx = 0
let sy = 0
let start: WidgetGeom = { x: 0, y: 0, w: 0, h: 0 }

function coords(e: MouseEvent | TouchEvent): { x: number; y: number } {
  const t = 'touches' in e ? e.touches[0] : null
  return t
    ? { x: t.clientX, y: t.clientY }
    : { x: (e as MouseEvent).clientX, y: (e as MouseEvent).clientY }
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
  if ((e.target as HTMLElement).closest('.fw-close, .fw-actions')) return
  emit('focus')
  // 起手先以「目前螢幕座標」為基準（停靠中的視窗座標與 geom 不同步 → 需量測 DOM）。
  const rect = rootEl.value?.getBoundingClientRect()
  const base: WidgetGeom = rect
    ? { x: rect.left, y: rect.top, w: props.geom.w, h: rect.height }
    : { ...props.geom }
  emit('grab', base) // 父層先讓它浮動到此位置
  dragging = true
  const p = coords(e)
  sx = p.x
  sy = p.y
  start = base
  addMoveListeners()
}
function beginResize(e: MouseEvent | TouchEvent) {
  emit('focus')
  resizing = true
  const p = coords(e)
  sx = p.x
  sy = p.y
  const rect = rootEl.value?.getBoundingClientRect()
  start = { ...props.geom, h: rect ? rect.height : props.geom.h }
  e.stopPropagation()
  addMoveListeners()
}
function onMove(e: MouseEvent | TouchEvent) {
  if (!dragging && !resizing) return
  if ('touches' in e) e.preventDefault()
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
    const maxH = window.innerHeight - TOP_GUARD - 8
    emit('update:geom', {
      ...props.geom,
      w: props.docked ? props.geom.w : Math.min(Math.max(props.minW, start.w + dx), maxW),
      h: Math.min(Math.max(props.minH, start.h + dy), maxH),
    })
  }
}
function endDrag() {
  if (dragging) emit('drop', { ...props.geom })
  dragging = false
  resizing = false
  removeMoveListeners()
}
onBeforeUnmount(removeMoveListeners)
</script>

<template>
  <div
    ref="rootEl"
    class="fw"
    :class="{ 'fw-docked': docked }"
    :style="
      docked
        ? { height: geom.h + 'px', zIndex: z }
        : { left: geom.x + 'px', top: geom.y + 'px', width: geom.w + 'px', height: geom.h + 'px', zIndex: z }
    "
    @mousedown="emit('focus')"
  >
    <div class="fw-hd" @mousedown="beginHeader" @touchstart="beginHeader">
      <span class="fw-grip"><i class="pi pi-bars" /></span>
      <span class="fw-title">{{ title }}</span>
      <span class="fw-actions"><slot name="actions" /></span>
      <button class="fw-close" title="關閉" @click="emit('close')"><i class="pi pi-times" /></button>
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
/* 停靠模式：由側欄容器決定寬度與排列，視窗本身走一般流。 */
.fw.fw-docked {
  position: relative;
  width: 100%;
  flex: 0 0 auto;
  border-radius: 0.35rem;
  box-shadow: none;
}
.fw-hd {
  display: flex;
  align-items: center;
  gap: 0.35rem;
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
.fw-grip {
  color: #475569;
  font-size: 0.7rem;
  letter-spacing: -1px;
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
