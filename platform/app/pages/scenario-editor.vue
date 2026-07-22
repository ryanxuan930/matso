<script setup lang="ts">
// 想定編輯器（O7.3，SPEC §11.2）——factions/relations/units/victory 編輯 + 匯出/匯入 roundtrip。
// UI 以 PrimeVue（Aura 主題）+ zh-TW 標籤重製；關係改為對稱矩陣，單位可編輯初始經緯度（#5.5）。
import { apiFetch } from '~/composables/useApi'
import type { ApiError } from '~/composables/useApi'
import {
  emptyScenario,
  exportScenario,
  importScenario,
  type EditorUnit,
  type RelationValue,
  type ScenarioModel,
  type UnitLevel,
} from '~/composables/useScenarioEditor'
import { emptyCondition } from '~/composables/useConditionDsl'

const LEVELS: UnitLevel[] = [
  'THEATER', 'CORPS', 'DIVISION', 'BRIGADE', 'BATTALION',
  'COMPANY', 'PLATOON', 'SQUAD', 'FIRETEAM', 'INDIVIDUAL',
]
const RELATIONS: RelationValue[] = ['ALLIED', 'NEUTRAL', 'HOSTILE']

// zh-TW 對照表 -------------------------------------------------------------
const MODE_OPTIONS = [
  { label: '即時', value: 'REALTIME' },
  { label: '同步回合', value: 'WEGO' },
  { label: '輪流回合', value: 'IGO_UGO' },
]
const LEVEL_LABELS: Record<UnitLevel, string> = {
  INDIVIDUAL: '兵', FIRETEAM: '伍', SQUAD: '班', PLATOON: '排', COMPANY: '連',
  BATTALION: '營', BRIGADE: '旅', DIVISION: '師', CORPS: '軍', THEATER: '戰區',
}
const LEVEL_OPTIONS = LEVELS.map((l) => ({ label: LEVEL_LABELS[l], value: l }))
const RELATION_LABELS: Record<RelationValue, string> = {
  ALLIED: '同盟', NEUTRAL: '中立', HOSTILE: '敵對',
}

const model = ref<ScenarioModel>(emptyScenario())
const importText = ref('')
const importError = ref('')
const exportText = computed(() => JSON.stringify(exportScenario(model.value), null, 2))

// 從既有想定載入（#5）——URL ?load=<id> → GET /scenarios/{id} → importScenario。
const loadError = ref('')
async function loadExisting(id: string) {
  loadError.value = ''
  try {
    const bundle = await apiFetch<Parameters<typeof importScenario>[0]>(`/scenarios/${encodeURIComponent(id)}`)
    model.value = importScenario(bundle)
  } catch (e) {
    const err = e as ApiError
    loadError.value = `載入想定失敗：${err.code === 'SCENARIO_NOT_FOUND' ? '找不到該想定' : (err.code ?? 'UNKNOWN')}`
  }
}
onMounted(() => {
  const q = useRoute().query.load
  const id = Array.isArray(q) ? q[0] : q
  if (id) loadExisting(String(id))
})

function addFaction() {
  model.value.factions.push({ id: `F${model.value.factions.length + 1}`, color: '#888888' })
}
function addUnit() {
  const f = model.value.factions[0]?.id ?? 'BLUE'
  model.value.units.push({ faction: f, designation: 'U', unitLevel: 'PLATOON' })
}
// MSEL 事件（GOAL#7）——陣營清單供 trigger/inject 的下拉；空預設 BLUE。
const factionIds = computed(() => model.value.factions.map((f) => f.id))
function addMsel() {
  const f = factionIds.value[0] ?? 'BLUE'
  model.value.msel.push({
    id: `E${model.value.msel.length + 1}`,
    once: true,
    trigger: emptyCondition('time', f),
    inject: { event_type: 'INTEL_REPORT', payload: {}, faction: undefined },
  })
}
function remove<T>(arr: T[], i: number) {
  arr.splice(i, 1)
}
function doImport() {
  try {
    model.value = importScenario(JSON.parse(importText.value))
    importError.value = ''
  } catch (e) {
    importError.value = `匯入失敗：${(e as Error).message}`
  }
}

// 關係矩陣（對稱）---------------------------------------------------------
// model.relations 是唯一結構（匯出為三元組 [a,b,relation]）；矩陣僅是其視圖，讀寫皆順序無關。
function relationOf(a: string, b: string): RelationValue {
  const r = model.value.relations.find(
    (x) => (x.a === a && x.b === b) || (x.a === b && x.b === a),
  )
  return r?.relation ?? 'HOSTILE' // 未宣告配對預設敵對（§12.1）
}
function cycleRelation(a: string, b: string) {
  const cur = relationOf(a, b)
  const next = RELATIONS[(RELATIONS.indexOf(cur) + 1) % RELATIONS.length]!
  const existing = model.value.relations.find(
    (x) => (x.a === a && x.b === b) || (x.a === b && x.b === a),
  )
  if (existing) existing.relation = next
  else model.value.relations.push({ a, b, relation: next })
}
function relSeverity(r: RelationValue): string {
  return r === 'ALLIED' ? 'success' : r === 'HOSTILE' ? 'danger' : 'secondary'
}

// 單位（ORBAT）-----------------------------------------------------------
function parentOptions(u: EditorUnit) {
  return [
    { label: '（無）', value: '' },
    ...model.value.units
      .filter((x) => x.faction === u.faction && x !== u)
      .map((x) => ({ label: x.designation, value: x.designation })),
  ]
}
function numStr(n?: number): string {
  return n === undefined ? '' : String(n)
}
// 空 → undefined（絕不寫入 ''/NaN/null），使 exportScenario 的 `u.lat !== undefined` 守則能省略空值。
function setNum(u: EditorUnit, key: 'lat' | 'lng', v: string | undefined) {
  const t = (v ?? '').trim()
  if (t === '') { u[key] = undefined; return }
  const n = Number(t)
  u[key] = Number.isFinite(n) ? n : undefined
}

// 地圖點選初始位置（#8）——以單位物件參照記錄哪些列展開了地圖（重排/刪除安全）。
const openPickers = ref(new Set<EditorUnit>())
function togglePicker(u: EditorUnit) {
  if (openPickers.value.has(u)) openPickers.value.delete(u)
  else openPickers.value.add(u)
}
// 單位經緯 ↔ MapPointPicker 的 {lng,lat}|null 轉接（保持 lat/lng 數值形狀不變）。
function unitPoint(u: EditorUnit): { lng: number; lat: number } | null {
  return u.lat !== undefined && u.lng !== undefined ? { lng: u.lng, lat: u.lat } : null
}
function setUnitPoint(u: EditorUnit, p: { lng: number; lat: number }) {
  u.lat = p.lat
  u.lng = p.lng
}

const saveStatus = ref('')
const saving = ref(false)
const saveSeverity = computed(() => (saveStatus.value.startsWith('已存') ? 'success' : 'error'))
async function saveToServer() {
  saving.value = true
  saveStatus.value = ''
  try {
    const bundle = exportScenario(model.value)
    const r = await apiFetch<{ id: string; name: string; version: string }>('/scenarios', {
      method: 'POST',
      body: bundle,
    })
    saveStatus.value = `已存到伺服器：${r.name} v${r.version}`
  } catch (e) {
    const err = e as ApiError
    saveStatus.value = `存檔失敗：${err.code === 'SCENARIO_INVALID' ? err.message : err.code}`
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="editor" data-testid="scenario-editor">
    <header class="sc-bar">
      <Button data-testid="sc-back-lobby" size="small" text @click="navigateTo('/lobby')">← 系統首頁</Button>
      <h1>劇本編輯器</h1>
      <Button data-testid="sc-save" class="sc-save-btn" size="small" :disabled="saving" @click="saveToServer">
        {{ saving ? '存檔中…' : '存到伺服器' }}
      </Button>
    </header>
    <Message v-if="saveStatus" :severity="saveSeverity" size="small" class="sc-status" data-testid="sc-save-status">
      {{ saveStatus }}
    </Message>
    <Message v-if="loadError" severity="error" size="small" class="sc-status" data-testid="sc-load-error">
      {{ loadError }}
    </Message>

    <section class="meta">
      <label>名稱 <InputText v-model="model.name" size="small" data-testid="sc-name" /></label>
      <label>版本 <InputText v-model="model.version" size="small" /></label>
      <label>模式
        <Select
          v-model="model.mode"
          :options="MODE_OPTIONS"
          option-label="label"
          option-value="value"
          size="small"
        />
      </label>
    </section>

    <section>
      <h2>陣營 <Button data-testid="add-faction" size="small" text @click="addFaction">＋</Button></h2>
      <div v-for="(f, i) in model.factions" :key="i" class="row" data-testid="faction-row">
        <InputText v-model="f.id" size="small" placeholder="ID" />
        <input v-model="f.color" type="color" class="color-input">
        <Button size="small" text severity="danger" @click="remove(model.factions, i)">✕</Button>
      </div>
    </section>

    <section>
      <h2>關係</h2>
      <table class="rel-matrix" data-testid="relations-matrix">
        <thead>
          <tr>
            <th />
            <th v-for="(c, ci) in model.factions" :key="ci">{{ c.id }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(a, ai) in model.factions" :key="ai">
            <th>{{ a.id }}</th>
            <td v-for="(b, bi) in model.factions" :key="bi">
              <span v-if="ai === bi" class="rel-diag">—</span>
              <Button
                v-else
                size="small"
                text
                :severity="relSeverity(relationOf(a.id, b.id))"
                @click="cycleRelation(a.id, b.id)"
              >
                {{ RELATION_LABELS[relationOf(a.id, b.id)] }}
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint">點格切換：同盟 → 中立 → 敵對（對稱寫入）。未宣告配對預設敵對（§12.1）。</p>
    </section>

    <section>
      <h2>單位（ORBAT） <Button data-testid="add-unit" size="small" text @click="addUnit">＋</Button></h2>
      <div v-for="(u, i) in model.units" :key="i" class="unit-block" data-testid="unit-block">
        <div class="row" data-testid="unit-row">
          <Select v-model="u.faction" :options="model.factions" option-label="id" option-value="id" size="small" />
          <InputText v-model="u.designation" size="small" placeholder="番號" />
          <Select
            v-model="u.unitLevel"
            :options="LEVEL_OPTIONS"
            option-label="label"
            option-value="value"
            size="small"
          />
          <Select
            v-model="u.parent"
            :options="parentOptions(u)"
            option-label="label"
            option-value="value"
            size="small"
            placeholder="上級"
          />
          <InputText
            :model-value="numStr(u.lat)"
            size="small"
            placeholder="緯度"
            class="coord"
            @update:model-value="(v: string | undefined) => setNum(u, 'lat', v)"
          />
          <InputText
            :model-value="numStr(u.lng)"
            size="small"
            placeholder="經度"
            class="coord"
            @update:model-value="(v: string | undefined) => setNum(u, 'lng', v)"
          />
          <Button
            size="small"
            text
            :severity="openPickers.has(u) ? 'primary' : 'secondary'"
            data-testid="unit-pick-toggle"
            @click="togglePicker(u)"
          >
            📍 地圖選取
          </Button>
          <Button size="small" text severity="danger" @click="remove(model.units, i)">✕</Button>
        </div>
        <ClientOnly v-if="openPickers.has(u)">
          <MapPointPicker
            class="unit-picker"
            :model-value="unitPoint(u)"
            @update:model-value="(p: { lng: number; lat: number }) => setUnitPoint(u, p)"
          />
        </ClientOnly>
      </div>
    </section>

    <section data-testid="msel-section">
      <h2>MSEL 事件 <Button data-testid="add-msel" size="small" text @click="addMsel">＋</Button></h2>
      <p class="hint">觸發條件成立時注入事件；「一次」勾選為邊緣觸發（僅觸一次），取消則每個成立的 tick 都觸。</p>
      <div v-for="(m, i) in model.msel" :key="i" class="msel-row" data-testid="msel-row">
        <div class="msel-head">
          <InputText v-model="m.id" size="small" placeholder="ID" class="msel-id" />
          <label class="msel-once">
            <Checkbox v-model="m.once" binary />一次
          </label>
          <Button size="small" text severity="danger" @click="remove(model.msel, i)">✕</Button>
        </div>
        <div class="msel-block">
          <span class="msel-label">觸發</span>
          <ConditionBuilder v-model="m.trigger" :factions="factionIds" />
        </div>
        <div class="msel-block">
          <span class="msel-label">注入</span>
          <InjectActionForm v-model="m.inject" :factions="factionIds" />
        </div>
      </div>
    </section>

    <section class="io">
      <div>
        <h2>匯出</h2>
        <Textarea :model-value="exportText" readonly rows="8" data-testid="export-text" class="mono" />
      </div>
      <div>
        <h2>匯入</h2>
        <Textarea v-model="importText" rows="8" placeholder="貼上匯出的 JSON" data-testid="import-text" class="mono" />
        <Button data-testid="do-import" size="small" @click="doImport">載入</Button>
        <Message v-if="importError" severity="error" size="small" data-testid="import-error">{{ importError }}</Message>
      </div>
    </section>
  </div>
</template>

<style scoped>
.editor { max-width: 900px; margin: 0 auto; padding: 1rem; color: #e2e8f0; }
.sc-bar { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.5rem; }
.sc-bar h1 { font-size: 1.25rem; margin: 0; }
.sc-save-btn { margin-left: auto; }
.sc-status { margin: 0 0 0.75rem; }
section { margin: 1rem 0; border-top: 1px solid #1e293b; padding-top: 0.75rem; }
h2 { font-size: 0.9375rem; color: #94a3b8; display: flex; align-items: center; gap: 0.5rem; }
.row { display: flex; gap: 0.4rem; margin: 0.25rem 0; align-items: center; flex-wrap: wrap; }
.unit-block { margin: 0.25rem 0; }
.unit-picker { margin: 0.25rem 0 0.5rem; max-width: 32rem; }
.meta { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
.meta label { display: inline-flex; align-items: center; gap: 0.4rem; }
.io { display: flex; gap: 1rem; }
.io > div { flex: 1; }
.mono { width: 100%; font-family: monospace; font-size: 0.75rem; }
.coord { width: 6rem; }
.color-input {
  width: 2.25rem; height: 2rem; padding: 0; border: 1px solid #334155;
  border-radius: 0.25rem; background: #0f172a; cursor: pointer;
}
.hint { color: #94a3b8; font-size: 0.8rem; }
.rel-matrix { border-collapse: collapse; margin: 0.25rem 0; }
.rel-matrix th, .rel-matrix td { border: 1px solid #1e293b; padding: 0.1rem 0.25rem; text-align: center; min-width: 4.75rem; }
.rel-matrix th { color: #94a3b8; font-weight: 600; font-size: 0.8rem; }
.rel-diag { color: #475569; }
.msel-row { border: 1px solid #1e293b; border-radius: 0.35rem; padding: 0.5rem; margin: 0.4rem 0; }
.msel-head { display: flex; gap: 0.5rem; align-items: center; }
.msel-id { width: 6rem; }
.msel-once { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.8125rem; color: #94a3b8; }
.msel-block { display: flex; gap: 0.4rem; align-items: baseline; margin-top: 0.4rem; flex-wrap: wrap; }
.msel-label { font-size: 0.8125rem; color: #64748b; min-width: 2.5rem; }
</style>
