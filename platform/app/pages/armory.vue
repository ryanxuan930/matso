<script setup lang="ts">
// 武器庫（裝備範本編輯）——編輯 EquipmentTemplate.base_stats（屬性/功能）。KINETIC/ARTILLERY/
// VEHICLE 走結構化表單（比照兵推）；SENSOR/COMMS/LOGISTICS/DRONE 走預填 JSON scaffold。
// 任何類別皆可「一鍵切換 表單/JSON」（保留擴充屬性：儲存時結構化欄位覆蓋於原 baseStats 之上）。
import {
  createEquipmentTemplate,
  fetchEquipmentTemplates,
  updateEquipmentTemplate,
  type EquipmentTemplate,
} from '~/composables/useEquipment'
import {
  ARMOR_CLASSES,
  ARMOR_CLASS_LABELS,
  ARTILLERY_KIND_LABELS,
  ARTILLERY_KINDS,
  CATEGORIES,
  categoryLabel,
  KINETIC_KIND_LABELS,
  KINETIC_KINDS,
  MOBILITY_CLASS_LABELS,
  MOBILITY_CLASSES,
  PH_INTERP_LABELS,
  PH_INTERP_MODES,
} from '~/composables/useWeaponVocab'

const toasts = useToasts()

const templates = ref<EquipmentTemplate[]>([])
const selectedId = ref<string | null>(null) // null = 新增中
const name = ref('')
const category = ref('KINETIC')
const busy = ref(false)
const editMode = ref<'form' | 'json'>('form') // 一鍵切換 表單/JSON
const originalBaseStats = ref<Record<string, unknown>>({}) // 儲存時保留未涵蓋的擴充鍵

// KINETIC / ARTILLERY 共用火力欄位
const maxRange = ref(600)
const minRange = ref(0)
const indirectFire = ref(false)
const ratePerTick = ref(1)
const ammoTypes = ref('') // 逗號分隔
const kineticKind = ref('GENERIC')
const phInterp = ref('linear') // 命中率插值法（#4）：linear | polynomial
const phBands = ref<{ range: number; ph: number }[]>([{ range: 300, ph: 0.5 }])
const dmgRows = ref<{ ac: string; dmg: number }[]>([{ ac: 'INFANTRY', dmg: 30 }])
const pkRows = ref<{ ac: string; pk: number }[]>([{ ac: 'INFANTRY', pk: 0.5 }]) // 每發擊殺率（真實化）
// ARTILLERY 專屬
const artilleryKind = ref('MORTAR')
const dispersionCep = ref(90)
const lethalRadius = ref(35)
const roundsPerMission = ref(6)
// VEHICLE 專屬
const crew = ref(3)
const passengerCap = ref(0)
const vehArmorClass = ref('ARMOR')
const armorFront = ref(200)
const armorSide = ref(80)
const armorRear = ref(40)
const armorTop = ref(20)
// 共用機動性（ARTILLERY/VEHICLE）
const canSelfMove = ref(true)
const mobilityClass = ref('TRACKED')
const roadSpeed = ref(60)
const ccSpeed = ref(35)
// 無結構化表單類別：JSON scaffold（先建立好預設欄位，比照一般兵推系統）
const jsonText = ref('{}')

const FORM_CATEGORIES = ['KINETIC', 'ARTILLERY', 'VEHICLE']
const hasForm = computed(() => FORM_CATEGORIES.includes(category.value))
const isKineticLike = computed(() => category.value === 'KINETIC' || category.value === 'ARTILLERY')

// 非表單類別的預設欄位 scaffold（比照一般兵推系統的常見諸元）。
const SCAFFOLDS: Record<string, Record<string, unknown>> = {
  SENSOR: {
    sensor_kind: 'OPTICAL',
    max_range_m: 5000,
    detect_curve: [
      [2000, 0.9],
      [5000, 0.5],
    ],
  },
  COMMS: {
    band: 'VHF',
    tx_power_dbm: 37,
    rx_sensitivity_dbm: -100,
    antenna_gain_dbi: 2,
    mesh_capable: false,
  },
  LOGISTICS: {
    capacity: { AMMO: 100, FUEL: 200, WATER_FOOD: 50, BATTERY: 20 },
    transport: { troop_capacity: 0, vehicle_slots: 0, can_tow: false, load_unload_ticks: 1 },
  },
  DRONE: {
    endurance_ticks: 120,
    cruise_speed_ms: 25,
    weather_limits: { max_wind_ms: 12, min_visibility_m: 1000 },
  },
}

async function load() {
  templates.value = await fetchEquipmentTemplates().catch(() => [])
}
onMounted(load)

function resetForm() {
  selectedId.value = null
  name.value = ''
  category.value = 'KINETIC'
  originalBaseStats.value = {}
  editMode.value = 'form'
  maxRange.value = 600
  minRange.value = 0
  indirectFire.value = false
  ratePerTick.value = 1
  ammoTypes.value = 'AMMO_GENERIC'
  kineticKind.value = 'GENERIC'
  phInterp.value = 'linear'
  phBands.value = [{ range: 300, ph: 0.5 }]
  dmgRows.value = [{ ac: 'INFANTRY', dmg: 30 }]
  pkRows.value = [{ ac: 'INFANTRY', pk: 0.5 }]
  artilleryKind.value = 'MORTAR'
  dispersionCep.value = 90
  lethalRadius.value = 35
  roundsPerMission.value = 6
  crew.value = 3
  passengerCap.value = 0
  vehArmorClass.value = 'ARMOR'
  armorFront.value = 200
  armorSide.value = 80
  armorRear.value = 40
  armorTop.value = 20
  canSelfMove.value = true
  mobilityClass.value = 'TRACKED'
  roadSpeed.value = 60
  ccSpeed.value = 35
  jsonText.value = '{}'
}

// 使用者改類別（新範本）：套用該類別的預設 scaffold / 表單預設，切到合適模式。
function onCategoryChange() {
  originalBaseStats.value = {}
  if (hasForm.value) {
    editMode.value = 'form'
  } else {
    editMode.value = 'json'
    jsonText.value = JSON.stringify(SCAFFOLDS[category.value] ?? {}, null, 2)
  }
}

function populateForm(bs: Record<string, unknown>): void {
  if (isKineticLike.value) {
    maxRange.value = Number(bs.max_range_m ?? 600)
    minRange.value = Number(bs.min_range_m ?? 0)
    indirectFire.value = Boolean(bs.indirect_fire ?? category.value === 'ARTILLERY')
    ratePerTick.value = Number(bs.rate_per_tick ?? 1)
    kineticKind.value = String(bs.kinetic_kind ?? 'GENERIC')
    phInterp.value = (bs.ph_interp as string) === 'polynomial' ? 'polynomial' : 'linear'
    ammoTypes.value = ((bs.ammo_types as string[]) ?? []).join(', ')
    phBands.value = ((bs.ph_by_range_band as [number, number][]) ?? []).map(([range, ph]) => ({
      range,
      ph,
    }))
    dmgRows.value = Object.entries((bs.damage_by_armor_class as Record<string, number>) ?? {}).map(
      ([ac, dmg]) => ({ ac, dmg }),
    )
    pkRows.value = Object.entries((bs.pk_by_armor_class as Record<string, number>) ?? {}).map(
      ([ac, pk]) => ({ ac, pk }),
    )
    if (category.value === 'ARTILLERY') {
      artilleryKind.value = String(bs.artillery_kind ?? 'MORTAR')
      dispersionCep.value = Number(bs.dispersion_cep_m ?? 90)
      lethalRadius.value = Number(bs.lethal_radius_m ?? 35)
      roundsPerMission.value = Number(bs.rounds_per_mission ?? 6)
      readMobility((bs.mobility as Record<string, unknown>) ?? {})
    }
  } else if (category.value === 'VEHICLE') {
    crew.value = Number(bs.crew ?? 3)
    passengerCap.value = Number(bs.passenger_capacity ?? 0)
    vehArmorClass.value = String(bs.armor_class ?? 'ARMOR')
    const a = (bs.armor_by_aspect_mm as Record<string, number>) ?? {}
    armorFront.value = Number(a.front ?? 200)
    armorSide.value = Number(a.side ?? 80)
    armorRear.value = Number(a.rear ?? 40)
    armorTop.value = Number(a.top ?? 20)
    readMobility((bs.mobility as Record<string, unknown>) ?? {})
  }
}

function readMobility(m: Record<string, unknown>): void {
  canSelfMove.value = m.can_self_move !== false
  mobilityClass.value = String(m.mobility_class ?? 'TRACKED')
  roadSpeed.value = Number(m.max_road_speed_kmh ?? 60)
  ccSpeed.value = Number(m.max_cross_country_speed_kmh ?? 35)
}

function mobilityStats(): Record<string, unknown> {
  return {
    can_self_move: canSelfMove.value,
    mobility_class: mobilityClass.value,
    max_road_speed_kmh: roadSpeed.value,
    max_cross_country_speed_kmh: ccSpeed.value,
  }
}

function pick(t: EquipmentTemplate) {
  selectedId.value = t.id
  name.value = t.name
  category.value = t.category
  const bs = (t.base_stats ?? {}) as Record<string, unknown>
  originalBaseStats.value = bs
  if (hasForm.value) {
    populateForm(bs)
    editMode.value = 'form'
  } else {
    jsonText.value = JSON.stringify(bs, null, 2)
    editMode.value = 'json'
  }
}

// 結構化欄位 → baseStats（表單模式），覆蓋於原 baseStats 之上（保留擴充鍵）。
function formToBaseStats(): Record<string, unknown> {
  const kvNum = (rows: { ac: string; v: number }[]) =>
    Object.fromEntries(rows.filter((r) => r.ac.trim() && r.ac !== '__custom__').map((r) => [r.ac.trim(), r.v]))
  if (isKineticLike.value) {
    const base: Record<string, unknown> = {
      max_range_m: maxRange.value,
      min_range_m: minRange.value,
      indirect_fire: indirectFire.value,
      rate_per_tick: ratePerTick.value,
      kinetic_kind: kineticKind.value,
      ammo_types: ammoTypes.value.split(',').map((s) => s.trim()).filter(Boolean),
      ph_interp: phInterp.value,
      ph_by_range_band: phBands.value.map((b) => [b.range, b.ph]),
      damage_by_armor_class: kvNum(dmgRows.value.map((r) => ({ ac: r.ac, v: r.dmg }))),
      pk_by_armor_class: kvNum(pkRows.value.map((r) => ({ ac: r.ac, v: r.pk }))),
    }
    if (category.value === 'ARTILLERY') {
      base.artillery_kind = artilleryKind.value
      base.dispersion_cep_m = dispersionCep.value
      base.lethal_radius_m = lethalRadius.value
      base.rounds_per_mission = roundsPerMission.value
      base.mobility = mobilityStats()
    }
    return base
  }
  // VEHICLE
  return {
    crew: crew.value,
    passenger_capacity: passengerCap.value,
    armor_class: vehArmorClass.value,
    armor_by_aspect_mm: {
      front: armorFront.value,
      side: armorSide.value,
      rear: armorRear.value,
      top: armorTop.value,
    },
    mobility: mobilityStats(),
  }
}

function buildBaseStats(): Record<string, unknown> {
  if (editMode.value === 'json' || !hasForm.value) {
    return JSON.parse(jsonText.value) as Record<string, unknown>
  }
  // 表單模式：結構化欄位覆蓋於原 baseStats 之上，保留未涵蓋的自訂/擴充鍵。
  return { ...originalBaseStats.value, ...formToBaseStats() }
}

// 一鍵切換 表單↔JSON，往返同步（form→json 序列化目前表單；json→form 盡力解析回填）。
function toggleMode() {
  if (editMode.value === 'form') {
    jsonText.value = JSON.stringify(buildBaseStats(), null, 2)
    editMode.value = 'json'
  } else {
    try {
      const parsed = JSON.parse(jsonText.value) as Record<string, unknown>
      originalBaseStats.value = parsed
      if (hasForm.value) populateForm(parsed)
      editMode.value = 'form'
    } catch {
      toasts.push({ severity: 'error', title: 'JSON 格式錯誤', detail: '無法切回表單', timeoutMs: 4000 })
    }
  }
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
      <h1>武器庫</h1>
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
            <select v-model="category" data-testid="armory-category" @change="onCategoryChange">
              <option v-for="c in CATEGORIES" :key="c" :value="c">{{ categoryLabel(c) }}</option>
            </select>
          </label>
          <button
            v-if="hasForm"
            class="mode-toggle"
            data-testid="armory-mode-toggle"
            :title="editMode === 'form' ? '切換為 JSON 編輯' : '切換回表單編輯'"
            @click="toggleMode"
          >
            {{ editMode === 'form' ? '⤳ JSON' : '⤳ 表單' }}
          </button>
        </div>

        <template v-if="isKineticLike && editMode === 'form'">
          <div class="row">
            <label>最大射程 (m) <input v-model.number="maxRange" type="number" data-testid="armory-maxrange"></label>
            <label>最小射程 (m) <input v-model.number="minRange" type="number"></label>
            <label>射速/tick <input v-model.number="ratePerTick" type="number" step="0.1"></label>
            <label class="chk"><input v-model="indirectFire" type="checkbox"> 間接射擊</label>
          </div>
          <div class="row">
            <label>{{ category === 'ARTILLERY' ? '火砲類型' : '武器細分' }}
              <select v-if="category === 'ARTILLERY'" v-model="artilleryKind" data-testid="armory-artillery-kind">
                <option v-for="k in ARTILLERY_KINDS" :key="k" :value="k">{{ ARTILLERY_KIND_LABELS[k] }}</option>
              </select>
              <select v-else v-model="kineticKind" data-testid="armory-kinetic-kind">
                <option v-for="k in KINETIC_KINDS" :key="k" :value="k">{{ KINETIC_KIND_LABELS[k] }}</option>
              </select>
            </label>
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

          <div class="sub">每發擊殺率 pk（真實化交戰主要傷亡驅動：armor_class → P(kill|hit) 0–1）</div>
          <div v-for="(r, i) in pkRows" :key="`pk${i}`" class="pair">
            <select v-model="r.ac" class="ac-sel">
              <option v-for="ac in ARMOR_CLASSES" :key="ac" :value="ac">{{ ARMOR_CLASS_LABELS[ac] }}（{{ ac }}）</option>
              <option v-if="r.ac && !ARMOR_CLASS_LABELS[r.ac]" :value="r.ac">{{ r.ac }}（自訂）</option>
            </select>
            <input v-model.number="r.pk" type="number" step="0.05" min="0" max="1" placeholder="pk 0–1">
            <button class="rm" @click="pkRows.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="pkRows.push({ ac: 'INFANTRY', pk: 0.5 })">＋ 擊殺率</button>

          <template v-if="category === 'ARTILLERY'">
            <div class="sub">火砲諸元（間瞄）</div>
            <div class="row">
              <label>散布 CEP (m) <input v-model.number="dispersionCep" type="number"></label>
              <label>致命半徑 (m) <input v-model.number="lethalRadius" type="number"></label>
              <label>每次射擊發數 <input v-model.number="roundsPerMission" type="number"></label>
            </div>
          </template>
          <div v-if="category === 'ARTILLERY'" class="mobility-block">
            <div class="sub">機動性（自走砲填道路/越野速度；牽引砲取消自走）</div>
            <div class="row">
              <label class="chk"><input v-model="canSelfMove" type="checkbox"> 可自走</label>
              <label>機動類型
                <select v-model="mobilityClass">
                  <option v-for="m in MOBILITY_CLASSES" :key="m" :value="m">{{ MOBILITY_CLASS_LABELS[m] }}</option>
                </select>
              </label>
              <label>道路速度 (km/h) <input v-model.number="roadSpeed" type="number" :disabled="!canSelfMove"></label>
              <label>越野速度 (km/h) <input v-model.number="ccSpeed" type="number" :disabled="!canSelfMove"></label>
            </div>
          </div>
        </template>

        <template v-else-if="category === 'VEHICLE' && editMode === 'form'">
          <div class="row">
            <label>組員 <input v-model.number="crew" type="number"></label>
            <label>載員數 <input v-model.number="passengerCap" type="number"></label>
            <label>防禦裝甲級別
              <select v-model="vehArmorClass" data-testid="armory-veh-armor-class">
                <option v-for="ac in ARMOR_CLASSES" :key="ac" :value="ac">{{ ARMOR_CLASS_LABELS[ac] }}</option>
              </select>
            </label>
          </div>
          <div class="sub">各面裝甲厚度（RHA 等效 mm）</div>
          <div class="row">
            <label>正面 <input v-model.number="armorFront" type="number"></label>
            <label>側面 <input v-model.number="armorSide" type="number"></label>
            <label>後面 <input v-model.number="armorRear" type="number"></label>
            <label>頂部 <input v-model.number="armorTop" type="number"></label>
          </div>
          <div class="mobility-block">
            <div class="sub">機動性</div>
            <div class="row">
              <label class="chk"><input v-model="canSelfMove" type="checkbox"> 可自走</label>
              <label>機動類型
                <select v-model="mobilityClass">
                  <option v-for="m in MOBILITY_CLASSES" :key="m" :value="m">{{ MOBILITY_CLASS_LABELS[m] }}</option>
                </select>
              </label>
              <label>道路速度 (km/h) <input v-model.number="roadSpeed" type="number" :disabled="!canSelfMove"></label>
              <label>越野速度 (km/h) <input v-model.number="ccSpeed" type="number" :disabled="!canSelfMove"></label>
            </div>
          </div>
        </template>

        <template v-else>
          <label class="wide">
            屬性 JSON（依 weaponeering.schema.json 之 {{ category.toLowerCase() }} 規格；已預填常見欄位）
            <textarea v-model="jsonText" rows="12" data-testid="armory-json" spellcheck="false" />
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
.mode-toggle {
  align-self: flex-end;
  margin-left: auto;
  padding: 0.3rem 0.6rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
  font-size: 0.75rem;
}
.mode-toggle:hover {
  border-color: #2563eb;
}
.mobility-block {
  margin-top: 0.4rem;
  padding: 0.5rem;
  border: 1px dashed #334155;
  border-radius: 0.35rem;
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
