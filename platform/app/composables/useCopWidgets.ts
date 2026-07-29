/**
 * COP 浮動工具視窗（#12）——六個小工具皆可拖拉/縮放/關閉，並以工具選單勾選開關。
 *
 * 取代舊的左右固定面板/換邊。幾何與開關由 `useCopPrefs` 一起持久化（同一把 localStorage
 * 鑰匙），故本模組**只管狀態機、不管存檔**——把 widgets 交出去由偏好層存。
 */
import { computed, ref, type Ref } from 'vue'

export type WidgetId =
  | 'layers'
  | 'units'
  | 'events'
  | 'orders'
  | 'mapedit'
  | 'coords'
  | 'c2'
  | 'fireplan'
export type DockSide = 'left' | 'right' | 'float'

export interface WStat {
  open: boolean
  dock: DockSide
  x: number
  y: number
  w: number
  h: number
  z: number
}

export interface WidgetGeom {
  x: number
  y: number
  w: number
  h: number
}

export const WIDGET_DEFS: { id: WidgetId; title: string; label: string }[] = [
  { id: 'layers', title: '圖層 / 底圖', label: '圖層' },
  { id: 'units', title: '單位 / 下令', label: '單位' },
  { id: 'events', title: '戰況事件', label: '戰況事件' },
  { id: 'orders', title: '指令', label: '指令' },
  { id: 'mapedit', title: '地圖編輯', label: '地圖編輯' },
  { id: 'coords', title: '座標查詢', label: '座標' },
  { id: 'c2', title: '信文 / 申請', label: '信文' },
  { id: 'fireplan', title: '火力計畫', label: '火力計畫' },
]

const DOCK_EDGE = 72 // 拖到最左/右 DOCK_EDGE px 內即停靠成側欄
export const DOCK_W = 320 // 停靠側欄寬（含邊距）——供地圖控制項讓位

export function defaultWidgets(): Record<WidgetId, WStat> {
  const vw = import.meta.client ? window.innerWidth : 1280
  const rx = Math.max(324, vw - 320)
  return {
    layers: { open: true, dock: 'left', x: 12, y: 60, w: 296, h: 470, z: 11 },
    units: { open: true, dock: 'right', x: rx, y: 60, w: 300, h: 300, z: 12 },
    events: { open: true, dock: 'right', x: rx, y: 372, w: 300, h: 148, z: 13 },
    orders: { open: true, dock: 'right', x: rx, y: 532, w: 300, h: 180, z: 14 },
    mapedit: { open: false, dock: 'float', x: 12, y: 60, w: 326, h: 540, z: 15 },
    coords: { open: false, dock: 'float', x: 12, y: 540, w: 260, h: 170, z: 16 },
    c2: { open: false, dock: 'right', x: rx, y: 60, w: 320, h: 380, z: 17 },
    fireplan: { open: false, dock: 'float', x: 340, y: 60, w: 340, h: 420, z: 18 },
  }
}

export function useCopWidgets() {
  const widgets = ref<Record<WidgetId, WStat>>(defaultWidgets())
  const widgetZTop = ref(20)
  const widgetMenuOpen = ref(false)

  const hasLeftDock = computed(() =>
    WIDGET_DEFS.some((d) => widgets.value[d.id].open && widgets.value[d.id].dock === 'left'),
  )
  const hasRightDock = computed(() =>
    WIDGET_DEFS.some((d) => widgets.value[d.id].open && widgets.value[d.id].dock === 'right'),
  )

  function focusWidget(id: WidgetId) {
    widgetZTop.value += 1
    widgets.value[id].z = widgetZTop.value
  }
  function toggleWidget(id: WidgetId) {
    const w = widgets.value[id]
    w.open = !w.open
    if (w.open) focusWidget(id)
  }
  function setWidgetGeom(id: WidgetId, g: WidgetGeom) {
    Object.assign(widgets.value[id], g)
  }
  // 拖曳起手：先脫離停靠變浮動，落在目前螢幕位置跟著游標走。
  function onWidgetGrab(id: WidgetId, g: WidgetGeom) {
    const w = widgets.value[id]
    w.dock = 'float'
    w.x = g.x
    w.y = g.y
    w.h = g.h
    focusWidget(id)
  }
  // 拖曳落下：靠最左/右緣 → 停靠成側欄；否則維持浮動於落點。
  function onWidgetDrop(id: WidgetId, g: WidgetGeom) {
    const w = widgets.value[id]
    const vw = window.innerWidth
    if (g.x <= DOCK_EDGE) w.dock = 'left'
    else if (g.x + g.w >= vw - DOCK_EDGE) w.dock = 'right'
    else {
      w.dock = 'float'
      w.x = g.x
      w.y = g.y
    }
  }

  /** 某個小工具的開關雙向代理（`coordQuery` / `mapEditorOpen` 這類別名用）。 */
  function openFlag(id: WidgetId): Ref<boolean> {
    return computed({
      get: () => widgets.value[id].open,
      set: (v: boolean) => {
        widgets.value[id].open = v
      },
    })
  }

  return {
    widgets,
    widgetZTop,
    widgetMenuOpen,
    hasLeftDock,
    hasRightDock,
    focusWidget,
    toggleWidget,
    setWidgetGeom,
    onWidgetGrab,
    onWidgetDrop,
    openFlag,
  }
}
