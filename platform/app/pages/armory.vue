<script setup lang="ts">
// 武器庫（裝備範本編輯，stage ②）——編輯 EquipmentTemplate.base_stats（武器屬性/功能）。
// KINETIC 走結構化表單（射程/命中 Ph/彈種/傷害…比照兵推）；其餘類別走 JSON。限統裁/管理。
import {
  createEquipmentTemplate,
  fetchEquipmentTemplates,
  updateEquipmentTemplate,
  type EquipmentTemplate,
} from '~/composables/useEquipment'
import {
  ARMOR_CLASSES,
  ARMOR_CLASS_LABELS,
  CATEGORIES,
  categoryLabel,
  PH_INTERP_LABELS,
  PH_INTERP_MODES,
} from '~/composables/useWeaponVocab'

const toasts = useToasts()

const templates = ref<EquipmentTemplate[]>([])
const selectedId = ref<string | null>(null) // null = 新增中
const name = ref('')
const category = ref('KINETIC')
const busy = ref(false)

// KINETIC 結構化欄位
const maxRange = ref(600)
const minRange = ref(0)
const indirectFire = ref(false)
const ratePerTick = ref(1)
const ammoTypes = ref('') // 逗號分隔
const phInterp = ref('linear') // 命中率插值法（#4）：linear | polynomial
const phBands = ref<{ range: number; ph: number }[]>([{ range: 300, ph: 0.5 }])
const dmgRows = ref<{ ac: string; dmg: number }[]>([{ ac: 'INFANTRY', dmg: 30 }])
// 非 KINETIC：raw JSON
const jsonText = ref('{}')

async function load() {
  templates.value = await fetchEquipmentTemplates().catch(() => [])
}
onMounted(load)

function resetForm() {
  selectedId.value = null
  name.value = ''
  category.value = 'KINETIC'
  maxRange.value = 600
  minRange.value = 0
  indirectFire.value = false
  ratePerTick.value = 1
  ammoTypes.value = 'AMMO_GENERIC'
  phInterp.value = 'linear'
  phBands.value = [{ range: 300, ph: 0.5 }]
  dmgRows.value = [{ ac: 'INFANTRY', dmg: 30 }]
  jsonText.value = '{}'
}

function pick(t: EquipmentTemplate) {
  selectedId.value = t.id
  name.value = t.name
  category.value = t.category
  const bs = (t.base_stats ?? {}) as Record<string, unknown>
  if (t.category === 'KINETIC') {
    maxRange.value = Number(bs.max_range_m ?? 0)
    minRange.value = Number(bs.min_range_m ?? 0)
    indirectFire.value = Boolean(bs.indirect_fire)
    ratePerTick.value = Number(bs.rate_per_tick ?? 1)
    phInterp.value = (bs.ph_interp as string) === 'polynomial' ? 'polynomial' : 'linear'
    ammoTypes.value = ((bs.ammo_types as string[]) ?? []).join(', ')
    phBands.value = ((bs.ph_by_range_band as [number, number][]) ?? []).map(([range, ph]) => ({
      range,
      ph,
    }))
    dmgRows.value = Object.entries((bs.damage_by_armor_class as Record<string, number>) ?? {}).map(
      ([ac, dmg]) => ({ ac, dmg }),
    )
  } else {
    jsonText.value = JSON.stringify(bs, null, 2)
  }
}

function buildBaseStats(): Record<string, unknown> {
  if (category.value === 'KINETIC') {
    return {
      max_range_m: maxRange.value,
      min_range_m: minRange.value,
      indirect_fire: indirectFire.value,
      rate_per_tick: ratePerTick.value,
      ammo_types: ammoTypes.value
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      ph_interp: phInterp.value,
      ph_by_range_band: phBands.value.map((b) => [b.range, b.ph]),
      damage_by_armor_class: Object.fromEntries(
        dmgRows.value
          .filter((r) => r.ac.trim() && r.ac !== '__custom__')
          .map((r) => [r.ac.trim(), r.dmg]),
      ),
    }
  }
  return JSON.parse(jsonText.value) as Record<string, unknown>
}

async function save() {
  if (!name.value.trim()) {
    toasts.push({ severity: 'warn', title: '請填範本名稱', timeoutMs: 3000 })
    return
  }
  let baseStats: Record<string, unknown>
  try {
    baseStats = buildBaseStats()
  } catch {
    toasts.push({ severity: 'error', title: 'JSON 格式錯誤', detail: '請檢查屬性 JSON', timeoutMs: 0 })
    return
  }
  busy.value = true
  const body = { name: name.value.trim(), category: category.value, base_stats: baseStats }
  try {
    const saved = selectedId.value
      ? await updateEquipmentTemplate(selectedId.value, body)
      : await createEquipmentTemplate(body)
    await load()
    pick(saved)
    toasts.push({ severity: 'success', title: `已儲存：${saved.name}`, timeoutMs: 3000 })
  } catch (e) {
    const err = e as { code?: string; message?: string }
    toasts.push({
      severity: 'error',
      title: `儲存失敗${err.code ? `（${err.code}）` : ''}`,
      detail: err.message ?? '請確認你有統裁/管理權限，且屬性符合規格',
      timeoutMs: 0,
    })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="armory">
    <header>
      <button class="back" data-testid="armory-back" @click="navigateTo('/lobby')">← 系統首頁</button>
      <h1>武器庫（裝備範本）</h1>
    </header>

    <div class="body">
      <aside class="list">
        <button class="new" data-testid="armory-new" @click="resetForm">＋ 新增範本</button>
        <ul>
          <li
            v-for="t in templates"
            :key="t.id"
            :class="{ sel: t.id === selectedId }"
            data-testid="armory-item"
            @click="pick(t)"
          >
            <span class="t-name">{{ t.name }}</span>
            <span class="t-cat">{{ t.category }}</span>
          </li>
          <li v-if="!templates.length" class="empty">（尚無範本）</li>
        </ul>
      </aside>

      <section class="editor" data-testid="armory-editor">
        <h2>{{ selectedId ? '編輯範本' : '新增範本' }}</h2>
        <div class="row">
          <label>名稱 <input v-model="name" data-testid="armory-name"></label>
          <label>類別
            <select v-model="category" data-testid="armory-category">
              <option v-for="c in CATEGORIES" :key="c" :value="c">{{ categoryLabel(c) }}</option>
            </select>
          </label>
        </div>

        <template v-if="category === 'KINETIC'">
          <div class="row">
            <label>最大射程 (m) <input v-model.number="maxRange" type="number" data-testid="armory-maxrange"></label>
            <label>最小射程 (m) <input v-model.number="minRange" type="number"></label>
            <label>射速/tick <input v-model.number="ratePerTick" type="number" step="0.1"></label>
            <label class="chk"><input v-model="indirectFire" type="checkbox"> 間接射擊</label>
          </div>
          <label class="wide">彈種（逗號分隔）
            <input v-model="ammoTypes" data-testid="armory-ammo" placeholder="AMMO_556, AMMO_AP">
          </label>

          <label class="wide">命中率插值法
            <select v-model="phInterp" data-testid="armory-ph-interp">
              <option v-for="m in PH_INTERP_MODES" :key="m" :value="m">{{ PH_INTERP_LABELS[m] }}</option>
            </select>
          </label>
          <div class="sub">
            命中率 Ph（依射程控制點：range_max_m → base_ph）——
            {{ phInterp === 'polynomial' ? '多項式（拉格朗日）曲線穿過全部控制點' : '控制點間線性插值' }}
          </div>
          <div v-for="(b, i) in phBands" :key="`ph${i}`" class="pair">
            <input v-model.number="b.range" type="number" placeholder="range m">
            <input v-model.number="b.ph" type="number" step="0.05" min="0" max="1" placeholder="Ph 0–1">
            <button class="rm" @click="phBands.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="phBands.push({ range: 0, ph: 0.5 })">＋ Ph 控制點</button>
          <p v-if="phInterp === 'polynomial' && phBands.length < 3" class="warn" data-testid="ph-poly-warn">
            多項式插值需 ≥3 個控制點才有意義（少於 3 點時等同線性）。
          </p>

          <div class="sub">傷害（依裝甲級別：armor_class → 扣血點）</div>
          <div v-for="(r, i) in dmgRows" :key="`dmg${i}`" class="pair">
            <select v-model="r.ac" class="ac-sel" data-testid="armory-armor-class">
              <option v-for="ac in ARMOR_CLASSES" :key="ac" :value="ac">
                {{ ARMOR_CLASS_LABELS[ac] }}（{{ ac }}）
              </option>
              <option v-if="r.ac && !ARMOR_CLASS_LABELS[r.ac]" :value="r.ac">{{ r.ac }}（自訂）</option>
              <option value="__custom__">＋ 自訂級別…</option>
            </select>
            <input
              v-if="r.ac === '__custom__' || (r.ac && !ARMOR_CLASS_LABELS[r.ac])"
              :value="r.ac === '__custom__' ? '' : r.ac"
              placeholder="自訂 armor_class"
              @input="r.ac = ($event.target as HTMLInputElement).value.toUpperCase()"
            >
            <input v-model.number="r.dmg" type="number" placeholder="damage">
            <button class="rm" @click="dmgRows.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="dmgRows.push({ ac: 'INFANTRY', dmg: 0 })">＋ 傷害級別</button>
        </template>

        <template v-else>
          <label class="wide">屬性 JSON（依 weaponeering.schema.json 之 {{ category.toLowerCase() }} 規格）
            <textarea v-model="jsonText" rows="10" data-testid="armory-json" spellcheck="false" />
          </label>
        </template>

        <div class="actions">
          <button class="save" data-testid="armory-save" :disabled="busy" @click="save">
            {{ selectedId ? '儲存變更' : '建立範本' }}
          </button>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.armory {
  max-width: 60rem;
  margin: 0 auto;
  padding: 1.5rem 1rem;
  color: #e2e8f0;
}
header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}
header h1 {
  font-size: 1.25rem;
  margin: 0;
}
.back {
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  padding: 0.25rem 0.6rem;
  cursor: pointer;
}
.body {
  display: flex;
  gap: 1.25rem;
  align-items: flex-start;
}
.list {
  width: 13rem;
  flex: none;
}
.list .new {
  width: 100%;
  margin-bottom: 0.5rem;
  padding: 0.4rem;
  border: 1px dashed #334155;
  border-radius: 0.3rem;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
}
.list ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.4rem 0.5rem;
  border: 1px solid #1e293b;
  border-radius: 0.3rem;
  cursor: pointer;
}
.list li.sel {
  border-color: #2563eb;
  background: #172554;
}
.list .t-cat {
  color: #64748b;
  font-size: 0.7rem;
}
.list .empty {
  color: #64748b;
  cursor: default;
}
.editor {
  flex: 1;
  border: 1px solid #1e293b;
  border-radius: 0.5rem;
  padding: 1rem;
}
.editor h2 {
  margin: 0 0 0.75rem;
  font-size: 1rem;
  color: #94a3b8;
}
.row {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 0.6rem;
}
label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.78rem;
  color: #94a3b8;
}
label.wide {
  margin-bottom: 0.6rem;
}
label.chk {
  flex-direction: row;
  align-items: center;
  gap: 0.35rem;
  align-self: flex-end;
  color: #cbd5e1;
}
input,
select,
textarea {
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.3rem 0.4rem;
  font-size: 0.8rem;
}
label.chk input {
  padding: 0;
}
textarea {
  width: 100%;
  font-family: ui-monospace, monospace;
  resize: vertical;
}
.sub {
  margin: 0.6rem 0 0.35rem;
  font-size: 0.74rem;
  color: #64748b;
}
.pair {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 0.3rem;
}
.pair input {
  flex: 1;
}
.pair .ac-sel {
  flex: 1;
  min-width: 8rem;
}
.warn {
  margin: 0.2rem 0 0.4rem;
  font-size: 0.72rem;
  color: #fbbf24;
}
.rm {
  border: none;
  background: transparent;
  color: #f87171;
  cursor: pointer;
}
.add {
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
  padding: 0.2rem 0.5rem;
  font-size: 0.74rem;
}
.actions {
  margin-top: 1rem;
}
.save {
  padding: 0.45rem 1rem;
  border: 0;
  border-radius: 0.3rem;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
}
.save:disabled {
  opacity: 0.5;
}
</style>
