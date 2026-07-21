<script setup lang="ts">
// 白軍控制台（O7.4，SPEC §12）——視角切換 / 時間控制 / 事件注入 / 事件流。限統裁角色。
import { useSessionStreamStore } from '~/stores/sessionStream'
import type { UnitView } from '~/composables/useOrders'
import {
  injectEvent,
  sessionControl,
  unitsAsFaction,
  type ControlAction,
} from '~/composables/useWhiteCell'

const route = useRoute()
const sessionId = route.params.id as string
const stream = useSessionStreamStore()

const viewpoint = ref<string>('') // '' = 全知 god view
const units = ref<UnitView[]>([])
const factions = computed(() => [...new Set(units.value.map((u) => u.faction))].sort())
const status = ref('')

async function loadUnits() {
  try {
    units.value = await unitsAsFaction(sessionId, viewpoint.value || null)
  } catch (e) {
    status.value = `讀取失敗：${(e as { message?: string }).message ?? e}`
  }
}

async function control(action: ControlAction) {
  const target = action === 'ROLLBACK' ? Number(prompt('回滾到哪個 tick？') ?? 0) : undefined
  try {
    await sessionControl(sessionId, action, target)
    status.value = `已送出 ${action}`
  } catch (e) {
    status.value = `控制失敗：${(e as { message?: string }).message ?? e}`
  }
}

const injectType = ref('BRIDGE_DESTROYED')
async function doInject() {
  try {
    await injectEvent(sessionId, injectType.value, { note: 'White Cell inject' })
    status.value = `已注入 ${injectType.value}`
  } catch (e) {
    status.value = `注入失敗：${(e as { message?: string }).message ?? e}`
  }
}

onMounted(() => {
  loadUnits()
  stream.connect(sessionId)
})
onUnmounted(() => stream.disconnect())
watch(viewpoint, loadUnits)
</script>

<template>
  <div class="wc" data-testid="white-cell-console">
    <h1>白軍控制台 · {{ sessionId }}</h1>
    <p v-if="status" class="status" data-testid="wc-status">{{ status }}</p>

    <section class="controls">
      <div>
        <h2>視角</h2>
        <select v-model="viewpoint" data-testid="viewpoint">
          <option value="">全知（God View）</option>
          <option v-for="f in factions" :key="f" :value="f">{{ f }} 視角</option>
        </select>
        <span data-testid="unit-count">{{ units.length }} 單位</span>
      </div>
      <div>
        <h2>時間控制</h2>
        <button data-testid="pause" @click="control('PAUSE')">⏸ 暫停</button>
        <button data-testid="resume" @click="control('RESUME')">▶ 續行</button>
        <button data-testid="rollback" @click="control('ROLLBACK')">⏪ 回滾</button>
      </div>
      <div>
        <h2>注入事件</h2>
        <input v-model="injectType" data-testid="inject-type">
        <button data-testid="do-inject" @click="doInject">注入</button>
      </div>
    </section>

    <section>
      <h2>單位（{{ viewpoint || '全知' }}）</h2>
      <ul data-testid="wc-unit-list">
        <li v-for="u in units" :key="u.id">{{ u.designation }} · {{ u.faction }} · {{ Math.round(u.health) }}%</li>
      </ul>
    </section>

    <section>
      <h2>戰況事件流（WS：{{ stream.status }}）</h2>
      <ul data-testid="wc-event-list">
        <li v-for="(e, i) in stream.events.slice(-20)" :key="i">
          #{{ e.seq }} {{ e.type }} {{ JSON.stringify(e.payload) }}
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.wc { max-width: 1000px; margin: 1rem auto; padding: 0 1rem; }
.controls { display: flex; gap: 2rem; flex-wrap: wrap; }
.status { color: #2a6; }
section { border-top: 1px solid #ccc; padding-top: 0.5rem; margin-top: 1rem; }
button { margin-right: 0.4rem; }
</style>
