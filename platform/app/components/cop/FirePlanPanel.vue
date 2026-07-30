<script setup lang="ts">
/**
 * 火力計畫小工具（WP-C10.3）——預劃目標清單 + 待命目標一鍵呼叫。
 *
 * 建立計畫的方式刻意做得直白：**在地圖上點好落點再回來按「加入計畫」**，
 * 用的是下令面板同一個「火力任務」落點（`currentAim`）——兩處各做一套點地圖互動，
 * 使用者要學兩次，而且第二套一定會漏掉誤傷警語。
 *
 * 可見性由後端決定（紅線 3）；`FIRED` 標籤寫「已下令」而非「已命中」，
 * 因為裁決失敗的令也會是 FIRED——打中沒有要看戰況事件。
 */
import { UNKNOWN_REASON } from '~/composables/useLabels'
import { computed, onMounted, ref } from 'vue'
import {
  SCHEDULE_LABELS,
  TARGET_STATUS_LABELS,
  createFirePlan,
  deleteFirePlan,
  fetchFirePlans,
  fireFirePlanTarget,
  type FirePlanView,
  type FireSchedule,
  type NewFireTarget,
} from '~/composables/useFirePlans'
import type { UnitView } from '~/composables/useOrders'

const props = defineProps<{
  sessionId: string
  /** 本陣營可下令的單位（供挑砲兵）。 */
  ownUnits: UnitView[]
  /** COP 下令面板當前的火力任務落點；沒有就不能加目標。 */
  currentAim: { lng: number; lat: number } | null
  /** 當前 sim tick——定時目標的預設時間以此為基準，讓「H+N」看得懂。 */
  currentTick: number
}>()

const emit = defineEmits<{ (e: 'focus-target', value: { lng: number; lat: number }): void }>()

const plans = ref<FirePlanView[]>([])
const err = ref('')
const busy = ref(false)

// 新計畫草稿：先在本地累積目標，一次送出（後端的建立端點要求至少一個目標）。
const draftName = ref('')
const draftTargets = ref<NewFireTarget[]>([])
const draftShooter = ref('')
const draftRounds = ref(4)
const draftSchedule = ref<FireSchedule>('ON_CALL')
const draftAtTick = ref<number | null>(null)

const canAddTarget = computed(() => !!props.currentAim && !!draftShooter.value)
const canCreate = computed(() => !!draftName.value.trim() && draftTargets.value.length > 0)

async function reload() {
  err.value = ''
  try {
    plans.value = await fetchFirePlans(props.sessionId)
  } catch (e) {
    err.value = `載入失敗：${(e as { message?: string }).message ?? UNKNOWN_REASON}`
  }
}
onMounted(reload)

function addTarget() {
  const aim = props.currentAim
  if (!aim || !draftShooter.value) return
  draftTargets.value = [
    ...draftTargets.value,
    {
      label: `AB${1000 + draftTargets.value.length}`,
      target_lat: aim.lat,
      target_lng: aim.lng,
      rounds: draftRounds.value,
      shooter_unit_id: draftShooter.value,
      schedule: draftSchedule.value,
      at_tick: draftSchedule.value === 'AT_TICK' ? (draftAtTick.value ?? props.currentTick) : null,
    },
  ]
}

function dropTarget(i: number) {
  draftTargets.value = draftTargets.value.filter((_, idx) => idx !== i)
}

async function doCreate() {
  if (!canCreate.value) return
  busy.value = true
  try {
    await createFirePlan(props.sessionId, draftName.value.trim(), draftTargets.value)
    draftName.value = ''
    draftTargets.value = []
    await reload()
  } catch (e) {
    err.value = `建立失敗：${(e as { message?: string }).message ?? UNKNOWN_REASON}`
  } finally {
    busy.value = false
  }
}

async function doFire(planId: string, targetId: string) {
  busy.value = true
  err.value = ''
  try {
    const out = await fireFirePlanTarget(props.sessionId, planId, targetId)
    if (out.status === 'FAILED') err.value = `未能執行：${out.failure_reason ?? ''}`
    await reload()
  } catch (e) {
    err.value = `呼叫失敗：${(e as { message?: string }).message ?? UNKNOWN_REASON}`
  } finally {
    busy.value = false
  }
}

async function doDelete(planId: string) {
  busy.value = true
  try {
    await deleteFirePlan(props.sessionId, planId)
    await reload()
  } catch (e) {
    err.value = `刪除失敗：${(e as { message?: string }).message ?? UNKNOWN_REASON}`
  } finally {
    busy.value = false
  }
}

function unitName(id: string): string {
  return props.ownUnits.find((u) => u.id === id)?.designation ?? id.slice(0, 8)
}
</script>

<template>
<div class="fp">
  <p v-if="err" class="fp-err" data-testid="fireplan-error">{{ err }}</p>

  <!-- 既有計畫 -->
  <div class="fp-sec">
    <div class="fp-hd">計畫（{{ plans.length }}）</div>
    <ul class="fp-plans" data-testid="fireplan-list">
      <li v-for="p in plans" :key="p.id" data-testid="fireplan-item">
        <div class="fp-prow">
          <b>{{ p.name }}</b>
          <span class="dim">T{{ p.created_at_tick }} · {{ p.created_by ?? '?' }}</span>
          <button class="fp-x" title="刪除此計畫" @click="doDelete(p.id)">
            <i class="pi pi-trash" />
          </button>
        </div>
        <ul class="fp-tgts">
          <li v-for="t in p.targets" :key="t.id" data-testid="fireplan-target">
            <button
              class="fp-loc"
              title="在地圖上看這個落點"
              @click="emit('focus-target', { lng: t.target_lng, lat: t.target_lat })"
            >
              🎯 {{ t.label || `#${t.seq}` }}
            </button>
            <span class="dim">{{ unitName(t.shooter_unit_id) }} · {{ t.rounds }} 發</span>
            <span class="fp-sch">
              {{ SCHEDULE_LABELS[t.schedule] }}<template v-if="t.schedule === 'AT_TICK'"> T{{ t.at_tick }}</template>
            </span>
            <span class="fp-st" :class="`st-${t.status}`">{{ TARGET_STATUS_LABELS[t.status] }}</span>
            <button
              v-if="t.status === 'PENDING' && t.schedule === 'ON_CALL'"
              class="fp-fire"
              :disabled="busy"
              data-testid="fire-on-call"
              @click="doFire(p.id, t.id)"
            >
              呼叫
            </button>
            <span v-if="t.failure_reason" class="fp-why">{{ t.failure_reason }}</span>
          </li>
          <li v-if="!p.targets.length" class="dim">（無目標）</li>
        </ul>
      </li>
      <li v-if="!plans.length" class="dim">（尚無火力計畫）</li>
    </ul>
  </div>

  <!-- 新計畫 -->
  <div class="fp-sec">
    <div class="fp-hd">新增計畫</div>
    <input v-model="draftName" placeholder="計畫名稱（如：攻擊準備射擊）" data-testid="fireplan-name">
    <div class="fp-row">
      <select v-model="draftShooter" data-testid="fireplan-shooter">
        <option value="">選砲兵單位</option>
        <option v-for="u in ownUnits" :key="u.id" :value="u.id">{{ u.designation }}</option>
      </select>
      <input v-model.number="draftRounds" type="number" min="1" max="200" title="發數">
    </div>
    <div class="fp-row">
      <select v-model="draftSchedule" data-testid="fireplan-schedule">
        <option value="ON_CALL">待命（FSO 呼叫）</option>
        <option value="AT_TICK">定時（屆時自動射擊）</option>
      </select>
      <input
        v-if="draftSchedule === 'AT_TICK'"
        v-model.number="draftAtTick"
        type="number"
        min="0"
        :placeholder="`tick（現在 ${currentTick}）`"
        data-testid="fireplan-at-tick"
      >
    </div>
    <p class="fp-hint">
      落點取自「單位／下令」面板的火力任務落點——先在那裡點好地圖，再回來加入。
    </p>
    <button :disabled="!canAddTarget" data-testid="fireplan-add-target" @click="addTarget">
      加入目標<template v-if="currentAim">（{{ currentAim.lat.toFixed(4) }}, {{ currentAim.lng.toFixed(4) }}）</template>
    </button>
    <ul class="fp-draft" data-testid="fireplan-draft">
      <li v-for="(t, i) in draftTargets" :key="i">
        {{ t.label }} · {{ unitName(t.shooter_unit_id) }} · {{ t.rounds }} 發 ·
        {{ SCHEDULE_LABELS[t.schedule] }}<template v-if="t.at_tick != null"> T{{ t.at_tick }}</template>
        <button class="fp-x" @click="dropTarget(i)"><i class="pi pi-times" /></button>
      </li>
    </ul>
    <button :disabled="!canCreate || busy" data-testid="fireplan-create" @click="doCreate">
      建立計畫（{{ draftTargets.length }} 個目標）
    </button>
    <p class="fp-warn">
      ⚠ 面射擊<b>敵我皆受損</b>。本局若要求火協，預劃目標一樣需要已核准的火力支援申請。
    </p>
  </div>
</div>
</template>

<style scoped>
.fp {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  font-size: 0.78rem;
  color: #cbd5e1;
}
.fp-hd {
  font-size: 0.78rem;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 0.3rem;
}
.fp-sec {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.fp-err {
  margin: 0;
  color: #f87171;
  font-size: 0.72rem;
}
.dim {
  color: #64748b;
  font-size: 0.7rem;
}
.fp-plans,
.fp-tgts,
.fp-draft {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.fp-plans > li {
  border: 1px solid #1e293b;
  border-radius: 0.3rem;
  padding: 0.35rem 0.45rem;
}
.fp-prow {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.fp-prow b {
  color: #e2e8f0;
}
.fp-tgts {
  margin-top: 0.25rem;
  padding-left: 0.35rem;
  border-left: 2px solid #1e293b;
}
.fp-tgts > li {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
  font-size: 0.72rem;
}
.fp-loc {
  border: none;
  background: transparent;
  color: #fca5a5;
  cursor: pointer;
  padding: 0;
  font-size: 0.72rem;
}
.fp-sch {
  color: #93c5fd;
}
.fp-st {
  padding: 0 0.28rem;
  border-radius: 0.2rem;
  background: #1e293b;
}
.fp-st.st-FIRED {
  color: #86efac;
}
.fp-st.st-FAILED {
  color: #fca5a5;
}
.fp-st.st-SKIPPED {
  color: #64748b;
}
.fp-why {
  flex-basis: 100%;
  color: #fca5a5;
  font-size: 0.68rem;
}
.fp-row {
  display: flex;
  gap: 0.3rem;
}
.fp input,
.fp select {
  flex: 1;
  min-width: 0;
  padding: 0.22rem 0.35rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.74rem;
}
.fp button {
  padding: 0.22rem 0.45rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  cursor: pointer;
  font-size: 0.74rem;
}
.fp button:disabled {
  opacity: 0.45;
  cursor: default;
}
.fp .fp-x {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #f87171;
  padding: 0 0.2rem;
}
.fp .fp-fire {
  padding: 0.05rem 0.35rem;
  font-size: 0.68rem;
  border-color: #b45309;
  color: #fcd34d;
}
.fp-hint,
.fp-warn {
  margin: 0;
  font-size: 0.68rem;
  line-height: 1.4;
}
.fp-hint {
  color: #64748b;
}
.fp-warn {
  color: #fca5a5;
}
</style>
