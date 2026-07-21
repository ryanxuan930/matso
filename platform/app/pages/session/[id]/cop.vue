<script setup lang="ts">
import type { Contact, OwnUnit } from '~/composables/useUnits'
import type { UnitView, OrderResponse } from '~/composables/useOrders'
import type { ApiError } from '~/composables/useApi'
import { cancelOrder, fetchOrders, fetchUnits, submitOrder } from '~/composables/useOrders'

// COP（SPEC §13.1/§13.4）：地圖基座（O4.2）+ 單位/fog of war（O4.4）+ 下令 UX（O4.5）。
const route = useRoute()
const sessionId = computed(() => String(route.params.id))

const hex = ref(false)
const hillshade = ref(false)
const currentTick = ref(100)

const TYPES = ['INFANTRY', 'ARMOR', 'ARTILLERY', 'RECON', 'HQ']

// ?units=N 合成單位（FPS/demo，O4.4）
const syntheticUnits = computed<OwnUnit[]>(() => {
  const n = Math.min(Math.max(Number(route.query.units) || 0, 0), 2000)
  const cols = Math.ceil(Math.sqrt(n)) || 1
  const out: OwnUnit[] = []
  for (let i = 0; i < n; i++) {
    out.push({
      id: `syn-${i}`,
      faction: 'BLUE',
      lng: 120.0 + ((i % cols) / cols) * 2.0,
      lat: 22.8 + (Math.floor(i / cols) / cols) * 1.8,
      unitType: TYPES[i % TYPES.length],
      comms: i % 17 === 0 ? 'OFFLINE' : 'ONLINE',
      lastReportedTick: 100 - (i % 40),
    })
  }
  return out
})

// 真單位（GET /units；下令對象）
const realUnits = ref<UnitView[]>([])
const orders = ref<OrderResponse[]>([])
const selectedId = ref<string | null>(null)
const orderType = ref<'MOVE' | 'ENGAGE'>('MOVE')
const destH3 = ref<string | null>(null)
const targeting = ref(false)
const targetUnitId = ref<string | null>(null)
const precheck = ref<OrderResponse['precheck'] | null>(null)
const message = ref('')

const realAsOwn = computed<OwnUnit[]>(() =>
  realUnits.value.map((u) => ({
    id: u.id,
    faction: (u.faction as OwnUnit['faction']) ?? 'BLUE',
    lng: u.lng ?? 121,
    lat: u.lat ?? 23.7,
    comms: (u.comms as OwnUnit['comms']) ?? 'ONLINE',
    lastReportedTick: 100,
  })),
)
// 固定示範一個 OFFLINE 虛影（fog of war demo，O4.4）
const GHOST: OwnUnit = {
  id: 'demo-ghost',
  faction: 'BLUE',
  lng: 121.2,
  lat: 24.2,
  unitType: 'HQ',
  comms: 'OFFLINE',
  lastReportedTick: 60,
}
const ownUnits = computed<OwnUnit[]>(() => [GHOST, ...syntheticUnits.value, ...realAsOwn.value])
const contacts = computed<Contact[]>(() => [
  { contactId: 'c-det', fidelity: 'DETECTED', lng: 121.4, lat: 23.5, errorRadiusM: 2000, lastSeenTick: 40 },
  { contactId: 'c-cls', fidelity: 'CLASSIFIED', lng: 121.5, lat: 23.6, errorRadiusM: 800, unitType: 'ARMOR', lastSeenTick: 80 },
  { contactId: 'c-id', fidelity: 'IDENTIFIED', lng: 121.6, lat: 23.7, errorRadiusM: 200, unitType: 'ARTILLERY', designation: '3-BN', lastSeenTick: 98 },
])

async function refresh() {
  realUnits.value = await fetchUnits(sessionId.value).catch(() => [])
  orders.value = await fetchOrders(sessionId.value).catch(() => [])
}

function selectUnit(id: string) {
  selectedId.value = id
  precheck.value = null
  message.value = ''
  destH3.value = null
  targetUnitId.value = null
}

function onMapClick(e: { h3: string }) {
  if (targeting.value) {
    destH3.value = e.h3
    targeting.value = false
  }
}

async function submit() {
  if (!selectedId.value) return
  message.value = ''
  precheck.value = null
  const payload =
    orderType.value === 'MOVE'
      ? { to_h3: destH3.value, mobility_profile: 'FOOT' }
      : { target_unit_id: targetUnitId.value }
  try {
    const resp = await submitOrder(sessionId.value, {
      unit_id: selectedId.value,
      order_type: orderType.value,
      payload,
    })
    precheck.value = resp.precheck ?? null
    message.value = `已下令（${resp.status}）`
    await refresh()
  } catch (e) {
    const err = e as ApiError & { message?: string }
    const pc = (err as unknown as { details?: { precheck?: OrderResponse['precheck'] } }).details
    precheck.value = pc?.precheck ?? null
    message.value = `不可行：${err.code ?? ''}`
  }
}

async function cancel(id: string) {
  await cancelOrder(sessionId.value, id).catch(() => undefined)
  await refresh()
}

// WS stream（O4.3/O4.6）：連 session，顯示收到的裁決事件
const stream = useSessionStreamStore()
const streamEvents = computed(() =>
  stream.events.filter((e) => e.type === 'EVENT').slice(-20).reverse(),
)

async function back() {
  stream.disconnect()
  await navigateTo('/lobby')
}

onMounted(() => {
  refresh()
  stream.connect(sessionId.value)
})
onBeforeUnmount(() => stream.disconnect())
</script>

<template>
  <div class="cop">
    <header class="cop-bar">
      <button data-testid="back-lobby" @click="back">← 大廳</button>
      <span class="sid" data-testid="cop-session">Session {{ sessionId }}</span>
      <span class="count" data-testid="unit-count">單位 {{ ownUnits.length }}</span>
    </header>
    <div class="body">
      <aside class="panel">
        <h3>單位</h3>
        <ul class="units" data-testid="unit-list">
          <li
            v-for="u in realUnits"
            :key="u.id"
            :class="{ sel: u.id === selectedId }"
            data-testid="unit-item"
            @click="selectUnit(u.id)"
          >
            {{ u.designation }} · {{ u.faction }} · {{ Math.round(u.health) }}%
          </li>
          <li v-if="!realUnits.length" class="empty">（此 session 無可下令單位）</li>
        </ul>

        <div v-if="selectedId" class="order" data-testid="order-panel">
          <h3>下令</h3>
          <select v-model="orderType" data-testid="order-type">
            <option value="MOVE">移動 MOVE</option>
            <option value="ENGAGE">攻擊 ENGAGE</option>
          </select>
          <template v-if="orderType === 'MOVE'">
            <button data-testid="pick-dest" :class="{ armed: targeting }" @click="targeting = true">
              {{ targeting ? '點地圖設目標…' : '設定目標點' }}
            </button>
            <div class="dest" data-testid="dest-h3">{{ destH3 || '未設目標' }}</div>
          </template>
          <template v-else>
            <select v-model="targetUnitId" data-testid="engage-target">
              <option :value="null">選目標</option>
              <option v-for="u in realUnits" :key="u.id" :value="u.id">{{ u.designation }}</option>
            </select>
          </template>
          <button
            data-testid="submit-order"
            :disabled="orderType === 'MOVE' ? !destH3 : !targetUnitId"
            @click="submit"
          >
            送出
          </button>
          <p v-if="message" data-testid="order-message">{{ message }}</p>
          <div v-if="precheck" class="precheck" data-testid="precheck">
            <div :class="precheck.feasible ? 'ok' : 'bad'">
              預檢：{{ precheck.feasible ? '可行' : '不可行' }}
            </div>
            <ul>
              <li v-for="(c, i) in precheck.checks" :key="i">
                {{ c.passed ? '✓' : '✗' }} {{ c.name }} <span v-if="c.detail">— {{ c.detail }}</span>
              </li>
            </ul>
          </div>
        </div>

        <h3>戰況事件 <span class="ws" :data-testid="'ws-status'">· {{ stream.status }}</span></h3>
        <ul class="events" data-testid="event-list">
          <li v-for="(e, i) in streamEvents" :key="i" data-testid="event-row">
            {{ (e.payload as Record<string, unknown>)?.event_type }}
            <span v-if="(e.payload as Record<string, unknown>)?.order_type">
              · {{ (e.payload as Record<string, unknown>).order_type }}</span>
          </li>
          <li v-if="!streamEvents.length" class="empty">（尚無事件）</li>
        </ul>

        <h3>指令（pending / 歷史）</h3>
        <ul class="orders" data-testid="order-list">
          <li v-for="o in orders" :key="o.id" data-testid="order-row">
            {{ o.order_type }} · {{ o.status }}
            <button
              v-if="o.status === 'VALIDATED' || o.status === 'PENDING'"
              data-testid="cancel-order"
              @click="cancel(o.id)"
            >
              取消
            </button>
          </li>
          <li v-if="!orders.length" class="empty">（無指令）</li>
        </ul>
      </aside>

      <div class="map-wrap">
        <ClientOnly>
          <MapCanvas
            :hex-visible="hex"
            :hillshade-visible="hillshade"
            :own-units="ownUnits"
            :contacts="contacts"
            :current-tick="currentTick"
            @map-click="onMapClick"
          />
          <template #fallback>
            <div class="map-loading" data-testid="map-loading">地圖載入中…</div>
          </template>
        </ClientOnly>
        <LayerToggles v-model:hex="hex" v-model:hillshade="hillshade" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.cop {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0a1626;
  color: #e2e8f0;
}
.cop-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: #0f172a;
  border-bottom: 1px solid #1e293b;
}
.cop-bar button {
  padding: 0.25rem 0.75rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
}
.sid,
.count {
  font-size: 0.875rem;
  color: #94a3b8;
}
.body {
  display: flex;
  flex: 1;
  min-height: 0;
}
.panel {
  width: 18rem;
  overflow-y: auto;
  padding: 0.75rem 1rem;
  background: #0f172a;
  border-right: 1px solid #1e293b;
  font-size: 0.8125rem;
}
.panel h3 {
  margin: 0.75rem 0 0.375rem;
  font-size: 0.8125rem;
  color: #94a3b8;
}
.units,
.orders,
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
.units li,
.orders li {
  padding: 0.375rem 0.5rem;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
  cursor: pointer;
}
.units li.sel {
  border-color: #2563eb;
  background: #172554;
}
.empty {
  color: #64748b;
  cursor: default !important;
}
.order {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  margin: 0.5rem 0;
}
.order select,
.order button {
  padding: 0.375rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  cursor: pointer;
}
.order button.armed {
  border-color: #eab308;
}
.dest {
  font-family: monospace;
  color: #94a3b8;
}
.precheck .ok {
  color: #4ade80;
}
.precheck .bad {
  color: #f87171;
}
.precheck ul {
  margin: 0.25rem 0 0;
  padding-left: 1rem;
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
.map-wrap {
  position: relative;
  flex: 1;
}
.map-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
}
</style>
