<script setup lang="ts">
/**
 * 指令小工具內容（#27）——下令對象 + 時間 + 狀態，並可取消/停止未完成的令。
 *
 * 排序在這裡做：以下令 tick（≈真實時間）新到舊，剛下的令排最上。
 */
import { computed } from 'vue'
import { orderStatusLabel, orderTypeLabel } from '~/composables/useOrders'
import { useCopFeed } from '~/composables/useCopFeed'
import type { OrderResponse, UnitView } from '~/composables/useOrders'

const props = defineProps<{
  orders: OrderResponse[]
  units: UnitView[]
  /** 首次載入尚未完成——顯示載入中而不是空狀態（空狀態要留給「真的沒有」）。 */
  loading?: boolean
}>()

defineEmits<{ (e: 'cancel', orderId: string): void }>()

const { unitName, orderTargetLabel } = useCopFeed(() => props.units)
// 指令列表：以下令 tick（≈真實時間）新到舊排序，剛下的令排最上（穩定排序，同 tick 保原序）。
const sortedOrders = computed(() =>
  [...props.orders].sort((a, b) => (b.issued_at_tick ?? 0) - (a.issued_at_tick ?? 0)),
)
</script>

<template>
<div class="wsec-hd">指令（{{ orders.length }}）</div>
<ul class="orders" data-testid="order-list">
  <li v-for="o in sortedOrders" :key="o.id" data-testid="order-row">
    <div class="ord-main">
      <span class="ord-unit">{{ unitName(o.unit_id) || '單位' }}</span>
      <span class="ord-type">{{ orderTypeLabel(o.order_type) }}</span>
      <span v-if="orderTargetLabel(o)" class="ord-tgt">{{ orderTargetLabel(o) }}</span>
    </div>
    <div class="ord-meta">
      <span class="ord-time" title="下令 sim tick">T{{ o.issued_at_tick
        }}<span v-if="o.resolved_at_tick != null"> → T{{ o.resolved_at_tick }}</span></span>
      <span class="ord-status" :class="`st-${o.status}`">{{ orderStatusLabel(o.status) }}</span>
      <button
        v-if="o.status === 'VALIDATED' || o.status === 'PENDING' || o.status === 'EXECUTING'"
        data-testid="cancel-order"
        :title="o.status === 'EXECUTING' ? '停止移動並就地凍結（不彈回原位）' : '取消未執行指令'"
        @click="$emit('cancel', o.id)"
      >
        {{ o.status === 'EXECUTING' ? '停止' : '取消' }}
      </button>
    </div>
  </li>
  <li v-if="loading"><PanelLoading /></li>
  <li v-else-if="!orders.length" class="empty">（無指令）</li>
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
/* 原本與 .events 共用一條規則；兩者拆到不同元件後各留一份（scoped CSS 不跨元件）。 */
.orders {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.orders li {
  padding: 0.375rem 0.5rem;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
  cursor: pointer;
}
.empty {
  color: #64748b;
  cursor: default !important;
}
/* #27 指令列：對象 + 時間 + 狀態。 */
.orders li {
  cursor: default;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.ord-main {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.ord-unit {
  font-weight: 600;
  color: #e2e8f0;
}
.ord-type {
  color: #93c5fd;
  font-size: 0.72rem;
}
.ord-tgt {
  color: #fca5a5;
  font-size: 0.72rem;
}
.ord-meta {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.7rem;
  color: #94a3b8;
}
.ord-time {
  font-variant-numeric: tabular-nums;
}
.ord-status {
  padding: 0 0.3rem;
  border-radius: 0.2rem;
  background: #1e293b;
}
.ord-status.st-COMPLETED {
  color: #86efac;
}
.ord-status.st-REJECTED,
.ord-status.st-CANCELLED {
  color: #fca5a5;
}
.ord-status.st-EXECUTING {
  color: #fcd34d;
}
.ord-meta button {
  margin-left: auto;
  padding: 0.1rem 0.4rem;
  font-size: 0.68rem;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  background: transparent;
  color: #cbd5e1;
  cursor: pointer;
}
.orders button {
  margin-left: 0.5rem;
  padding: 0.0625rem 0.375rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #f87171;
  cursor: pointer;
}
</style>
