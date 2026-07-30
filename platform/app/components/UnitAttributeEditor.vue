<script setup lang="ts">
/**
 * 單位屬性編輯器——番號、兵科、編制級別、人數、戰力。
 *
 * 與 `UnitOrbatEditor`（武器/彈藥）並列於單位資訊卡：那一個管「帶什麼裝備」，
 * 這一個管「這是一支什麼樣的部隊」。
 *
 * ## 三件對操作員要講清楚的事
 *
 * 1. **作戰效能不可編**——它是由戰力比導出的，裁決層每次命中都會覆寫。
 *    畫面上只顯示算出來的值，並說明它從哪來，免得有人以為這裡可以「補血」。
 * 2. **建制數有三段推導**（明示 attributes → 人數 → 依級別導出）。改完人數要把
 *    **實際生效**的建制數顯示出來，而不是把使用者填的原值回吐——那兩個可能不同。
 * 3. **改編制級別要重啟**該局 runner 才會影響聚合裁決（後端回 `restart_required`）。
 *
 * 未變更的欄位**不送**（PATCH 語義）：一次把整包送上去，會把別人剛改的欄位蓋回去。
 */
import { BRANCH_LABELS, UNIT_LEVEL_LABELS } from '~/composables/useUnits'
import { editUnitAttributes, type UnitEditView } from '~/composables/useEquipment'

const props = defineProps<{
  sessionId: string
  unitId: string
  /** 目前值（由資訊卡傳入——它已經有一份權威的 UnitView）。 */
  designation: string
  branch: string
  unitLevel: string
  personnel: number | null
  strength: number
  authorizedStrength: number
}>()

const emit = defineEmits<{ (e: 'saved', v: UnitEditView): void }>()

const form = reactive({
  designation: '',
  branch: '',
  unit_level: '',
  personnel_current: null as number | null,
  current_strength: 0,
  authorized_strength: 0,
})

/** 把表單重設回 props 的目前值。換單位、或存檔完成後都要跑。 */
function reset() {
  form.designation = props.designation
  form.branch = props.branch
  form.unit_level = props.unitLevel
  form.personnel_current = props.personnel
  form.current_strength = props.strength
  form.authorized_strength = props.authorizedStrength
}
watch(() => props.unitId, reset, { immediate: true })
// 活模擬會持續改動戰力——若使用者沒在編輯，表單要跟著外部值走。
watch(() => [props.strength, props.designation, props.personnel], () => {
  if (!dirty.value) reset()
})

const busy = ref(false)
const err = ref('')
const note = ref('')
const result = ref<UnitEditView | null>(null)

/** 只有真的改過的欄位才送（PATCH 語義，見模組說明）。 */
const changes = computed(() => {
  const body: Record<string, unknown> = {}
  if (form.designation.trim() && form.designation !== props.designation) {
    body.designation = form.designation.trim()
  }
  if (form.branch !== props.branch) body.branch = form.branch
  if (form.unit_level !== props.unitLevel) body.unit_level = form.unit_level
  if (form.personnel_current !== props.personnel) body.personnel_current = form.personnel_current
  if (form.current_strength !== props.strength) body.current_strength = form.current_strength
  if (form.authorized_strength !== props.authorizedStrength) {
    body.authorized_strength = form.authorized_strength
  }
  return body
})
const dirty = computed(() => Object.keys(changes.value).length > 0)

/** 作戰效能是**算出來的**——這裡預覽它會變成多少，不接受輸入。 */
const effectivenessPreview = computed(() => {
  const auth = form.authorized_strength
  if (!auth || auth <= 0) return 0
  return Math.round((form.current_strength / auth) * 1000) / 10
})

async function save() {
  if (!dirty.value || busy.value) return
  busy.value = true
  err.value = ''
  note.value = ''
  try {
    const view = await editUnitAttributes(props.sessionId, props.unitId, changes.value)
    result.value = view
    if (view.restart_required) {
      note.value = '已儲存。編制級別要等本局重新啟動才會影響聚合裁決（其餘欄位已即時生效）。'
    } else {
      note.value = '已儲存，並已套用至進行中的推演。'
    }
    emit('saved', view)
  } catch (e) {
    err.value = (e as { message?: string }).message ?? '儲存失敗'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="unit-attrs" data-testid="unit-attr-editor">
    <label class="ua-row">
      <span>番號</span>
      <input v-model="form.designation" type="text" maxlength="64" data-testid="ua-designation">
    </label>

    <label class="ua-row">
      <span>兵科</span>
      <select v-model="form.branch" data-testid="ua-branch">
        <option v-for="(text, key) in BRANCH_LABELS" :key="key" :value="key">{{ text }}</option>
      </select>
    </label>

    <label class="ua-row">
      <span>編制級別</span>
      <select v-model="form.unit_level" data-testid="ua-level">
        <option v-for="(text, key) in UNIT_LEVEL_LABELS" :key="key" :value="key">{{ text }}</option>
      </select>
    </label>

    <label class="ua-row">
      <span>現員人數</span>
      <input
        v-model.number="form.personnel_current"
        type="number"
        min="0"
        max="100000"
        data-testid="ua-personnel"
      >
    </label>

    <label class="ua-row">
      <span>當前戰力</span>
      <input
        v-model.number="form.current_strength"
        type="number"
        min="0"
        step="0.1"
        data-testid="ua-strength"
      >
    </label>

    <label class="ua-row">
      <span>滿編戰力</span>
      <input
        v-model.number="form.authorized_strength"
        type="number"
        min="0.1"
        step="0.1"
        data-testid="ua-authorized"
      >
    </label>

    <!-- 導出量：顯示但不可編（見模組說明第 1 點）。 -->
    <div class="ua-row ua-derived">
      <span>戰力比</span>
      <span data-testid="ua-effectiveness">
        {{ effectivenessPreview }}%
        <em>（由當前÷滿編算出，不可直接編輯）</em>
      </span>
    </div>
    <div v-if="result" class="ua-row ua-derived">
      <span>生效建制數</span>
      <span data-testid="ua-platforms">{{ result.platform_count }}</span>
    </div>

    <div class="ua-actions">
      <button :disabled="!dirty || busy" data-testid="ua-save" @click="save">
        {{ busy ? '儲存中…' : '儲存' }}
      </button>
      <button :disabled="!dirty || busy" class="ua-reset" @click="reset">還原</button>
    </div>

    <div v-if="err" class="ua-err" data-testid="ua-error">{{ err }}</div>
    <div v-else-if="note" class="ua-note" data-testid="ua-note">{{ note }}</div>
  </div>
</template>

<style scoped>
.unit-attrs {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.72rem;
}
.unit-attrs .ua-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.unit-attrs .ua-row > span:first-child {
  flex: 0 0 5.2rem;
  color: #94a3b8;
}
.unit-attrs input,
.unit-attrs select {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0.15rem 0.3rem;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  color: #e2e8f0;
  font-size: 0.72rem;
}
.unit-attrs .ua-derived em {
  color: #64748b;
  font-style: normal;
  font-size: 0.66rem;
}
.unit-attrs .ua-actions {
  display: flex;
  gap: 0.35rem;
  margin-top: 0.2rem;
}
.unit-attrs .ua-actions button {
  padding: 0.15rem 0.6rem;
  background: #1d4ed8;
  border: none;
  border-radius: 0.2rem;
  color: #fff;
  font-size: 0.72rem;
  cursor: pointer;
}
.unit-attrs .ua-actions button:disabled {
  background: #334155;
  color: #64748b;
  cursor: default;
}
.unit-attrs .ua-actions .ua-reset {
  background: #334155;
}
.unit-attrs .ua-err {
  color: #f87171;
  line-height: 1.35;
}
.unit-attrs .ua-note {
  color: #4ade80;
  line-height: 1.35;
}
</style>
