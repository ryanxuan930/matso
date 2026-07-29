<script setup lang="ts">
/**
 * 戰況事件小工具內容——最近的裁決事件 feed（新到舊）。
 *
 * 事件文字由 `useCopFeed` 格式化；它需要單位清單把 UUID 換成番號，故以 `units` 收下
 * （與指令小工具共用同一份解析）。
 */
import { computed } from 'vue'
import { useCopFeed } from '~/composables/useCopFeed'
import type { UnitView } from '~/composables/useOrders'

const props = defineProps<{
  /** WS 連線狀態（live / resyncing / closed…）。 */
  status: string
  events: { payload?: Record<string, unknown> }[]
  units: UnitView[]
}>()

const { formatEvent } = useCopFeed(() => props.units)
const rows = computed(() => props.events.map((e) => formatEvent(e.payload ?? {})))
</script>

<template>
<div class="wsec-hd">戰況事件 <span class="ws">· {{ status }}</span></div>
<ul class="events" data-testid="event-list">
  <li v-for="(text, i) in rows" :key="i" data-testid="event-row">
    {{ text }}
  </li>
  <li v-if="!rows.length" class="empty">（尚無事件）</li>
</ul>
</template>

<style scoped>
/* 段落小標（取代舊 sec-hd，浮動視窗內用） */
.wsec-hd {
  font-size: 0.78rem;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 0.4rem;
}
/* 原本與 .orders 共用一條規則；兩者拆到不同元件後各留一份（scoped CSS 不跨元件）。 */
.events {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.events li {
  padding: 0.25rem 0.5rem;
  border-left: 2px solid #f59e0b;
  background: #1c1917;
  font-size: 0.75rem;
}
.ws {
  color: #64748b;
  font-weight: normal;
}
.empty {
  color: #64748b;
  cursor: default !important;
}
</style>
