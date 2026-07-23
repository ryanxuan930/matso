<script setup lang="ts">
// 注入動作編輯器（GOAL#7）——event_type + 目標陣營 + payload key/value（每值 JSON.parse 強制）。
// 無 trigger（即時注入與 MSEL 共用此表單）。
import type { InjectAction } from '~/composables/useConditionDsl'

const props = defineProps<{ modelValue: InjectAction; factions: string[]; eventTestid?: string }>()
const emit = defineEmits<{ 'update:modelValue': [InjectAction] }>()

const EVENT_SUGGESTIONS = ['BRIDGE_DESTROYED', 'REINFORCEMENT', 'WEATHER_CHANGE', 'INTEL_REPORT']
const dlId = `inject-events-${useId()}`

// payload 以本地 rows 為編輯真源；每格值以 JSON 文字呈現，送出時逐值 JSON.parse 強制。
interface Row { key: string; value: string }
const rows = ref<Row[]>([])

function payloadToRows(p: Record<string, unknown> | undefined): Row[] {
  return Object.entries(p ?? {}).map(([key, v]) => ({ key, value: JSON.stringify(v) }))
}
function coerce(text: string): unknown {
  const t = text.trim()
  if (t === '') return ''
  try {
    return JSON.parse(t)
  } catch {
    return text // 非合法 JSON → 當純字串，容錯
  }
}
function rowsToPayload(rs: Row[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const r of rs) {
    if (r.key.trim() === '') continue
    out[r.key] = coerce(r.value)
  }
  return out
}

// 外部（如匯入）改動 payload → 重建 rows；內部送出造成的相等變更則跳過（不重置游標）。
watch(
  () => props.modelValue.payload,
  (p) => {
    if (JSON.stringify(p ?? {}) !== JSON.stringify(rowsToPayload(rows.value))) {
      rows.value = payloadToRows(p)
    }
  },
  { immediate: true, deep: true },
)

function emitPayload() {
  emit('update:modelValue', { ...props.modelValue, payload: rowsToPayload(rows.value) })
}
function addRow() {
  rows.value.push({ key: '', value: '' })
}
function removeRow(i: number) {
  rows.value.splice(i, 1)
  emitPayload()
}

function setEventType(v: string | undefined) {
  emit('update:modelValue', { ...props.modelValue, event_type: v ?? '' })
}

const factionOptions = computed(() => [
  { label: '（廣播全體）', value: '' },
  ...props.factions.map((f) => ({ label: f, value: f })),
])
function setFaction(v: string) {
  emit('update:modelValue', { ...props.modelValue, faction: v === '' ? undefined : v })
}
</script>

<template>
  <div class="iaf" data-testid="inject-action-form">
    <label class="iaf-field">事件類型
      <InputText
        :model-value="modelValue.event_type"
        :list="dlId"
        :data-testid="eventTestid"
        size="small"
        placeholder="event_type"
        @update:model-value="setEventType"
      />
      <datalist :id="dlId">
        <option v-for="s in EVENT_SUGGESTIONS" :key="s" :value="s" />
      </datalist>
    </label>

    <label class="iaf-field">目標陣營
      <Select
        :model-value="modelValue.faction ?? ''"
        :options="factionOptions"
        option-label="label"
        option-value="value"
        size="small"
        @update:model-value="setFaction"
      />
    </label>

    <div class="iaf-payload">
      <span class="iaf-payload-label">payload
        <Button size="small" text data-testid="iaf-add-payload" @click="addRow">＋</Button>
      </span>
      <div v-for="(r, i) in rows" :key="i" class="iaf-row" data-testid="iaf-payload-row">
        <InputText v-model="r.key" size="small" placeholder="key" @update:model-value="emitPayload" />
        <InputText v-model="r.value" size="small" placeholder="value（JSON）" class="iaf-val" @update:model-value="emitPayload" />
        <Button size="small" text severity="danger" @click="removeRow(i)">✕</Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.iaf { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: flex-start; }
.iaf-field { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.8125rem; color: #94a3b8; }
.iaf-payload { display: flex; flex-direction: column; gap: 0.25rem; }
.iaf-payload-label { font-size: 0.8125rem; color: #94a3b8; display: inline-flex; align-items: center; gap: 0.25rem; }
.iaf-row { display: flex; gap: 0.3rem; align-items: center; }
.iaf-val { min-width: 10rem; }
</style>
