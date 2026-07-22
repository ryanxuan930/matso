<script setup lang="ts">
// 想定編輯器（O7.3，SPEC §11.2）——factions/relations/units/victory 編輯 + 匯出/匯入 roundtrip。
import {
  emptyScenario,
  exportScenario,
  importScenario,
  type RelationValue,
  type ScenarioModel,
  type UnitLevel,
} from '~/composables/useScenarioEditor'

const LEVELS: UnitLevel[] = [
  'THEATER', 'CORPS', 'DIVISION', 'BRIGADE', 'BATTALION',
  'COMPANY', 'PLATOON', 'SQUAD', 'FIRETEAM', 'INDIVIDUAL',
]
const RELATIONS: RelationValue[] = ['ALLIED', 'NEUTRAL', 'HOSTILE']

const model = ref<ScenarioModel>(emptyScenario())
const importText = ref('')
const importError = ref('')
const exportText = computed(() => JSON.stringify(exportScenario(model.value), null, 2))

function addFaction() {
  model.value.factions.push({ id: `F${model.value.factions.length + 1}`, color: '#888888' })
}
function addUnit() {
  const f = model.value.factions[0]?.id ?? 'BLUE'
  model.value.units.push({ faction: f, designation: 'U', unitLevel: 'PLATOON' })
}
function addRelation() {
  const ids = model.value.factions
  model.value.relations.push({ a: ids[0]?.id ?? '', b: ids[1]?.id ?? '', relation: 'NEUTRAL' })
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
</script>

<template>
  <div class="editor" data-testid="scenario-editor">
    <h1>想定編輯器</h1>

    <section class="meta">
      <label>名稱 <input v-model="model.name" data-testid="sc-name"></label>
      <label>版本 <input v-model="model.version"></label>
      <label>模式
        <select v-model="model.mode">
          <option>REALTIME</option><option>WEGO</option><option>IGO_UGO</option>
        </select>
      </label>
    </section>

    <section>
      <h2>陣營 <button data-testid="add-faction" @click="addFaction">＋</button></h2>
      <div v-for="(f, i) in model.factions" :key="i" class="row" data-testid="faction-row">
        <input v-model="f.id" placeholder="ID">
        <input v-model="f.color" type="color">
        <button @click="remove(model.factions, i)">✕</button>
      </div>
    </section>

    <section>
      <h2>關係 <button data-testid="add-relation" @click="addRelation">＋</button></h2>
      <div v-for="(r, i) in model.relations" :key="i" class="row" data-testid="relation-row">
        <input v-model="r.a"><input v-model="r.b">
        <select v-model="r.relation"><option v-for="rel in RELATIONS" :key="rel">{{ rel }}</option></select>
        <button @click="remove(model.relations, i)">✕</button>
      </div>
      <p class="hint">未宣告配對預設 HOSTILE（§12.1）。</p>
    </section>

    <section>
      <h2>單位（ORBAT） <button data-testid="add-unit" @click="addUnit">＋</button></h2>
      <div v-for="(u, i) in model.units" :key="i" class="row" data-testid="unit-row">
        <select v-model="u.faction"><option v-for="f in model.factions" :key="f.id">{{ f.id }}</option></select>
        <input v-model="u.designation" placeholder="番號">
        <select v-model="u.unitLevel"><option v-for="l in LEVELS" :key="l">{{ l }}</option></select>
        <input v-model="u.parent" placeholder="上級">
        <button @click="remove(model.units, i)">✕</button>
      </div>
    </section>

    <section class="io">
      <div>
        <h2>匯出</h2>
        <textarea :value="exportText" readonly rows="8" data-testid="export-text" />
      </div>
      <div>
        <h2>匯入</h2>
        <textarea v-model="importText" rows="8" placeholder="貼上匯出的 JSON" data-testid="import-text" />
        <button data-testid="do-import" @click="doImport">載入</button>
        <p v-if="importError" class="err" data-testid="import-error">{{ importError }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.editor { max-width: 900px; margin: 0 auto; padding: 1rem; color: #e2e8f0; }
section { margin: 1rem 0; border-top: 1px solid #1e293b; padding-top: 0.75rem; }
h2 { font-size: 0.9375rem; color: #94a3b8; }
.row { display: flex; gap: 0.4rem; margin: 0.25rem 0; align-items: center; }
.meta { display: flex; gap: 1rem; flex-wrap: wrap; }
.io { display: flex; gap: 1rem; }
.io > div { flex: 1; }
input, textarea {
  background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.25rem; padding: 0.375rem 0.5rem;
}
textarea { width: 100%; font-family: monospace; font-size: 0.75rem; }
button { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.25rem; padding: 0.375rem 0.75rem; cursor: pointer; }
.hint { color: #94a3b8; font-size: 0.8rem; }
</style>
