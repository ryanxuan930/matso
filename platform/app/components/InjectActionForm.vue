<script setup lang="ts">
/**
 * 注入動作編輯器——MSEL 腳本（想定編輯器）與白軍即時注入（控制台）共用。
 *
 * ## 這個表單過去把什麼丟給使用者
 *
 * 只有三格：event_type、目標陣營、一組要自己打 JSON 的 payload key/value。
 * 而後端的注入其實有**五種會真的改變世界的動作**（`msel_actions.make_applier`：
 * SPAWN_UNITS / MODIFY_UNIT / MESSAGE / PAUSE / WEATHER_OVERRIDE），
 * 它們的欄位在 `inject` 的**最上層**——這個表單一個都送不出去。
 * 結果是 WP-B2 整套注入引擎做完了、測試全綠，卻**沒有任何 UI 生得出一條會生效的注入**。
 *
 * ## 兩種型態（`variant`）不是樣式差別，是語義差別
 *
 * - `msel`（預設，想定編輯器）：注入由 `MselRuntime` 於觸發時套用 → **動作有效**。
 * - `live`（白軍控制台的即時注入）：`core/app/api/inject.py` 只把事件發進 Redis ring / WS，
 *   **不經過套用層**。所以這裡不給動作選項——給了就是騙人。即時注入能做的只有
 *   「發一則事件通知」，這一點表單自己要講清楚。
 *
 * 進階逃生口保留（直接編整個 inject 的 JSON）：結構化欄位覆蓋不到的東西
 * （增援的裝備清單、自訂 attributes）仍然編得到，而不是被鎖死。
 */
import {
  INJECT_ACTION_LABELS,
  injectActionIssues,
  setInjectAction,
  type InjectAction,
  type InjectActionKind,
  type SpawnUnitSpec,
} from '~/composables/useConditionDsl'
import { EVENT_LABELS } from '~/composables/useCopFeed'
import { ASSIGNABLE_SEATS, SEAT_ROLE_LABELS } from '~/composables/useParticipants'
import { UNIT_LEVEL_LABELS } from '~/composables/useUnits'

const props = withDefaults(
  defineProps<{
    modelValue: InjectAction
    factions: string[]
    eventTestid?: string
    /** `msel`＝腳本注入（動作有效）；`live`＝白軍即時注入（只發事件）。 */
    variant?: 'msel' | 'live'
    /** 可選的單位清單（活局才有）——讓「打哪個單位」不必手抄 UUID。 */
    units?: Array<{ id: string; designation: string; faction?: string }>
  }>(),
  { eventTestid: undefined, variant: 'msel', units: () => [] },
)
const emit = defineEmits<{ 'update:modelValue': [InjectAction] }>()

const EVENT_SUGGESTIONS = ['BRIDGE_DESTROYED', 'REINFORCEMENT', 'WEATHER_CHANGE', 'INTEL_REPORT']
const dlId = `inject-events-${useId()}`

/** 值為 undefined 的鍵一律不留——匯出的想定檔不該多出一堆空欄位。 */
function compact(obj: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined))
}
/** 統一的送出點。 */
function patch(p: Record<string, unknown>): void {
  emit('update:modelValue', compact({ ...props.modelValue, ...p }) as unknown as InjectAction)
}

// ---- 事件型別 ----

function setEventType(v: string | undefined) {
  patch({ event_type: v ?? '' })
}
/**
 * 戰況流有沒有這個型別的中文敘述（`useCopFeed.EVENT_LABELS`）。
 * 沒有的話 `formatEvent` 會走安全退路——**只印型別代號，整包 payload 都不顯示**。
 * 這對即時注入尤其致命：統裁以為自己發了一則說明，玩家看到的是一行英文。
 */
const typeUnknown = computed(
  () => !!props.modelValue.event_type && !EVENT_LABELS[props.modelValue.event_type],
)

// ---- 陣營（三種語義共用同一個後端鍵） ----

const factionOptions = computed(() => [
  { label: '（廣播全體）', value: '' },
  ...props.factions.map((f) => ({ label: f, value: f })),
])
const factionLabel = computed(() => {
  if (props.modelValue.action === 'SPAWN_UNITS') return '生成陣營'
  if (props.modelValue.action === 'MESSAGE') return '收件陣營'
  return '目標陣營'
})
function setFaction(v: string) {
  patch({ faction: v === '' ? undefined : v })
}

// ---- 注入動作 ----

const actionOptions = [
  { label: '（無）純事件通知', value: '' },
  ...Object.entries(INJECT_ACTION_LABELS).map(([value, label]) => ({ label, value })),
]
function setAction(v: string) {
  // 換動作要清掉前一個動作的欄位——理由見 `setInjectAction` 的註解。
  emit('update:modelValue', setInjectAction(props.modelValue, v as InjectActionKind | ''))
}

// 席位中文取自參與者名冊那一份對照表——同一組席位在兩個畫面上不該長不一樣的名字。
const SEAT_OPTIONS = [
  { label: '（全陣營）', value: '' },
  ...ASSIGNABLE_SEATS.map((value) => ({ label: SEAT_ROLE_LABELS[value] ?? value, value })),
]
const levelOptions = Object.entries(UNIT_LEVEL_LABELS).map(([value, label]) => ({
  label: `${label}（${value}）`,
  value,
}))
const unitOptions = computed(() =>
  props.units.map((u) => ({
    label: `${u.designation}${u.faction ? ` · ${u.faction}` : ''}`,
    value: u.id,
  })),
)

// ---- SPAWN_UNITS 的單位清單 ----

const spawnUnits = computed<SpawnUnitSpec[]>(() => props.modelValue.units ?? [])
function setUnit(i: number, p: Partial<SpawnUnitSpec>) {
  const units = [...spawnUnits.value]
  units[i] = { ...units[i], ...p }
  patch({ units })
}
function addUnit() {
  patch({ units: [...spawnUnits.value, {} as SpawnUnitSpec] })
}
function removeUnit(i: number) {
  const units = [...spawnUnits.value]
  units.splice(i, 1)
  patch({ units })
}

// ---- WEATHER_OVERRIDE 的效果係數（鍵名對齊 `core/app/weather.py` 的 CellEffects） ----

const EFFECT_FIELDS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'mobility_modifier', label: '機動係數', hint: '1.0＝不受影響；0.6＝泥濘難行' },
  { key: 'sensor_optical_modifier', label: '光學偵測係數', hint: '能見度；霧/雨天下降' },
  { key: 'sensor_ir_modifier', label: '紅外偵測係數', hint: '熱像儀受影響程度' },
  { key: 'artillery_dispersion_modifier', label: '砲兵散佈係數', hint: '>1＝散佈變大、命中下降' },
  { key: 'rf_attenuation_db', label: '無線電衰減（dB）', hint: '0＝無影響；影響通聯' },
  { key: 'wind_ms', label: '風速（m/s）', hint: '影響煙幕漂移' },
  { key: 'wind_dir_deg', label: '風向（度，來向）', hint: '氣象慣例：北風＝0' },
]
function effectValue(key: string): number | null {
  const v = props.modelValue.effects?.[key]
  return typeof v === 'number' ? v : null
}
function setEffect(key: string, v: number | boolean | null) {
  const drop = v === null || (typeof v === 'number' && !Number.isFinite(v))
  const eff = compact({ ...(props.modelValue.effects ?? {}), [key]: drop ? undefined : v })
  patch({ effects: Object.keys(eff).length ? (eff as Record<string, number | boolean>) : undefined })
}
function boolEffect(key: string): boolean {
  const v = props.modelValue.effects?.[key]
  return v === undefined ? true : !!v // 後端預設可用
}

// ---- payload（事件敘述用的自由欄位；本地 rows 為編輯真源） ----

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
  patch({ payload: rowsToPayload(rows.value) })
}
function addRow() {
  rows.value.push({ key: '', value: '' })
}
function removeRow(i: number) {
  rows.value.splice(i, 1)
  emitPayload()
}
/** live 型態：把單位下拉的選擇寫進 payload（`formatEvent` 就是讀這兩個鍵換番號的）。 */
function setPayloadKey(key: string, value: string) {
  patch({ payload: compact({ ...(props.modelValue.payload ?? {}), [key]: value || undefined }) })
}
function payloadStr(key: string): string {
  const v = props.modelValue.payload?.[key]
  return typeof v === 'string' ? v : ''
}

// ---- 進階：直接編整個 inject 的 JSON（逃生口） ----

const jsonOpen = ref(false)
const jsonText = ref('')
const jsonErr = ref('')
function toggleJson() {
  jsonOpen.value = !jsonOpen.value
  if (jsonOpen.value) {
    jsonText.value = JSON.stringify(props.modelValue, null, 2)
    jsonErr.value = ''
  }
}
function applyJson(text: string | undefined) {
  jsonText.value = text ?? ''
  try {
    const parsed = JSON.parse(jsonText.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      jsonErr.value = 'inject 必須是一個物件（{ … }）。'
      return
    }
    jsonErr.value = ''
    emit('update:modelValue', parsed as InjectAction)
  } catch (e) {
    // 講清楚是**哪裡**壞掉：JSON.parse 的訊息帶位置，比「格式錯誤」有用得多。
    jsonErr.value = `JSON 解析失敗：${(e as Error).message}`
  }
}

// ---- 送出前檢查 ----

// 呼叫端要擋送出時**自己呼叫同一個純函式**（`injectActionIssues`），不從這裡拿。
// 兩邊共用同一份判定，才不會出現「表單說有問題、按鈕仍然送得出去」。
const issues = computed(() => injectActionIssues(props.modelValue))
</script>

<template>
  <div class="iaf" data-testid="inject-action-form">
    <div class="iaf-head">
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
          <option v-for="s in Object.keys(EVENT_LABELS)" :key="s" :value="s" />
        </datalist>
      </label>

      <label class="iaf-field">{{ factionLabel }}
        <Select
          :model-value="modelValue.faction ?? ''"
          :options="factionOptions"
          option-label="label"
          option-value="value"
          size="small"
          data-testid="iaf-faction"
          @update:model-value="setFaction"
        />
      </label>

      <!-- 動作只在 MSEL 型態出現：即時注入端點不會套用它（見本檔頭）。 -->
      <label v-if="variant === 'msel'" class="iaf-field">注入動作
        <Select
          :model-value="modelValue.action ?? ''"
          :options="actionOptions"
          option-label="label"
          option-value="value"
          size="small"
          data-testid="iaf-action"
          @update:model-value="setAction"
        />
      </label>

      <Button size="small" text data-testid="iaf-json-toggle" @click="toggleJson">
        {{ jsonOpen ? '← 回結構化表單' : '進階：直接編 JSON' }}
      </Button>
    </div>

    <p v-if="typeUnknown" class="iaf-warn" data-testid="iaf-unknown-type">
      戰況流沒有「{{ modelValue.event_type }}」的中文敘述，會顯示成原始代號，
      <b>payload 內容一個字都不會出現</b>。要讓玩家讀得懂，請改用已知型別或另行發信文。
    </p>

    <p v-if="variant === 'live'" class="iaf-note" data-testid="iaf-live-note">
      即時注入<b>只發一則事件</b>（進 WS 戰況流），不會改變世界狀態。
      增援生成／調整單位／發信文／天氣覆蓋要寫在想定的 MSEL 腳本裡（本頁「待命注入」可扣發）。
    </p>

    <!-- ============ 進階 JSON（與結構化表單互斥，避免兩個真源打架） ============ -->
    <div v-if="jsonOpen" class="iaf-json-box">
      <Textarea
        :model-value="jsonText"
        rows="10"
        class="iaf-json"
        data-testid="iaf-json"
        spellcheck="false"
        @update:model-value="applyJson"
      />
      <p v-if="jsonErr" class="iaf-err" data-testid="iaf-json-error">{{ jsonErr }}</p>
      <p v-else class="iaf-note">已套用。結構化表單看不到的欄位（增援的 equipment、自訂 attributes）在這裡編。</p>
    </div>

    <template v-else>
      <!-- ============ MODIFY_UNIT ============ -->
      <div v-if="modelValue.action === 'MODIFY_UNIT'" class="iaf-block" data-testid="iaf-modify-unit">
        <label class="iaf-field">單位
          <Select
            v-if="unitOptions.length"
            :model-value="modelValue.unit_id ?? ''"
            :options="unitOptions"
            option-label="label"
            option-value="value"
            filter
            size="small"
            data-testid="iaf-unit-id"
            @update:model-value="(v: string) => patch({ unit_id: v || undefined })"
          />
          <InputText
            v-else
            :model-value="modelValue.unit_id ?? ''"
            size="small"
            placeholder="unit_id（UUID）"
            data-testid="iaf-unit-id"
            @update:model-value="(v?: string) => patch({ unit_id: v || undefined })"
          />
        </label>
        <label class="iaf-field">戰力
          <InputNumber
            :model-value="modelValue.strength ?? null"
            size="small"
            :min-fraction-digits="0"
            :max-fraction-digits="1"
            input-class="iaf-num"
            data-testid="iaf-strength"
            @update:model-value="(v: number | null) => patch({ strength: v ?? undefined })"
          />
        </label>
        <label class="iaf-field">緯度
          <InputNumber
            :model-value="modelValue.lat ?? null"
            size="small"
            :max-fraction-digits="6"
            input-class="iaf-num"
            data-testid="iaf-lat"
            @update:model-value="(v: number | null) => patch({ lat: v ?? undefined })"
          />
        </label>
        <label class="iaf-field">經度
          <InputNumber
            :model-value="modelValue.lng ?? null"
            size="small"
            :max-fraction-digits="6"
            input-class="iaf-num"
            data-testid="iaf-lng"
            @update:model-value="(v: number | null) => patch({ lng: v ?? undefined })"
          />
        </label>
        <p class="iaf-note">
          單位 id 是<b>開局時才產生</b>的（想定裡的編成沒有 id）。在想定編輯器裡唯一填得出來的
          是同一份 MSEL 用 SPAWN_UNITS 生出來的增援（其 id 由 msel 事件 id 決定性派生）。
        </p>
      </div>

      <!-- ============ MESSAGE ============ -->
      <div v-if="modelValue.action === 'MESSAGE'" class="iaf-block" data-testid="iaf-message">
        <label class="iaf-field">收件席位
          <Select
            :model-value="modelValue.to_seat ?? ''"
            :options="SEAT_OPTIONS"
            option-label="label"
            option-value="value"
            size="small"
            data-testid="iaf-to-seat"
            @update:model-value="(v: string) => patch({ to_seat: v || undefined })"
          />
        </label>
        <label class="iaf-field iaf-grow">內容
          <Textarea
            :model-value="modelValue.body ?? ''"
            rows="3"
            class="iaf-grow"
            data-testid="iaf-body"
            @update:model-value="(v?: string) => patch({ body: v || undefined })"
          />
        </label>
      </div>

      <!-- ============ PAUSE ============ -->
      <div v-if="modelValue.action === 'PAUSE'" class="iaf-block" data-testid="iaf-pause">
        <label class="iaf-field iaf-grow">暫停理由
          <InputText
            :model-value="modelValue.reason ?? ''"
            size="small"
            placeholder="講評／裁決討論"
            data-testid="iaf-reason"
            @update:model-value="(v?: string) => patch({ reason: v || undefined })"
          />
        </label>
      </div>

      <!-- ============ SPAWN_UNITS ============ -->
      <div v-if="modelValue.action === 'SPAWN_UNITS'" class="iaf-block iaf-col" data-testid="iaf-spawn">
        <span class="iaf-payload-label">增援單位
          <Button size="small" text data-testid="iaf-spawn-add" @click="addUnit">＋</Button>
        </span>
        <div v-for="(u, i) in spawnUnits" :key="i" class="iaf-row" data-testid="iaf-spawn-row">
          <InputText
            :model-value="u.designation ?? ''"
            size="small"
            placeholder="番號"
            @update:model-value="(v?: string) => setUnit(i, { designation: v || undefined })"
          />
          <Select
            :model-value="u.unit_level ?? 'PLATOON'"
            :options="levelOptions"
            option-label="label"
            option-value="value"
            size="small"
            @update:model-value="(v: string) => setUnit(i, { unit_level: v })"
          />
          <InputNumber
            :model-value="u.lat ?? null"
            size="small"
            :max-fraction-digits="6"
            placeholder="緯度"
            input-class="iaf-num"
            @update:model-value="(v: number | null) => setUnit(i, { lat: v ?? undefined })"
          />
          <InputNumber
            :model-value="u.lng ?? null"
            size="small"
            :max-fraction-digits="6"
            placeholder="經度"
            input-class="iaf-num"
            @update:model-value="(v: number | null) => setUnit(i, { lng: v ?? undefined })"
          />
          <InputNumber
            :model-value="u.strength ?? null"
            size="small"
            placeholder="戰力"
            input-class="iaf-num"
            @update:model-value="(v: number | null) => setUnit(i, { strength: v ?? undefined })"
          />
          <Button size="small" text severity="danger" @click="removeUnit(i)">✕</Button>
        </div>
        <p class="iaf-note">
          裝備（equipment）請用「進階：直接編 JSON」——沒有配裝的增援打不出任何一發子彈。
        </p>
      </div>

      <!-- ============ WEATHER_OVERRIDE ============ -->
      <div v-if="modelValue.action === 'WEATHER_OVERRIDE'" class="iaf-block iaf-col" data-testid="iaf-weather">
        <div class="iaf-row iaf-wrap">
          <label v-for="f in EFFECT_FIELDS" :key="f.key" class="iaf-field" :title="f.hint">
            {{ f.label }}
            <InputNumber
              :model-value="effectValue(f.key)"
              size="small"
              :max-fraction-digits="2"
              input-class="iaf-num"
              :data-testid="`iaf-effect-${f.key}`"
              @update:model-value="(v: number | null) => setEffect(f.key, v)"
            />
          </label>
        </div>
        <div class="iaf-row iaf-wrap">
          <label class="iaf-field">
            <Checkbox
              :model-value="boolEffect('uav_operability')"
              binary
              @update:model-value="(v: boolean) => setEffect('uav_operability', v)"
            />無人機可飛
          </label>
          <label class="iaf-field">
            <Checkbox
              :model-value="boolEffect('rotary_wing_operability')"
              binary
              @update:model-value="(v: boolean) => setEffect('rotary_wing_operability', v)"
            />直升機可飛
          </label>
          <label class="iaf-field">持續 tick
            <InputNumber
              :model-value="modelValue.duration_ticks ?? null"
              size="small"
              input-class="iaf-num"
              data-testid="iaf-duration"
              @update:model-value="(v: number | null) => patch({ duration_ticks: v ?? undefined })"
            />
          </label>
        </div>
      </div>

      <!-- ============ live：事件裡的相關單位（免手抄 UUID） ============ -->
      <div v-if="variant === 'live' && unitOptions.length" class="iaf-block" data-testid="iaf-live-units">
        <label class="iaf-field">發起單位
          <Select
            :model-value="payloadStr('initiator_id')"
            :options="[{ label: '（無）', value: '' }, ...unitOptions]"
            option-label="label"
            option-value="value"
            filter
            size="small"
            data-testid="iaf-initiator"
            @update:model-value="(v: string) => setPayloadKey('initiator_id', v)"
          />
        </label>
        <label class="iaf-field">目標單位
          <Select
            :model-value="payloadStr('target_id')"
            :options="[{ label: '（無）', value: '' }, ...unitOptions]"
            option-label="label"
            option-value="value"
            filter
            size="small"
            data-testid="iaf-target"
            @update:model-value="(v: string) => setPayloadKey('target_id', v)"
          />
        </label>
      </div>

      <!-- ============ payload（自由欄位） ============ -->
      <div class="iaf-payload">
        <span class="iaf-payload-label">payload（事件敘述用；動作欄位不放這裡）
          <Button size="small" text data-testid="iaf-add-payload" @click="addRow">＋</Button>
        </span>
        <div v-for="(r, i) in rows" :key="i" class="iaf-row" data-testid="iaf-payload-row">
          <InputText v-model="r.key" size="small" placeholder="key" @update:model-value="emitPayload" />
          <InputText v-model="r.value" size="small" placeholder="value（JSON）" class="iaf-val" @update:model-value="emitPayload" />
          <Button size="small" text severity="danger" @click="removeRow(i)">✕</Button>
        </div>
      </div>
    </template>

    <ul v-if="issues.length" class="iaf-issues" data-testid="iaf-issues">
      <li v-for="(is, i) in issues" :key="i" :class="is.level">
        {{ is.level === 'error' ? '✖' : '⚠' }} {{ is.text }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.iaf { display: flex; flex-direction: column; gap: 0.5rem; align-items: stretch; }
.iaf-head { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; }
.iaf-field { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.8125rem; color: #94a3b8; }
.iaf-block { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; padding: 0.4rem 0.5rem; border: 1px solid #1e293b; border-radius: 0.25rem; }
.iaf-col { flex-direction: column; align-items: stretch; }
.iaf-grow { flex: 1 1 16rem; }
.iaf-wrap { flex-wrap: wrap; }
.iaf-payload { display: flex; flex-direction: column; gap: 0.25rem; }
.iaf-payload-label { font-size: 0.8125rem; color: #94a3b8; display: inline-flex; align-items: center; gap: 0.25rem; }
.iaf-row { display: flex; gap: 0.3rem; align-items: center; }
.iaf-val { min-width: 10rem; }
.iaf-note { font-size: 0.75rem; color: #64748b; margin: 0; }
.iaf-warn { font-size: 0.75rem; color: #fbbf24; margin: 0; }
.iaf-err { font-size: 0.75rem; color: #f87171; margin: 0; }
.iaf-json-box { display: flex; flex-direction: column; gap: 0.25rem; }
.iaf-json { width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.75rem; }
.iaf-issues { list-style: none; padding: 0; margin: 0; font-size: 0.75rem; display: flex; flex-direction: column; gap: 0.15rem; }
.iaf-issues .error { color: #f87171; }
.iaf-issues .warn { color: #fbbf24; }
:deep(.iaf-num) { width: 7rem; }
</style>
