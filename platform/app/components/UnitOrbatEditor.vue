<script setup lang="ts">
// 單位編裝編輯器（stage ①）——列出/增/刪裝備、設定彈藥。COP（本軍）+ 白軍（任一）共用。
// canEdit=false 時純唯讀（顯示 loadout）。
import {
  addUnitEquipment,
  editUnitEquipment,
  fetchEquipmentTemplates,
  fetchUnitEquipment,
  removeUnitEquipment,
  type EquipmentInstance,
  type EquipmentTemplate,
} from '~/composables/useEquipment'

const props = defineProps<{ sessionId: string; unitId: string; canEdit?: boolean }>()

const items = ref<EquipmentInstance[]>([])
const templates = ref<EquipmentTemplate[]>([])
const addId = ref('')
const busy = ref(false)
const err = ref('')

async function load() {
  items.value = await fetchUnitEquipment(props.sessionId, props.unitId).catch(() => [])
}
watch(() => props.unitId, load, { immediate: true })
onMounted(async () => {
  if (props.canEdit) templates.value = await fetchEquipmentTemplates().catch(() => [])
})

function rangeKm(bs: Record<string, unknown>): string | null {
  const r = bs?.max_range_m
  return typeof r === 'number' ? (r / 1000).toFixed(1) : null
}
function ammoOf(inst: EquipmentInstance): number | null {
  const a = (inst.current_state as Record<string, unknown>)?.ammo
  return typeof a === 'number' ? a : null
}
// 武器（消耗彈藥者）：有 ammo_types 或屬武器類別 → 需要彈藥欄。感測/通信/後勤等不需。
const WEAPON_CATEGORIES = ['KINETIC', 'ARTILLERY', 'MISSILE']
function usesAmmo(inst: EquipmentInstance): boolean {
  const at = (inst.base_stats as Record<string, unknown>)?.ammo_types
  if (Array.isArray(at) && at.length > 0) return true
  return WEAPON_CATEGORIES.includes(inst.category)
}

async function add() {
  if (!addId.value || busy.value) return
  busy.value = true
  err.value = ''
  try {
    await addUnitEquipment(props.sessionId, props.unitId, addId.value)
    addId.value = ''
    await load()
  } catch (e) {
    err.value = (e as { message?: string }).message ?? '新增失敗'
  } finally {
    busy.value = false
  }
}
async function remove(iid: string) {
  busy.value = true
  err.value = ''
  try {
    await removeUnitEquipment(props.sessionId, props.unitId, iid)
    await load()
  } catch (e) {
    err.value = (e as { message?: string }).message ?? '移除失敗'
  } finally {
    busy.value = false
  }
}
async function saveAmmo(inst: EquipmentInstance, ev: Event) {
  const v = Number((ev.target as HTMLInputElement).value)
  if (!Number.isFinite(v)) return
  busy.value = true
  err.value = ''
  try {
    await editUnitEquipment(props.sessionId, props.unitId, inst.id, { ammo: v })
    await load()
  } catch (e) {
    err.value = (e as { message?: string }).message ?? '更新失敗'
  } finally {
    busy.value = false
  }
}
// #30 建制數量：一件 instance 代表 N 件同型武器（如班內 7 支步槍）→ 驅動 squad 齊射火力。
async function saveQty(inst: EquipmentInstance, ev: Event) {
  const v = Math.max(1, Math.round(Number((ev.target as HTMLInputElement).value)))
  if (!Number.isFinite(v)) return
  busy.value = true
  err.value = ''
  try {
    await editUnitEquipment(props.sessionId, props.unitId, inst.id, {}, v)
    await load()
  } catch (e) {
    err.value = (e as { message?: string }).message ?? '更新失敗'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="orbat-editor" data-testid="orbat-editor">
    <ul class="eq-list">
      <li v-for="inst in items" :key="inst.id" data-testid="eq-item">
        <span class="eq-name">{{ inst.name }}</span>
        <span class="eq-cat">{{ inst.category }}</span>
        <span v-if="rangeKm(inst.base_stats)" class="eq-dim">{{ rangeKm(inst.base_stats) }} km</span>
        <label class="eq-qty" title="建制數量（班內同型武器件數，驅動 squad 齊射火力）">
          ×
          <input
            v-if="canEdit"
            type="number"
            min="1"
            step="1"
            :value="inst.quantity ?? 1"
            data-testid="eq-quantity"
            @change="saveQty(inst, $event)"
          >
          <span v-else>{{ inst.quantity ?? 1 }}</span>
        </label>
        <!-- 編輯：武器（消耗彈藥者）一律顯示彈藥欄（即使目前無值，預設 0 讓可設定，修 Javelin
             等無初始彈藥的武器沒有彈藥欄可填的問題）。唯讀：僅有值才顯示。 -->
        <label v-if="canEdit ? usesAmmo(inst) : ammoOf(inst) != null" class="eq-ammo">
          彈
          <input
            v-if="canEdit"
            type="number"
            min="0"
            :value="ammoOf(inst) ?? 0"
            data-testid="eq-ammo"
            @change="saveAmmo(inst, $event)"
          >
          <span v-else>{{ ammoOf(inst) }}</span>
        </label>
        <button
          v-if="canEdit"
          class="eq-rm"
          data-testid="eq-remove"
          :disabled="busy"
          title="移除"
          @click="remove(inst.id)"
        >
          ✕
        </button>
      </li>
      <li v-if="!items.length" class="eq-empty">（無裝備）</li>
    </ul>
    <div v-if="canEdit" class="eq-add">
      <select v-model="addId" data-testid="eq-add-select">
        <option value="">＋ 配發裝備…</option>
        <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}（{{ t.category }}）</option>
      </select>
      <button data-testid="eq-add" :disabled="!addId || busy" @click="add">加入</button>
    </div>
    <p v-if="err" class="eq-err" data-testid="eq-error">{{ err }}</p>
  </div>
</template>

<style scoped>
.orbat-editor {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.eq-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.eq-list li {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.74rem;
  min-width: 0;
}
.eq-name {
  color: #e2e8f0;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.eq-cat {
  color: #64748b;
  font-size: 0.6rem;
  flex: none;
}
.eq-dim {
  color: #94a3b8;
  flex: none;
  white-space: nowrap;
}
.eq-ammo,
.eq-qty {
  flex: none;
  color: #94a3b8;
  display: inline-flex;
  gap: 0.15rem;
  align-items: center;
}
.eq-qty {
  color: #fbbf24;
}
.eq-ammo input,
.eq-qty input {
  width: 3.8rem;
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  padding: 0.05rem 0.2rem;
}
.eq-qty input {
  width: 3rem;
}
.eq-rm {
  flex: none;
}
.eq-rm {
  border: none;
  background: transparent;
  color: #f87171;
  cursor: pointer;
  padding: 0 0.2rem;
}
.eq-empty {
  color: #64748b;
}
.eq-add {
  display: flex;
  gap: 0.3rem;
  align-items: center;
}
.eq-add select {
  flex: 1 1 auto;
  min-width: 0; /* 可縮，讓「加入」鈕不被擠出 */
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.2rem 0.3rem;
  font-size: 0.74rem;
}
.eq-add button {
  flex: 0 0 auto;
  padding: 0.2rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #1d4ed8;
  color: #fff;
  font-size: 0.74rem;
  cursor: pointer;
}
.eq-add button:disabled {
  opacity: 0.5;
  cursor: default;
}
.eq-add button {
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #172554;
  color: #e2e8f0;
  cursor: pointer;
  padding: 0.2rem 0.5rem;
  font-size: 0.74rem;
}
.eq-err {
  color: #f87171;
  font-size: 0.7rem;
  margin: 0;
}
</style>
