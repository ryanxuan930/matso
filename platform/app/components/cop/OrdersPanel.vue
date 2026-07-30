<script setup lang="ts">
/**
 * 指令小工具內容（#27）——下令對象 + 任務階段 + 時間 + 狀態，並可取消/停止未完成的令。
 *
 * 排序與層級在這裡做：母令以下令 tick（≈真實時間）新到舊，剛下的令排最上；
 * 任務令分解出的子令收在母令底下（見 `orderRows`）。
 */
import { computed } from 'vue'
import { missionPhaseLabel, orderStatusLabel, orderTypeLabel } from '~/composables/useOrders'
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

/**
 * 指令列表：母令新到舊，任務子令收在母令底下（WP-A2）。
 *
 * 原本是一個平面清單，任務令分解出來的 MOVE/ENGAGE 子令與人親手下的令混在一起，
 * 看起來就像「這支部隊收到一堆沒人下過的命令」——參謀分不出哪些是自己下的、
 * 哪些是任務展開的，也就無從判斷該取消哪一道。
 *
 * 母令不在本次清單內的孤兒子令（母令已被清掉/分頁沒撈到）仍當作頂層顯示，
 * 但保留「任務子令」標記——寧可位置不對，也不要整筆消失。
 */
const orderRows = computed(() => {
  const ids = new Set(props.orders.map((o) => o.id))
  const children = new Map<string, OrderResponse[]>()
  const roots: OrderResponse[] = []
  for (const o of props.orders) {
    const parent = o.parent_order_id
    if (parent && ids.has(parent)) children.set(parent, [...(children.get(parent) ?? []), o])
    else roots.push(o)
  }
  roots.sort((a, b) => (b.issued_at_tick ?? 0) - (a.issued_at_tick ?? 0))
  const rows: { order: OrderResponse; sub: boolean }[] = []
  for (const root of roots) {
    rows.push({ order: root, sub: false })
    // 子令是任務的**執行序**，由前往後讀才對（與母令列的新到舊刻意相反）。
    const subs = [...(children.get(root.id) ?? [])].sort(
      (a, b) => (a.issued_at_tick ?? 0) - (b.issued_at_tick ?? 0),
    )
    for (const s of subs) rows.push({ order: s, sub: true })
  }
  // 孤兒子令（母令不在本次清單內）仍會落在 roots，故仍保留「任務子令」標記。
  return rows.map((r) => ({ ...r, sub: r.sub || !!r.order.parent_order_id }))
})

/**
 * 任務階段（PLANNED/MOVING/ENGAGING/…）。
 *
 * ⚠ 契約（`OrderResponse.mission_phase`）宣告了這個欄位，但**後端 `_to_response`
 * 目前不填**（`core/app/orders/schemas.py` 的 `OrderResponse` 根本沒有這個欄位，
 * 階段值只存在令載荷的 `_mission_state.phase` 裡）。所以在後端補上之前，
 * 這裡恆為空、什麼都不顯示。前端這一半先接好，後端一填就會自己亮起來。
 */
function phaseLabel(o: OrderResponse): string {
  return o.order_type === 'MISSION' ? missionPhaseLabel(o.mission_phase) : ''
}
</script>

<template>
<div class="wsec-hd">指令（{{ orders.length }}）</div>
<ul class="orders" data-testid="order-list">
  <li v-for="{ order: o, sub } in orderRows" :key="o.id" :class="{ 'ord-sub': sub }" data-testid="order-row">
    <div class="ord-main">
      <span v-if="sub" class="ord-subtag" data-testid="order-sub-tag">任務子令</span>
      <span class="ord-unit">{{ unitName(o.unit_id) || '單位' }}</span>
      <span class="ord-type">{{ orderTypeLabel(o.order_type) }}</span>
      <span v-if="orderTargetLabel(o)" class="ord-tgt">{{ orderTargetLabel(o) }}</span>
      <span v-if="phaseLabel(o)" class="ord-phase" data-testid="order-phase" title="任務階段">
        {{ phaseLabel(o) }}
      </span>
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
/* 任務子令：縮排 + 左緣導引線，一眼看出「這是那道任務展開出來的」而不是有人另外下的令。
   選擇器要帶 `.orders`：上面的 `.orders li` 已經宣告了 border，特異度低的規則會被它蓋掉。 */
.orders li.ord-sub {
  margin-left: 0.9rem;
  border-left: 2px solid #3b82f6;
}
.ord-subtag {
  padding: 0 0.25rem;
  border-radius: 0.2rem;
  background: #1e3a5f;
  color: #93c5fd;
  font-size: 0.66rem;
}
/* 任務階段（機動中/接戰中/鞏固中…）：狀態只會顯示「執行中」，階段才看得出跑到哪。 */
.ord-phase {
  padding: 0 0.3rem;
  border-radius: 0.2rem;
  background: #422006;
  color: #fcd34d;
  font-size: 0.68rem;
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
