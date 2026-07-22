<script setup lang="ts">
// 白軍控制台（O7.4，SPEC §12）——視角切換 / 時間控制 / 事件注入 / 事件流。限統裁角色。
import { useSessionStreamStore } from '~/stores/sessionStream'
import type { UnitView } from '~/composables/useOrders'
import { apiFetch } from '~/composables/useApi'
import {
  injectEvent,
  sessionControl,
  unitsAsFaction,
  type ControlAction,
} from '~/composables/useWhiteCell'
import type { InjectAction } from '~/composables/useConditionDsl'

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

// 即時注入（trigger-free）：event_type + payload + 目標陣營（空＝廣播全體）。
const injectAction = ref<InjectAction>({ event_type: 'BRIDGE_DESTROYED', payload: {}, faction: undefined })
async function doInject() {
  try {
    await injectEvent(
      sessionId,
      injectAction.value.event_type,
      injectAction.value.payload ?? {},
      injectAction.value.faction ?? null,
    )
    status.value = `已注入 ${injectAction.value.event_type}`
  } catch (e) {
    status.value = `注入失敗：${(e as { message?: string }).message ?? e}`
  }
}

// 編裝編輯 + 各軍自編權限（#6）
const editUnitId = ref('')
const editDesignation = ref('')
const editHealth = ref(100)
const editAttrs = ref('{}')
const orbatFactions = ref<string[]>([])

function pickUnit(u: UnitView) {
  editUnitId.value = u.id
  editDesignation.value = u.designation
  editHealth.value = Math.round(u.health)
  editAttrs.value = '{}'
}
async function saveUnit() {
  if (!editUnitId.value) return
  const body: Record<string, unknown> = {
    designation: editDesignation.value,
    health_status: editHealth.value,
  }
  try {
    body.attributes = JSON.parse(editAttrs.value)
  } catch {
    status.value = 'attributes 需為合法 JSON'
    return
  }
  try {
    await apiFetch(`/sessions/${sessionId}/units/${editUnitId.value}`, { method: 'PATCH', body })
    status.value = '已更新單位編裝'
    editUnitId.value = ''
    await loadUnits()
  } catch (e) {
    status.value = `更新失敗：${(e as { code?: string }).code ?? e}`
  }
}
async function loadPerms() {
  const r = await apiFetch<{ factions: string[] }>(
    `/sessions/${sessionId}/orbat-permissions`,
  ).catch(() => ({ factions: [] as string[] }))
  orbatFactions.value = r.factions
}
async function togglePerm(f: string) {
  const set = new Set(orbatFactions.value)
  if (set.has(f)) set.delete(f)
  else set.add(f)
  orbatFactions.value = [...set]
  try {
    await apiFetch(`/sessions/${sessionId}/orbat-permissions`, {
      method: 'PUT',
      body: { factions: orbatFactions.value },
    })
    status.value = `自編權限：${orbatFactions.value.join('、') || '（僅白軍）'}`
  } catch (e) {
    status.value = `設定失敗：${(e as { code?: string }).code ?? e}`
  }
}

onMounted(() => {
  loadUnits()
  loadPerms()
  stream.connect(sessionId)
})
onUnmounted(() => stream.disconnect())
watch(viewpoint, loadUnits)
</script>

<template>
  <div class="wc" data-testid="white-cell-console">
    <header class="wc-bar">
      <button data-testid="wc-back-cop" @click="navigateTo(`/session/${sessionId}/cop`)">← 圖台</button>
      <h1>白軍控制台 · {{ sessionId }}</h1>
      <a class="help" href="/help" target="_blank">操作教學</a>
    </header>
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
      <div class="inject-box">
        <h2>注入事件</h2>
        <InjectActionForm v-model="injectAction" :factions="factions" event-testid="inject-type" />
        <button data-testid="do-inject" @click="doInject">注入</button>
      </div>
    </section>

    <section>
      <h2>編裝編輯（#6）— 各軍自編權限</h2>
      <div class="perms">
        <label v-for="f in factions" :key="f">
          <input
            type="checkbox"
            :checked="orbatFactions.includes(f)"
            :data-testid="`perm-${f}`"
            @change="togglePerm(f)"
          >
          {{ f }} 可自編本軍
        </label>
        <span v-if="!factions.length" class="hint">（無單位）</span>
      </div>
    </section>

    <section>
      <h2>單位（{{ viewpoint || '全知' }}）— 點選編輯</h2>
      <ul data-testid="wc-unit-list" class="units">
        <li
          v-for="u in units"
          :key="u.id"
          :class="{ sel: u.id === editUnitId }"
          data-testid="wc-unit-item"
          @click="pickUnit(u)"
        >
          {{ u.designation }} · {{ u.faction }} · {{ Math.round(u.health) }}%
        </li>
      </ul>
      <div v-if="editUnitId" class="edit" data-testid="unit-edit">
        <label>番號 <input v-model="editDesignation" data-testid="edit-designation"></label>
        <label>戰力% <input v-model.number="editHealth" type="number" min="0" max="100" data-testid="edit-health"></label>
        <label>屬性(JSON) <input v-model="editAttrs" data-testid="edit-attrs"></label>
        <button data-testid="save-unit" @click="saveUnit">儲存編裝</button>
      </div>
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
.wc { max-width: 1000px; margin: 0 auto; padding: 1rem; color: #e2e8f0; }
.wc-bar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.wc-bar h1 { font-size: 1.25rem; margin: 0; }
.wc-bar .help { margin-left: auto; font-size: 0.8125rem; color: #60a5fa; text-decoration: none; }
.wc-bar .help:hover { text-decoration: underline; }
h2 { font-size: 0.9375rem; color: #94a3b8; margin: 0 0 0.5rem; }
.controls { display: flex; gap: 2rem; flex-wrap: wrap; }
.status { color: #4ade80; }
section { border-top: 1px solid #1e293b; padding-top: 0.75rem; margin-top: 1rem; }
ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.8125rem; }
input, select {
  padding: 0.375rem 0.5rem; border: 1px solid #334155; border-radius: 0.25rem;
  background: #0f172a; color: #e2e8f0;
}
button {
  margin-right: 0.4rem; padding: 0.375rem 0.75rem; border: 1px solid #334155;
  border-radius: 0.25rem; background: #1e293b; color: #e2e8f0; cursor: pointer;
}
button:hover { border-color: #2563eb; }
.units li { cursor: pointer; padding: 0.25rem 0.5rem; border-radius: 0.25rem; }
.units li:hover { background: #1e293b; }
.units li.sel { background: #172554; outline: 1px solid #2563eb; }
.perms { display: flex; gap: 1rem; flex-wrap: wrap; }
.perms label { display: flex; gap: 0.375rem; align-items: center; }
.edit { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; margin-top: 0.5rem; }
.edit label { display: flex; gap: 0.375rem; align-items: center; font-size: 0.8125rem; }
.hint { color: #64748b; }
</style>
