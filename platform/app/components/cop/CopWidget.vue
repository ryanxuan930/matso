<script setup lang="ts">
/**
 * 小工具外殼（#12）——把六個小工具共用的 `Teleport`（停靠側欄）＋ `FloatingWidget`
 * （拖拉/縮放/關閉）樣板收成一處。原本這 18 行在 cop.vue 重複六次，差別只有 id 與內容。
 *
 * `ui` 收的是 `useCopWidgets()` 整包（父層須傳 `reactive(...)`，樣板不會 unwrap 巢狀 ref），
 * 停靠與幾何的狀態機都在那；本元件不自己算，只負責把事件轉給它。
 */
import { computed } from 'vue'
import { WIDGET_DEFS } from '~/composables/useCopWidgets'
import type { UnwrapNestedRefs } from 'vue'
import type { WidgetId, useCopWidgets } from '~/composables/useCopWidgets'

const props = defineProps<{
  ui: UnwrapNestedRefs<ReturnType<typeof useCopWidgets>>
  id: WidgetId
  /** 是否顯示——多半就是 `widgets[id].open`，但 mapedit 另有繪圖權限條件。 */
  open: boolean
}>()

const stat = computed(() => props.ui.widgets[props.id])
/** 標題取自 WIDGET_DEFS，免得工具選單與視窗標題各寫一份而漂移。 */
const title = computed(() => WIDGET_DEFS.find((d) => d.id === props.id)?.title ?? props.id)
</script>

<template>
<!-- eslint-disable vue/no-mutating-props -- ui 是 useCopWidgets() 整包狀態機（父層 reactive），
     關閉即改它持有的 open，與 setWidgetGeom 等 action 同源，不是另存一份區域狀態。 -->
<Teleport
  :to="stat.dock === 'right' ? '#dock-right-col' : '#dock-left-col'"
  :disabled="stat.dock === 'float'"
>
  <FloatingWidget
    v-if="open"
    :title="title"
    :geom="stat"
    :z="stat.z"
    :docked="stat.dock !== 'float'"
    @update:geom="(g) => ui.setWidgetGeom(id, g)"
    @grab="(g) => ui.onWidgetGrab(id, g)"
    @drop="(g) => ui.onWidgetDrop(id, g)"
    @close="stat.open = false"
    @focus="ui.focusWidget(id)"
  >
    <slot />
  </FloatingWidget>
</Teleport>
</template>
