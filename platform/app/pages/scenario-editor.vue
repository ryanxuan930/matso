<script setup lang="ts">
// 想定編輯器（O7.3，SPEC §11.2）——factions/relations/units/victory 編輯 + 匯出/匯入 roundtrip。
// UI 以 PrimeVue（Aura 主題）+ zh-TW 標籤重製；關係改為對稱矩陣，單位可編輯初始經緯度（#5.5）。
import { apiFetch } from '~/composables/useApi'
import type { ApiError } from '~/composables/useApi'
import {
  DAY_NIGHT_DEFAULTS,
  REQUEST_QUOTA_KINDS,
  SURVIVABILITY_DEFAULTS,
  emptyScenario,
  exportScenario,
  importScenario,
  type EditorEquipment,
  type EditorRequestQuotas,
  type EditorUnit,
  type RelationValue,
  type RequestQuotaKind,
  type ScenarioModel,
  type UnitLevel,
} from '~/composables/useScenarioEditor'
import { emptyCondition } from '~/composables/useConditionDsl'
import { REQUEST_KIND_LABELS } from '~/composables/useC2'
import { BRANCH_LABELS, BRANCH_OPTIONS } from '~/composables/useUnits'
import { fetchEquipmentTemplates, type EquipmentTemplate } from '~/composables/useEquipment'

// 由大到小（與後端 UnitLevel 的宣告順序一致——那個順序就是編制大小）。
const LEVELS: UnitLevel[] = [
  'THEATER', 'ARMY_GROUP', 'ARMY', 'CORPS', 'DIVISION', 'BRIGADE', 'REGIMENT',
  'BATTALION', 'COMPANY', 'PLATOON', 'SECTION', 'SQUAD', 'FIRETEAM', 'INDIVIDUAL',
]
const RELATIONS: RelationValue[] = ['ALLIED', 'NEUTRAL', 'HOSTILE']

// zh-TW 對照表 -------------------------------------------------------------
// E8 誠實化：WEGO/IGO_UGO 在 core 只有 enum 定義，`sim_runtime`/engine 完全沒有回合制分支——
// 選了「同步回合」跑起來仍是即時制。回合制是一整張功能卡；在做出來之前，**標示清楚**，
// 不要讓想定作者以為自己選了一種推演制度（這正是「設得到但沒效果」的老毛病）。
const UNIMPLEMENTED_MODE_SUFFIX = '（尚未實作，目前一律以即時制執行）'
const MODE_OPTIONS = [
  { label: '即時', value: 'REALTIME' },
  { label: `同步回合${UNIMPLEMENTED_MODE_SUFFIX}`, value: 'WEGO' },
  { label: `輪流回合${UNIMPLEMENTED_MODE_SUFFIX}`, value: 'IGO_UGO' },
]
const LEVEL_LABELS: Record<UnitLevel, string> = {
  INDIVIDUAL: '兵', FIRETEAM: '伍', SQUAD: '班', SECTION: '組', PLATOON: '排',
  COMPANY: '連', BATTALION: '營', REGIMENT: '團', BRIGADE: '旅', DIVISION: '師',
  CORPS: '軍', ARMY: '軍團', ARMY_GROUP: '集團軍', THEATER: '戰區',
}
const LEVEL_OPTIONS = LEVELS.map((l) => ({ label: LEVEL_LABELS[l], value: l }))
const RELATION_LABELS: Record<RelationValue, string> = {
  ALLIED: '同盟', NEUTRAL: '中立', HOSTILE: '敵對',
}

const model = ref<ScenarioModel>(emptyScenario())
const importText = ref('')
const importError = ref('')
const exportText = computed(() => JSON.stringify(exportScenario(model.value), null, 2))

// 想定 meta（E7）----------------------------------------------------------
// 說明欄：空字串一律收回 undefined。寫 `description: ""` 進想定不會壞，但會在檔案裡
// 留下一個看起來「有設定卻是空的」欄位，且 diff 永遠髒。
const description = computed({
  get: () => model.value.description ?? '',
  // InputText 清空時可能送 undefined，先收成字串再判斷（直接 .trim() 會炸掉整個頁面）。
  set: (v: string) => { const t = String(v ?? ''); model.value.description = t.trim() ? t : undefined },
})

// bbox 順序是 [最小經, 最小緯, 最大經, 最大緯]（GeoJSON 慣例，contracts 與 MapFeature 一致）。
const BBOX_LABELS = ['西界（最小經度）', '南界（最小緯度）', '東界（最大經度）', '北界（最大緯度）']
function setBbox(i: number, v: number) {
  // 非數值不寫入：bbox 是必填四元組，塞 null/NaN 進去會讓整份想定存不進伺服器，
  // 而錯誤訊息只會說 "bbox: 不是數字"，作者很難連回是哪一格被清空的。
  if (!Number.isFinite(v)) return
  const bbox = [...model.value.bbox] as [number, number, number, number]
  bbox[i] = v
  model.value.bbox = bbox
}
const bboxInvalid = computed(() => {
  const [minLng, minLat, maxLng, maxLat] = model.value.bbox
  return minLng >= maxLng || minLat >= maxLat
})

function setTickRate(v: number) {
  if (!Number.isFinite(v) || v <= 0) return
  model.value.tickRateMs = Math.trunc(v)
}
/** tick 長度的人話版本（作者填的是毫秒，腦子裡想的是「幾分鐘一步」）。 */
const tickLength = computed(() => {
  const ms = model.value.tickRateMs
  if (ms >= 60000) return `${Number((ms / 60000).toFixed(3))} 分模擬時間`
  return `${Number((ms / 1000).toFixed(3))} 秒模擬時間`
})
// tick 速率**現在真的會生效**（想定宣告值已接進執行期時鐘）。它是行軍距離、每日補給消耗、
// 修理進度的共同分母，設太小＝同樣的推演時間內部隊幾乎不動、油彈幾乎不耗。
const tickWarning = computed(() => {
  const ms = model.value.tickRateMs
  if (ms < 100) return 'tick 速率低於契約下限 100 ms，存檔會被伺服器拒絕。'
  if (ms < 10000) return `1 tick 僅 ${Number((ms / 1000).toFixed(3))} 秒：速度、每日消耗、修理進度都以 tick 長度換算，設這麼小會讓這些數字全部失真。`
  return ''
})

const AGG_LEVEL_OPTIONS = [
  { label: '（未宣告＝營級）', value: '' },
  { label: '營級以上', value: 'BATTALION' },
  { label: '旅級以上', value: 'BRIGADE' },
  { label: '師級以上', value: 'DIVISION' },
]
const aggLevel = computed<string>({
  get: () => model.value.aggregateAdjudicationLevel ?? '',
  set: (v: string) => {
    // 空選項要寫回 undefined 而不是 ''：''不是合法 enum 值，存檔會被 schema 擋下。
    model.value.aggregateAdjudicationLevel = v
      ? (v as NonNullable<ScenarioModel['aggregateAdjudicationLevel']>)
      : undefined
  },
})

// 想定設定（E6）----------------------------------------------------------
// 申請配額：**清空＝不限**，0＝一張都不准申請。兩者是不同的想定意圖，所以清空要寫回 undefined
// 而不是 0——C2 面板對「不限」顯示（不限），對 0 顯示額度用罄。
function quotaOf(kind: RequestQuotaKind): number | undefined {
  return model.value.requestQuotas?.[kind]
}
function setQuota(kind: RequestQuotaKind, v: number | null) {
  // 逐種重建而不是 delete：清空的那一種必須**整個鍵消失**（＝不限），留 undefined 值在物件裡
  // 會讓 JSON.stringify 吃掉它但 Object.keys 仍看得到，於是 requestQuotas 變成永遠非空。
  const next: EditorRequestQuotas = {}
  for (const k of REQUEST_QUOTA_KINDS) {
    const cur = k === kind ? v : model.value.requestQuotas?.[k] ?? null
    if (cur !== null && Number.isFinite(cur) && cur >= 0) next[k] = Math.trunc(cur)
  }
  model.value.requestQuotas = Object.keys(next).length ? next : undefined
}

// 晝夜：勾選即寫入預設 06:00/18:00（schema 要求 sunrise/sunset 同時存在，不能半殘）。
const dayNightOn = computed({
  get: () => model.value.dayNight !== undefined,
  set: (on: boolean) => { model.value.dayNight = on ? { ...DAY_NIGHT_DEFAULTS } : undefined },
})
type DayNightField = 'sunriseMin' | 'sunsetMin' | 'startMin'
const hourOf = (min: number) => Math.floor(min / 60)
const minuteOf = (min: number) => min % 60
/** 顯示用：startMin 未宣告時視為午夜（與後端 `start_minute()` 的預設一致）。 */
function timeOf(field: DayNightField): number {
  return model.value.dayNight?.[field] ?? 0
}
function setTime(field: DayNightField, part: 'h' | 'm', v: number) {
  const d = model.value.dayNight
  if (!d) return
  // 時/分清空一律當 0：這裡與 bbox 不同——把時刻欄位留舊值不寫，畫面會是空的、匯出卻仍是舊時間，
  // 使用者看不出自己清掉的東西還在。時刻歸零沒有危險，「畫面與檔案不一致」才有。
  const n = Number.isFinite(v) ? Math.trunc(v) : 0
  const cur = timeOf(field)
  const h = part === 'h' ? Math.min(23, Math.max(0, n)) : hourOf(cur)
  const m = part === 'm' ? Math.min(59, Math.max(0, n)) : minuteOf(cur)
  d[field] = h * 60 + m
}
const hhmm = (min: number) => `${String(hourOf(min)).padStart(2, '0')}:${String(minuteOf(min)).padStart(2, '0')}`
/** 日落早於日出＝跨午夜的夜間（schema 明講允許），提示作者這不是填錯。 */
const dayNightNote = computed(() => {
  const d = model.value.dayNight
  if (!d) return ''
  return d.sunsetMin < d.sunriseMin
    ? `夜間跨午夜：${hhmm(d.sunsetMin)} 天黑，翌日 ${hhmm(d.sunriseMin)} 天亮。`
    : `白天 ${hhmm(d.sunriseMin)}–${hhmm(d.sunsetMin)}，其餘時間為夜間。`
})

// 陣地變換：勾選即寫入與後端相同的預設值（SURVIVABILITY_DEFAULTS），取消勾選則整段不寫（＝停用）。
const survivabilityOn = computed({
  get: () => model.value.survivabilityMove?.enabled === true,
  set: (on: boolean) => {
    model.value.survivabilityMove = on ? { enabled: true, ...SURVIVABILITY_DEFAULTS } : undefined
  },
})
function setSurvivability(field: 'missionsBeforeMove' | 'minKm' | 'maxKm', v: number) {
  const s = model.value.survivabilityMove
  if (!s || !Number.isFinite(v)) return
  s[field] = field === 'missionsBeforeMove' ? Math.trunc(v) : v
}
// 後端遇到 min>max 會自動對調而不是報錯，但那是救急不是設計意圖——在這裡先講清楚。
const survivabilityNote = computed(() => {
  const s = model.value.survivabilityMove
  if (!s?.enabled) return ''
  const lo = s.minKm ?? SURVIVABILITY_DEFAULTS.minKm
  const hi = s.maxKm ?? SURVIVABILITY_DEFAULTS.maxKm
  return lo > hi ? '位移下限大於上限，載入時後端會自動對調。' : ''
})

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
/**
 * 軍械庫範本（編裝下拉的來源）。
 *
 * **一律從清單挑，不讓人手打**：`equipment[].template` 參照的是
 * `EquipmentTemplate.name`，打錯字要到**開局**才報錯（`_create_declared_equipment`
 * 找不到名稱就整局載不起來）。想定編輯與開局之間隔著幾天，那時候沒有人記得打了什麼。
 *
 * 抓不到範本（未登入／後端沒起）→ 空清單，編裝區塊顯示提示而不是一個空下拉。
 */
const templates = ref<EquipmentTemplate[]>([])
const templateNames = computed(() => templates.value.map((x) => x.name).sort())

onMounted(() => {
  const q = useRoute().query.load
  const id = Array.isArray(q) ? q[0] : q
  if (id) loadExisting(String(id))
  fetchEquipmentTemplates()
    .then((list) => {
      templates.value = list
    })
    .catch(() => {
      templates.value = []
    })
})

// ---- 編裝（P6：編輯器能不能獨立產出一份能打的想定的分水嶺）----

/** 目前展開編裝面板的單位（以番號＋陣營為鍵——想定裡的單位還沒有 id）。 */
const equipOpen = ref<string | null>(null)
function unitKey(u: EditorUnit): string {
  return `${u.faction}/${u.designation}`
}
function toggleEquip(u: EditorUnit) {
  equipOpen.value = equipOpen.value === unitKey(u) ? null : unitKey(u)
}
/**
 * 開始編這支單位的編裝。
 *
 * **從 undefined 變成 `[]` 是一個有意義的動作**：省略＝沿用開局的預設配發，
 * 空陣列＝這支單位刻意什麼都不帶。所以第一次按下去就要建立空陣列，
 * 否則作者刪光了所有項目，存出去的想定會退回「預設配發」而不是「什麼都不帶」。
 */
function ensureEquip(u: EditorUnit): EditorEquipment[] {
  if (!u.equipment) u.equipment = []
  return u.equipment
}
function addEquip(u: EditorUnit) {
  ensureEquip(u).push({ template: templateNames.value[0] ?? '', quantity: 1 })
}
function removeEquip(u: EditorUnit, i: number) {
  ensureEquip(u).splice(i, 1)
}
/** 摘要（收合時顯示）。`undefined` 與 `[]` 要講得不一樣。 */
function equipSummary(u: EditorUnit): string {
  if (u.equipment === undefined) return '（沿用預設配發）'
  if (!u.equipment.length) return '（不配發任何裝備）'
  return u.equipment.map((e) => `${e.template}×${e.quantity ?? 1}`).join('、')
}

function addFaction() {
  model.value.factions.push({ id: `F${model.value.factions.length + 1}`, color: '#888888' })
}
function addUnit() {
  const f = model.value.factions[0]?.id ?? 'BLUE'
  model.value.units.push({ faction: f, designation: 'U', unitLevel: 'PLATOON', branch: 'UNKNOWN' })
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
// 勝負條件（想定規格要求至少一條）：預設「某陣營於敵方被殲滅時獲勝」，條件 DSL 與 MSEL 觸發共用。
function addVictory() {
  const winner = factionIds.value[0] ?? 'BLUE'
  const enemy = factionIds.value.find((f) => f !== winner) ?? winner
  model.value.victoryConditions.push({
    faction: winner,
    condition: emptyCondition('faction_eliminated', enemy),
  })
}
const factionOptions = computed(() => factionIds.value.map((f) => ({ label: f, value: f })))
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

// #29 ORBAT 樹狀（分陣營）：依 parent（上級番號）建指揮層級樹，每個陣營一棵 TreeTable。
interface OrbatNode {
  key: string
  data: EditorUnit
  children: OrbatNode[]
}
function buildFactionTree(units: EditorUnit[], keyOf: (u: EditorUnit) => string): OrbatNode[] {
  const nodeOf = new Map<EditorUnit, OrbatNode>()
  units.forEach((u) => nodeOf.set(u, { key: keyOf(u), data: u, children: [] }))
  const roots: OrbatNode[] = []
  for (const u of units) {
    const node = nodeOf.get(u)!
    const parent = u.parent ? units.find((x) => x !== u && x.designation === u.parent) : undefined
    if (parent && nodeOf.has(parent)) nodeOf.get(parent)!.children.push(node)
    else roots.push(node)
  }
  // 環路/孤兒安全：確保每個節點皆可從 roots 觸及，否則升為 root（避免遺失單位）。
  const seen = new Set<OrbatNode>()
  const walk = (ns: OrbatNode[]) =>
    ns.forEach((n) => {
      if (!seen.has(n)) {
        seen.add(n)
        walk(n.children)
      }
    })
  walk(roots)
  for (const node of nodeOf.values()) {
    if (!seen.has(node)) {
      roots.push(node)
      walk([node])
    }
  }
  return roots
}
// 依單位在 model.units 的索引作為穩定 key（供 remove + expandedKeys）。
// 每個**宣告的陣營**都建一棵樹（即使 0 單位）→ 各陣營皆可「＋加單位」；另補上「有單位但未宣告」
// 的陣營（匯入相容，不遺失單位）。
const orbatTrees = computed(() => {
  const keyOf = (u: EditorUnit) => String(model.value.units.indexOf(u))
  const declared = model.value.factions.map((f) => f.id)
  const extra = [...new Set(model.value.units.map((u) => u.faction))].filter(
    (f) => !declared.includes(f),
  )
  return [...declared, ...extra].map((fid) => {
    const color = model.value.factions.find((f) => f.id === fid)?.color
    const units = model.value.units.filter((u) => u.faction === fid)
    return { id: fid, color, count: units.length, nodes: buildFactionTree(units, keyOf) }
  })
})
// 展開/收合整棵陣營樹（PrimeVue TreeTable 以 expandedKeys 控制；收合下級單位用）。
function setTreeExpanded(nodes: OrbatNode[], val: boolean): void {
  const keys = { ...expandedKeys.value }
  const walk = (ns: OrbatNode[]) =>
    ns.forEach((n) => {
      keys[n.key] = val
      walk(n.children)
    })
  walk(nodes)
  expandedKeys.value = keys
}
// 新增節點預設展開，保留使用者手動摺疊。
const expandedKeys = ref<Record<string, boolean>>({})
watch(
  orbatTrees,
  (trees) => {
    const keys = { ...expandedKeys.value }
    const add = (ns: OrbatNode[]) =>
      ns.forEach((n) => {
        if (!(n.key in keys)) keys[n.key] = true
        add(n.children)
      })
    trees.forEach((t) => add(t.nodes))
    expandedKeys.value = keys
  },
  { immediate: true, deep: true },
)
function addUnitTo(fid: string) {
  model.value.units.push({ faction: fid, designation: 'U', unitLevel: 'PLATOON', branch: 'UNKNOWN' })
}
function removeUnit(u: EditorUnit) {
  const i = model.value.units.indexOf(u)
  if (i >= 0) model.value.units.splice(i, 1)
  openPickers.value.delete(u)
}
function openPickersOf(fid: string): EditorUnit[] {
  return [...openPickers.value].filter((u) => u.faction === fid)
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

    <section class="meta" data-testid="sc-meta">
      <label data-scenario-key="name">名稱 <InputText v-model="model.name" size="small" data-testid="sc-name" /></label>
      <label data-scenario-key="version">版本 <InputText v-model="model.version" size="small" data-testid="sc-version" /></label>
      <label data-scenario-key="description" class="meta-wide">說明
        <InputText v-model="description" size="small" placeholder="想定概要（選填）" data-testid="sc-description" />
      </label>
      <label data-scenario-key="mode">推演模式
        <Select
          v-model="model.mode"
          :options="MODE_OPTIONS"
          option-label="label"
          option-value="value"
          size="small"
          data-testid="sc-mode"
        />
      </label>
    </section>
    <!-- E8：回合制在 core 只有 enum，執行期沒有任何分支。選了也不會變成回合制，講明白。 -->
    <Message
      v-if="model.mode !== 'REALTIME'"
      severity="warn"
      size="small"
      class="sc-status"
      data-testid="sc-mode-warning"
    >
      回合制（同步回合／輪流回合）尚未實作：執行期沒有回合分支，本局仍會以即時制逐 tick 推進。
      此選項目前只是想定文件上的宣告。
    </Message>

    <!-- E7：戰場範圍／tick 速率／六角解析度／彙整裁決層級——過去這四項在模型裡有、UI 卻沒有，
         用編輯器新建的想定戰場永遠是預設 bbox、節奏永遠是 60000ms，只能改 JSON。 -->
    <section data-testid="sc-battlefield-section">
      <h2>戰場範圍與節奏</h2>
      <div class="row" data-scenario-key="bbox" data-testid="sc-bbox">
        <label v-for="(lbl, bi) in BBOX_LABELS" :key="bi" class="setting-field">{{ lbl }}
          <InputNumber
            :model-value="model.bbox[bi]"
            :max-fraction-digits="6"
            size="small"
            :input-style="{ width: '7rem' }"
            @update:model-value="(v: number) => setBbox(bi, v)"
          />
        </label>
      </div>
      <p class="hint">
        戰場範圍（WGS84）決定地圖初始視野與想定的作戰地境；單位可以擺在框外，但 COP 開圖時不會在畫面上。
      </p>
      <Message v-if="bboxInvalid" severity="warn" size="small" data-testid="sc-bbox-warning">
        西界須小於東界、南界須小於北界，否則此範圍算不出任何區域。
      </Message>

      <div class="row">
        <label class="setting-field" data-scenario-key="tick_rate_ms">tick 速率（毫秒）
          <InputNumber
            :model-value="model.tickRateMs"
            :min="100"
            size="small"
            :input-style="{ width: '8rem' }"
            data-testid="sc-tick-rate"
            @update:model-value="setTickRate"
          />
        </label>
        <span class="hint">1 tick ＝ {{ tickLength }}</span>
      </div>
      <p class="hint">
        本局執行期的時間刻度，<b>此值會實際決定執行期節奏</b>（60000 ＝ 1 tick 為 1 分鐘）。行軍距離、
        每日油彈消耗、修理進度都以 tick 長度換算，改它等於改動整局的所有速率。
      </p>
      <Message v-if="tickWarning" severity="warn" size="small" data-testid="sc-tick-warning">
        {{ tickWarning }}
      </Message>

      <div class="row">
        <label class="setting-field" data-scenario-key="hex_resolution">六角格解析度
          <!-- 目前只支援 h3 res 8：core 的地形取樣/路徑規劃/預檢全部固定在該解析度，
               宣告別的值 loader 會直接拒載。給下拉等於給一個必定失敗的選項，故鎖定唯讀。 -->
          <InputText model-value="8" size="small" disabled class="hex-res" data-testid="sc-hex-resolution" />
        </label>
        <span class="hint">固定值：地形取樣、路徑規劃與預檢皆以 h3 解析度 8 運作，填別的值伺服器會拒載。</span>
      </div>

      <div class="row">
        <label class="setting-field" data-scenario-key="aggregate_adjudication_level">彙整裁決層級
          <Select
            v-model="aggLevel"
            :options="AGG_LEVEL_OPTIONS"
            option-label="label"
            option-value="value"
            size="small"
            data-testid="sc-agg-level"
          />
        </label>
      </div>
      <p class="hint">
        達到此層級（含）以上的單位交戰改用 Lanchester 聚合裁決，不再逐武器逐發計算——大部隊會戰跑得動，
        代價是單一武器的細節不再進入結果。
      </p>
    </section>

    <!-- E6：contracts 有這五個頂層設定，過去編輯器一個欄位都沒有，只能靠「匯入 JSON」手貼。 -->
    <section data-testid="sc-rules-section">
      <h2>想定設定</h2>

      <div class="setting" data-scenario-key="request_quotas" data-testid="sc-quotas">
        <div class="setting-head">申請配額</div>
        <div class="row">
          <label v-for="kind in REQUEST_QUOTA_KINDS" :key="kind" class="setting-field">
            {{ REQUEST_KIND_LABELS[kind] ?? kind }}
            <InputNumber
              :model-value="quotaOf(kind)"
              :min="0"
              :max-fraction-digits="0"
              size="small"
              placeholder="不限"
              :input-style="{ width: '5.5rem' }"
              :data-testid="`sc-quota-${kind}`"
              @update:model-value="(v: number) => setQuota(kind, v)"
            />
          </label>
        </div>
        <p class="hint">
          參謀能提出的申請單上限（<b>整局總量，不是每日</b>；各陣營分別計算）。留空＝不限，
          「信文／申請」面板顯示「（不限）」；填 0 ＝ 該種申請一張都不准提。額度用罄後再提的申請會直接落
          「已駁回」留痕，而不是被拒收——AAR 才看得出該陣營是在第幾 tick 被配額卡住。
        </p>
      </div>

      <div class="setting" data-scenario-key="day_night" data-testid="sc-day-night">
        <label class="setting-head">
          <Checkbox v-model="dayNightOn" binary data-testid="sc-day-night-toggle" />宣告晝夜
        </label>
        <div v-if="model.dayNight" class="row" data-testid="sc-day-night-fields">
          <label class="setting-field">日出
            <InputNumber
              :model-value="hourOf(timeOf('sunriseMin'))" :min="0" :max="23" size="small"
              :input-style="{ width: '3.5rem' }" data-testid="sc-sunrise-h"
              @update:model-value="(v: number) => setTime('sunriseMin', 'h', v)"
            />時
            <InputNumber
              :model-value="minuteOf(timeOf('sunriseMin'))" :min="0" :max="59" size="small"
              :input-style="{ width: '3.5rem' }" data-testid="sc-sunrise-m"
              @update:model-value="(v: number) => setTime('sunriseMin', 'm', v)"
            />分
          </label>
          <label class="setting-field">日落
            <InputNumber
              :model-value="hourOf(timeOf('sunsetMin'))" :min="0" :max="23" size="small"
              :input-style="{ width: '3.5rem' }" data-testid="sc-sunset-h"
              @update:model-value="(v: number) => setTime('sunsetMin', 'h', v)"
            />時
            <InputNumber
              :model-value="minuteOf(timeOf('sunsetMin'))" :min="0" :max="59" size="small"
              :input-style="{ width: '3.5rem' }" data-testid="sc-sunset-m"
              @update:model-value="(v: number) => setTime('sunsetMin', 'm', v)"
            />分
          </label>
          <label class="setting-field">開演時刻
            <InputNumber
              :model-value="hourOf(timeOf('startMin'))" :min="0" :max="23" size="small"
              :input-style="{ width: '3.5rem' }" data-testid="sc-start-h"
              @update:model-value="(v: number) => setTime('startMin', 'h', v)"
            />時
            <InputNumber
              :model-value="minuteOf(timeOf('startMin'))" :min="0" :max="59" size="small"
              :input-style="{ width: '3.5rem' }" data-testid="sc-start-m"
              @update:model-value="(v: number) => setTime('startMin', 'm', v)"
            />分
          </label>
        </div>
        <p v-if="dayNightNote" class="hint" data-testid="sc-day-night-note">{{ dayNightNote }}</p>
        <p class="hint">
          開了之後夜間會壓低觀測與命中（光照係數 &lt; 1）；不宣告＝整場都是白天（係數恆 1.0）。
          「開演時刻」是 tick 0 對應的當日時間，決定演習從幾點開始打。
        </p>
      </div>

      <div class="setting" data-scenario-key="allow_fratricide" data-testid="sc-fratricide">
        <label class="setting-head">
          <Checkbox v-model="model.allowFratricide" binary data-testid="sc-fratricide-toggle" />允許友軍誤傷裁決
        </label>
        <p class="hint">
          開了之後，對自己陣營／盟軍的交戰令不再被交戰規則直接拒絕，改為強警告＋照常裁決＋記「友軍誤傷」事件供 AAR 追究，
          COP 下令面板會出現誤傷確認核取方塊。<b>不涵蓋中立方</b>（攻中立仍一律拒），面射擊也不受本開關影響——
          砲彈不挑陣營，殺傷半徑內的友軍本來就會受傷。
        </p>
      </div>

      <div
        class="setting"
        data-scenario-key="indirect_fire_requires_approval"
        data-testid="sc-fire-approval"
      >
        <label class="setting-head">
          <Checkbox
            v-model="model.indirectFireRequiresApproval"
            binary
            data-testid="sc-fire-approval-toggle"
          />曲射火力須經火協核准
        </label>
        <p class="hint">
          開了之後，砲兵／飛彈單位的交戰令必須掛一張已核准的「火力支援」申請單，否則預檢直接拒絕；
          COP 下令面板會出現核准單下拉，「信文／申請」面板的申請—核覆流程才會真正被用到。不開＝曲射火力不設限。
        </p>
      </div>

      <div class="setting" data-scenario-key="survivability_move" data-testid="sc-survivability">
        <label class="setting-head">
          <Checkbox v-model="survivabilityOn" binary data-testid="sc-survivability-toggle" />啟用陣地變換
        </label>
        <div v-if="model.survivabilityMove?.enabled" class="row" data-testid="sc-survivability-fields">
          <label class="setting-field">每
            <InputNumber
              :model-value="model.survivabilityMove.missionsBeforeMove ?? SURVIVABILITY_DEFAULTS.missionsBeforeMove"
              :min="1" :max-fraction-digits="0" size="small"
              :input-style="{ width: '4.5rem' }" data-testid="sc-surv-missions"
              @update:model-value="(v: number) => setSurvivability('missionsBeforeMove', v)"
            />次火力任務後轉移
          </label>
          <label class="setting-field">位移
            <InputNumber
              :model-value="model.survivabilityMove.minKm ?? SURVIVABILITY_DEFAULTS.minKm"
              :min="0.1" :max-fraction-digits="2" size="small"
              :input-style="{ width: '4.5rem' }" data-testid="sc-surv-min-km"
              @update:model-value="(v: number) => setSurvivability('minKm', v)"
            />–
            <InputNumber
              :model-value="model.survivabilityMove.maxKm ?? SURVIVABILITY_DEFAULTS.maxKm"
              :min="0.1" :max-fraction-digits="2" size="small"
              :input-style="{ width: '4.5rem' }" data-testid="sc-surv-max-km"
              @update:model-value="(v: number) => setSurvivability('maxKm', v)"
            />公里
          </label>
        </div>
        <Message v-if="survivabilityNote" severity="warn" size="small" data-testid="sc-surv-warning">
          {{ survivabilityNote }}
        </Message>
        <p class="hint">
          開了之後，自走砲（履帶／輪型）打滿指定次數的火力任務就會自動下一道移動令換陣地，避免被反砲兵火力找上；
          計的是<b>火力任務次數</b>不是發數。牽引砲不會被排程（需要牽引車，尚無資料模型）。
          不開＝砲兵不會自動轉移陣地（仍可由人工下移動令）。
        </p>
      </div>
    </section>

    <section data-scenario-key="factions">
      <h2>陣營 <Button data-testid="add-faction" size="small" text @click="addFaction">＋</Button></h2>
      <div v-for="(f, i) in model.factions" :key="i" class="row" data-testid="faction-row">
        <InputText v-model="f.id" size="small" placeholder="ID" />
        <input v-model="f.color" type="color" class="color-input">
        <Button size="small" text severity="danger" @click="remove(model.factions, i)">✕</Button>
      </div>
    </section>

    <section data-scenario-key="relations">
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
      <p class="hint">點格切換：同盟 → 中立 → 敵對（對稱寫入）。未宣告的配對一律視為敵對。</p>
    </section>

    <section>
      <h2>單位（ORBAT） <Button data-testid="add-unit" size="small" text @click="addUnit">＋</Button></h2>
      <p v-if="!model.units.length" class="empty-hint" data-testid="orbat-empty">
        尚無單位，按 ＋ 新增（依「上級」欄構成指揮層級樹）。
      </p>
      <!-- #29 分陣營 TreeTable：每個陣營一棵樹，依上級番號構成指揮層級 -->
      <div
        v-for="tree in orbatTrees"
        :key="tree.id"
        class="orbat-faction"
        data-testid="orbat-faction"
      >
        <div class="orbat-faction-head">
          <span class="fac-dot" :style="{ background: tree.color || '#888' }" />
          <b>{{ tree.id }}</b>
          <span class="fac-count">· {{ tree.count }} 單位</span>
          <Button
            size="small"
            text
            data-testid="add-unit-faction"
            @click="addUnitTo(tree.id)"
          >＋ 加單位</Button>
          <span v-if="tree.count > 1" class="tree-toggles">
            <Button
              size="small"
              text
              severity="secondary"
              data-testid="expand-all"
              @click="setTreeExpanded(tree.nodes, true)"
            >展開全部</Button>
            <Button
              size="small"
              text
              severity="secondary"
              data-testid="collapse-all"
              @click="setTreeExpanded(tree.nodes, false)"
            >收合全部</Button>
          </span>
        </div>
        <p v-if="!tree.nodes.length" class="empty-hint" data-testid="orbat-faction-empty">
          此陣營尚無單位，按「＋ 加單位」新增（設「上級」欄即構成可收合的指揮層級）。
        </p>
        <TreeTable
          v-else
          v-model:expanded-keys="expandedKeys"
          :value="tree.nodes"
          size="small"
          data-testid="orbat-treetable"
        >
          <Column field="designation" header="番號" expander>
            <template #body="{ node }">
              <InputText
                v-model="node.data.designation"
                size="small"
                placeholder="番號"
                data-testid="unit-designation"
              />
            </template>
          </Column>
          <Column header="編制">
            <template #body="{ node }">
              <Select
                v-model="node.data.unitLevel"
                :options="LEVEL_OPTIONS"
                option-label="label"
                option-value="value"
                size="small"
              />
            </template>
          </Column>
          <Column header="上級">
            <template #body="{ node }">
              <Select
                v-model="node.data.parent"
                :options="parentOptions(node.data)"
                option-label="label"
                option-value="value"
                size="small"
                placeholder="上級"
              />
            </template>
          </Column>
          <Column header="兵科">
            <template #body="{ node }">
              <Select
                v-model="node.data.branch"
                :options="BRANCH_OPTIONS"
                :option-label="(b: string) => BRANCH_LABELS[b] ?? b"
                size="small"
                data-testid="unit-branch"
                placeholder="兵科"
              />
            </template>
          </Column>
          <Column header="固定">
            <template #body="{ node }">
              <label
                class="fixed-cell"
                data-testid="unit-fixed"
                title="固定單位（指揮部/後勤/陣地）：不接受移動令，不會被派去移動或機動交戰"
              >
                <Checkbox v-model="node.data.fixed" binary />
                <span v-if="node.data.fixed" class="fixed-tag">🔒 指揮部</span>
              </label>
            </template>
          </Column>
          <!-- 編裝（P6）。**沒有編裝的單位打不了仗**——`ENGAGE` 找不到武器、預檢直接不可行。
               範本一律從軍械庫清單挑：`template` 參照的是名稱，打錯字要到開局才報錯。 -->
          <Column header="編裝">
            <template #body="{ node }">
              <button
                class="equip-btn"
                data-testid="unit-equip-toggle"
                :title="equipSummary(node.data)"
                @click="toggleEquip(node.data)"
              >
                {{ equipSummary(node.data) }}
              </button>
            </template>
          </Column>
          <Column header="座標">
            <template #body="{ node }">
              <span class="coord-cell">
                <InputText
                  :model-value="numStr(node.data.lat)"
                  size="small"
                  placeholder="緯"
                  class="coord"
                  @update:model-value="(v: string | undefined) => setNum(node.data, 'lat', v)"
                />
                <InputText
                  :model-value="numStr(node.data.lng)"
                  size="small"
                  placeholder="經"
                  class="coord"
                  @update:model-value="(v: string | undefined) => setNum(node.data, 'lng', v)"
                />
                <Button
                  size="small"
                  text
                  :severity="openPickers.has(node.data) ? 'primary' : 'secondary'"
                  data-testid="unit-pick-toggle"
                  @click="togglePicker(node.data)"
                >📍</Button>
              </span>
            </template>
          </Column>
          <Column header="">
            <template #body="{ node }">
              <Button
                size="small"
                text
                severity="danger"
                data-testid="remove-unit"
                @click="removeUnit(node.data)"
              >✕</Button>
            </template>
          </Column>
        </TreeTable>
        <!-- 編裝面板（展開者顯示於表格下方，與地圖選取同一個模式） -->
        <div
          v-for="u in model.units.filter((x) => x.faction === tree.id && unitKey(x) === equipOpen)"
          :key="`equip-${unitKey(u)}`"
          class="equip-panel"
          data-testid="unit-equip-panel"
        >
          <div class="equip-head">
            <b>{{ u.designation }} 編裝</b>
            <Button size="small" text data-testid="equip-add" @click="addEquip(u)">＋ 加一件</Button>
            <Button size="small" text severity="secondary" @click="equipOpen = null">收合</Button>
          </div>
          <p v-if="!templateNames.length" class="empty-hint" data-testid="equip-no-templates">
            軍械庫沒有可用範本（或尚未登入）——請先到「軍械庫」建立裝備範本，
            編裝參照的是範本**名稱**。
          </p>
          <p v-else-if="u.equipment === undefined" class="hint">
            尚未宣告編裝：開局時會沿用預設配發。按「＋ 加一件」開始自訂
            （<b>清空後存檔＝這支單位刻意不帶任何裝備</b>，與「沿用預設」不同）。
          </p>
          <div v-for="(e, ei) in u.equipment ?? []" :key="ei" class="equip-row" data-testid="equip-row">
            <Select
              v-model="e.template"
              :options="templateNames"
              size="small"
              filter
              placeholder="範本"
              data-testid="equip-template"
            />
            <label class="equip-field">數量
              <InputNumber v-model="e.quantity" :min="1" size="small" data-testid="equip-quantity" />
            </label>
            <label class="equip-field" title="省略＝用範本的預設攜行量">彈藥
              <InputNumber v-model="e.ammo" :min="0" size="small" placeholder="預設" />
            </label>
            <Button size="small" text severity="danger" data-testid="equip-remove" @click="removeEquip(u, ei)">✕</Button>
          </div>
        </div>
        <!-- 地圖選取（展開者顯示於表格下方） -->
        <ClientOnly v-for="u in openPickersOf(tree.id)" :key="`pick-${tree.id}-${u.designation}`">
          <div class="picker-wrap" data-testid="unit-picker-wrap">
            <span class="picker-label">📍 {{ u.designation }} 初始位置</span>
            <MapPointPicker
              class="unit-picker"
              :model-value="unitPoint(u)"
              @update:model-value="(p: { lng: number; lat: number }) => setUnitPoint(u, p)"
            />
          </div>
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

    <section data-testid="victory-section" data-scenario-key="victory_conditions">
      <h2>勝負條件 <Button data-testid="add-victory" size="small" text @click="addVictory">＋</Button></h2>
      <p class="hint">每條：指定陣營於「條件」成立時獲勝（條件的寫法與 MSEL 事件的觸發條件相同）。想定規格要求至少一條。</p>
      <p v-if="!model.victoryConditions.length" class="empty-hint" data-testid="victory-empty">
        尚無勝負條件——按 ＋ 新增（未設定將無法存檔）。
      </p>
      <div v-for="(v, i) in model.victoryConditions" :key="i" class="victory-row" data-testid="victory-row">
        <div class="victory-head">
          <label class="victory-winner">獲勝陣營
            <Select
              v-model="v.faction"
              :options="factionOptions"
              option-label="label"
              option-value="value"
              size="small"
            />
          </label>
          <Button size="small" text severity="danger" data-testid="remove-victory" @click="remove(model.victoryConditions, i)">✕</Button>
        </div>
        <div class="victory-block">
          <span class="msel-label">條件</span>
          <ConditionBuilder v-model="v.condition" :factions="factionIds" />
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
.equip-btn {
  background: none; border: 1px dashed #334155; border-radius: 4px;
  color: #94a3b8; font-size: 0.75rem; padding: 2px 6px; cursor: pointer;
  max-width: 14rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.equip-btn:hover { border-color: #64748b; color: #e2e8f0 }
.equip-panel { border-left: 2px solid #1e293b; margin: 0.25rem 0 0.5rem; padding: 0.25rem 0 0.25rem 0.5rem }
.equip-head { display: flex; gap: 0.5rem; align-items: center }
.equip-row { display: flex; gap: 0.5rem; align-items: center; margin: 0.2rem 0 }
.equip-field { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.8125rem; color: #94a3b8 }

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
/* #29 ORBAT 分陣營 TreeTable */
.orbat-faction { margin: 0.5rem 0 1rem; }
.orbat-faction-head {
  display: flex; align-items: center; gap: 0.4rem;
  margin-bottom: 0.25rem; font-size: 0.9rem;
}
.orbat-faction-head .fac-dot {
  width: 0.75rem; height: 0.75rem; border-radius: 50%; display: inline-block; flex: none;
}
.orbat-faction-head .fac-count { color: #94a3b8; font-size: 0.8rem; }
.orbat-faction-head .tree-toggles { margin-left: auto; display: inline-flex; gap: 0.25rem; }
.coord-cell { display: inline-flex; gap: 0.25rem; align-items: center; }
.fixed-cell { display: inline-flex; gap: 0.35rem; align-items: center; cursor: pointer; }
.fixed-tag { color: #fbbf24; font-size: 0.72rem; white-space: nowrap; }
.picker-wrap { margin: 0.25rem 0 0.5rem; }
.picker-label { display: block; color: #94a3b8; font-size: 0.75rem; margin-bottom: 0.2rem; }
.empty-hint { color: #94a3b8; font-size: 0.82rem; }
:deep(.p-treetable) { font-size: 0.85rem; }
:deep(.p-treetable td) { padding: 0.2rem 0.4rem; }
.meta { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
.meta label { display: inline-flex; align-items: center; gap: 0.4rem; }
.meta .meta-wide { flex: 1 1 18rem; }
.meta .meta-wide :deep(input) { width: 100%; }
/* 想定設定：一項一塊，控制項後面必有一行「開了會怎樣」 */
.setting { margin: 0.75rem 0; }
.setting + .setting { border-top: 1px dashed #1e293b; padding-top: 0.75rem; }
.setting-head {
  display: inline-flex; align-items: center; gap: 0.4rem;
  font-size: 0.85rem; color: #cbd5e1; margin-bottom: 0.25rem;
}
label.setting-head { cursor: pointer; }
.setting-field {
  display: inline-flex; align-items: center; gap: 0.3rem;
  font-size: 0.8125rem; color: #94a3b8;
}
.hex-res { width: 4rem; }
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
.victory-row { border: 1px solid #1e293b; border-radius: 0.35rem; padding: 0.5rem; margin: 0.4rem 0; }
.victory-head { display: flex; gap: 0.5rem; align-items: center; }
.victory-winner { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.8125rem; color: #94a3b8; }
.victory-block { display: flex; gap: 0.4rem; align-items: baseline; margin-top: 0.4rem; flex-wrap: wrap; }
</style>
