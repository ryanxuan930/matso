<script setup lang="ts">
import type { Contact, OwnUnit } from '~/composables/useUnits'
import type { UnitView, OrderResponse } from '~/composables/useOrders'
import type { ApiError } from '~/composables/useApi'
import { apiFetch } from '~/composables/useApi'
import { buildBasemapSources } from '~/composables/useMapStyle'
import { cancelOrder, fetchOrders, fetchUnits, submitOrder } from '~/composables/useOrders'

// COP（SPEC §13.1/§13.4）：地圖基座（O4.2）+ 單位/fog of war（O4.4）+ 下令 UX（O4.5）。
const route = useRoute()
const sessionId = computed(() => String(route.params.id))

// 白軍控制台（時間控制 / 注入 / 視角）限統裁角色（SPEC §12）；其餘角色不顯示入口。
const auth = useAuthStore()
const canControl = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF'].includes(auth.user?.role ?? ''),
)

const hex = ref(false)
const hillshade = ref(false)
const currentTick = ref(100)

// 底圖來源（可抽換，#2）：離線 / 街道 / 衛星 / 軍用…由 runtimeConfig 注入。
const basemap = ref('offline')
const _pub = useRuntimeConfig().public
const basemapSources = buildBasemapSources({
  tileUrl: _pub.tileUrl as string,
  satelliteUrl: _pub.satelliteUrl as string | undefined,
  basemaps: _pub.basemaps as never,
})

// 是否已設定離線 tile server（有 .mbtiles）。未設 → 顯示離線底圖提示（SPEC §13.2）。
const hasTiles = computed(() => !!_pub.tileUrl)

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
const myFaction = ref<string>('') // 觀測者陣營（GET /sessions.my_faction）
const orders = ref<OrderResponse[]>([])
const selectedId = ref<string | null>(null)
const orderType = ref<'MOVE' | 'ENGAGE'>('MOVE')
const destH3 = ref<string | null>(null)
const targeting = ref(false)
const targetUnitId = ref<string | null>(null)
const precheck = ref<OrderResponse['precheck'] | null>(null)
const message = ref('')

// 真單位依「我方 / 他軍」分流渲染：我方＝友軍符號（可選取指揮）；他軍＝敵情符號（可鎖為攻擊目標）。
// myFaction 未知（純白軍全知）時，全部以友軍呈現以便至少可見。
const realAsOwn = computed<OwnUnit[]>(() =>
  realUnits.value
    .filter((u) => !myFaction.value || u.faction === myFaction.value)
    .map((u) => ({
      id: u.id,
      faction: (u.faction as OwnUnit['faction']) ?? 'BLUE',
      lng: u.lng ?? 121,
      lat: u.lat ?? 23.7,
      comms: (u.comms as OwnUnit['comms']) ?? 'ONLINE',
      lastReportedTick: 100,
    })),
)
const realAsContacts = computed<Contact[]>(() =>
  myFaction.value
    ? realUnits.value
        .filter((u) => u.faction !== myFaction.value)
        .map((u) => ({
          contactId: u.id,
          fidelity: 'IDENTIFIED' as const,
          lng: u.lng ?? 121,
          lat: u.lat ?? 23.7,
          errorRadiusM: 0,
          designation: u.designation,
          lastSeenTick: 100,
          faction: u.faction,
          relation: 'HOSTILE' as const, // 前端保守標敵；真 ROE 由後端關係矩陣裁決
        }))
    : [],
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
const DEMO_CONTACTS: Contact[] = [
  { contactId: 'c-det', fidelity: 'DETECTED', lng: 121.4, lat: 23.5, errorRadiusM: 2000, lastSeenTick: 40 },
  { contactId: 'c-cls', fidelity: 'CLASSIFIED', lng: 121.5, lat: 23.6, errorRadiusM: 800, unitType: 'ARMOR', lastSeenTick: 80 },
  { contactId: 'c-id', fidelity: 'IDENTIFIED', lng: 121.6, lat: 23.7, errorRadiusM: 200, unitType: 'ARTILLERY', designation: '3-BN', lastSeenTick: 98, faction: 'RED', relation: 'HOSTILE' },
  { contactId: 'c-neutral', fidelity: 'IDENTIFIED', lng: 121.55, lat: 23.55, errorRadiusM: 200, unitType: 'RECON', designation: 'Y-1', lastSeenTick: 96, faction: 'YELLOW', relation: 'NEUTRAL' },
]
const contacts = computed<Contact[]>(() => [...DEMO_CONTACTS, ...realAsContacts.value])

// 可作 ENGAGE 目標的真單位（他軍）——供下拉與地圖點選鎖定共用。
const realUnitIds = computed(() => new Set(realUnits.value.map((u) => u.id)))
const engageTargets = computed(() =>
  realUnits.value.filter((u) => u.id !== selectedId.value && u.faction !== myFaction.value),
)

async function refresh() {
  realUnits.value = await fetchUnits(sessionId.value).catch(() => [])
  orders.value = await fetchOrders(sessionId.value).catch(() => [])
  // 我方陣營（決定友/敵渲染與目標可選集）——由 session 摘要取得。
  const sessions = await apiFetch<{ id: string; my_faction?: string }[]>('/sessions').catch(() => [])
  myFaction.value = sessions.find((s) => s.id === sessionId.value)?.my_faction ?? ''
}

function selectUnit(id: string) {
  selectedId.value = id
  precheck.value = null
  message.value = ''
  destH3.value = null
  targetUnitId.value = null
}

function onMapClick(e: { h3: string }) {
  if (orderType.value === 'MOVE' && targeting.value) {
    destH3.value = e.h3
    targeting.value = false
  }
}

// 點地圖上的單位符號：我方 → 選取指揮；他軍（有選取的我方單位時）→ 鎖為 ENGAGE 目標。
function onUnitClick(e: { id: string; faction: string; kind: string }) {
  const isReal = realUnitIds.value.has(e.id)
  const isMine = isReal && e.faction === myFaction.value
  if (isMine) {
    selectUnit(e.id)
    return
  }
  if (isReal && selectedId.value && e.faction !== myFaction.value) {
    orderType.value = 'ENGAGE'
    targetUnitId.value = e.id
    targeting.value = false
    precheck.value = null
    message.value = `已鎖定目標：${realUnits.value.find((u) => u.id === e.id)?.designation ?? e.id}`
  }
}

const selectedUnit = computed(() => realUnits.value.find((u) => u.id === selectedId.value) ?? null)
const targetUnit = computed(() => realUnits.value.find((u) => u.id === targetUnitId.value) ?? null)

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

onMounted(async () => {
  if (!auth.user) await auth.fetchMe() // 直接開/重整 COP 時補抓使用者，讓角色相關入口（白軍控制台）正確顯示
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
      <nav class="cop-nav">
        <button
          v-if="canControl"
          data-testid="nav-white-cell"
          @click="navigateTo(`/session/${sessionId}/white-cell`)"
        >
          ⚙ 白軍控制台
        </button>
        <button data-testid="nav-aar" @click="navigateTo(`/session/${sessionId}/aar`)">📊 AAR</button>
        <a class="help" href="/help" target="_blank">操作教學</a>
      </nav>
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
          <h3>下令 · <span class="selunit" data-testid="selected-unit">{{ selectedUnit?.designation ?? selectedId }}</span></h3>
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
            <div class="hint">點地圖上的敵方單位鎖定目標（紅環），或從清單選：</div>
            <select v-model="targetUnitId" data-testid="engage-target">
              <option :value="null">選目標</option>
              <option v-for="u in engageTargets" :key="u.id" :value="u.id">{{ u.designation }}</option>
            </select>
            <div class="dest" data-testid="target-label">
              {{ targetUnit ? `🎯 ${targetUnit.designation}（${targetUnit.faction}）` : '未鎖定目標' }}
            </div>
          </template>
          <button
            data-testid="submit-order"
            :disabled="orderType === 'MOVE' ? !destH3 : !targetUnitId"
            @click="submit"
          >
            {{ orderType === 'MOVE' ? '送出移動' : '送出攻擊' }}
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
            :selected-id="selectedId"
            :target-id="targetUnitId"
            :basemap-id="basemap"
            @map-click="onMapClick"
            @unit-click="onUnitClick"
          />
          <template #fallback>
            <div class="map-loading" data-testid="map-loading">地圖載入中…</div>
          </template>
        </ClientOnly>
        <LayerToggles
          v-model:hex="hex"
          v-model:hillshade="hillshade"
          v-model:basemap="basemap"
          :hillshade-enabled="hasTiles"
          :basemaps="basemapSources"
        />
        <div v-if="!hasTiles" class="map-notice" data-testid="map-notice">
          <strong>離線底圖模式</strong>
          <span>目前顯示經緯格線 + 單位符號（無向量瓦片）。要載入台灣街道/地形底圖，需由
            <code>taiwan.osm.pbf</code> 產生 mbtiles 並啟用 tileserver — 見「操作教學」。</span>
        </div>
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
.cop-nav {
  margin-left: auto;
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.cop-nav button {
  padding: 0.25rem 0.75rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
}
.cop-nav button:hover {
  border-color: #2563eb;
}
.cop-nav .help {
  font-size: 0.8125rem;
  color: #60a5fa;
  text-decoration: none;
}
.cop-nav .help:hover {
  text-decoration: underline;
}
.map-notice {
  position: absolute;
  left: 1rem;
  bottom: 1rem;
  z-index: 10;
  max-width: 22rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.625rem 0.875rem;
  border-radius: 0.5rem;
  border: 1px solid #1e3a5f;
  background: rgba(15, 23, 42, 0.9);
  font-size: 0.75rem;
  color: #94a3b8;
  line-height: 1.5;
}
.map-notice strong {
  color: #e2e8f0;
}
.map-notice code {
  color: #7dd3fc;
  font-size: 0.7rem;
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
.order .selunit {
  color: #60a5fa;
  font-weight: 600;
}
.order .hint {
  color: #94a3b8;
  font-size: 0.72rem;
  line-height: 1.4;
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
