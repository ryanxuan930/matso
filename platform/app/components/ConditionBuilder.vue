<script setup lang="ts">
// 遞迴 condition 建構器（GOAL#7）——鏡像 triggers.py 的 condition DSL；all/any 巢狀自我引用。
import {
  CONDITION_LABELS,
  emptyCondition,
  type AfterTicksOfCondition,
  type AllCondition,
  type AnyCondition,
  type Condition,
  type ConditionType,
  type ContactEstablishedCondition,
  type FactionEliminatedCondition,
  type HeldForCondition,
  type NotCondition,
  type StrengthBelowCondition,
  type TimeCondition,
  type UnitInPolygonCondition,
  type UnitInRegionCondition,
} from '~/composables/useConditionDsl'

// 遞迴元件需具名（<ConditionBuilder> 在自身模板引用）。
defineOptions({ name: 'ConditionBuilder' })

const props = withDefaults(
  defineProps<{ modelValue: Condition; factions: string[]; depth?: number }>(),
  { depth: 0 },
)
const emit = defineEmits<{ 'update:modelValue': [Condition] }>()

// 需要陣營的三型、含 of 的群組型別別名（供 cast 用）。
type FactionCond =
  | FactionEliminatedCondition
  | StrengthBelowCondition
  | UnitInRegionCondition
  | UnitInPolygonCondition
  | ContactEstablishedCondition
type GroupCond = AllCondition | AnyCondition
/** `of` 是**單一**條件的兩型（與 all/any 的陣列不同——後端 validate 也分開處理）。 */
type WrapperCond = HeldForCondition | NotCondition

/**
 * 型別選單**由 `CONDITION_LABELS` 導出**，不再手抄一份。
 *
 * 手抄的那份只列了 6 種，而後端支援 12 種——缺的正好包括 `manual`
 * （白軍控制台的扣發按鈕唯一的來源）。少一種在畫面上看不出來，
 * 只會表現成「這個狀況寫不出來」，而作者不會知道是編輯器的問題還是系統不支援。
 */
const TYPE_OPTIONS: { label: string; value: ConditionType }[] = (
  Object.keys(CONDITION_LABELS) as ConditionType[]
).map((value) => ({ value, label: CONDITION_LABELS[value] }))

const firstFaction = () => props.factions[0] ?? ''

function setType(t: ConditionType) {
  emit('update:modelValue', emptyCondition(t, firstFaction()))
}
function setAtTick(v: number) {
  emit('update:modelValue', { type: 'time', at_tick: v ?? 0 })
}
function setFaction(f: string) {
  emit('update:modelValue', { ...(props.modelValue as FactionCond), faction: f })
}
function setValue(v: number) {
  emit('update:modelValue', { ...(props.modelValue as StrengthBelowCondition), value: v ?? 0 })
}
function setBbox(i: number, v: number) {
  const cur = props.modelValue as UnitInRegionCondition
  const bbox = [...cur.bbox] as [number, number, number, number]
  bbox[i] = v ?? 0
  emit('update:modelValue', { ...cur, bbox })
}
function setChild(i: number, c: Condition) {
  const cur = props.modelValue as GroupCond
  const of = cur.of.slice()
  of[i] = c
  emit('update:modelValue', { ...cur, of })
}
function addChild() {
  const cur = props.modelValue as GroupCond
  emit('update:modelValue', { ...cur, of: [...cur.of, emptyCondition('time', firstFaction())] })
}
function removeChild(i: number) {
  const cur = props.modelValue as GroupCond
  emit('update:modelValue', { ...cur, of: cur.of.filter((_, j) => j !== i) })
}

/** `ticks` 只有 `held_for` 與 `after_ticks_of` 有——`not` 沒有，故不併進同一個 cast。 */
function setTicks(v: number) {
  const cur = props.modelValue as HeldForCondition | AfterTicksOfCondition
  emit('update:modelValue', { ...cur, ticks: Math.max(1, v ?? 1) })
}
function setEvent(v: string | undefined) {
  emit('update:modelValue', { ...(props.modelValue as AfterTicksOfCondition), event: v ?? '' })
}
function setOfFaction(v: string) {
  emit('update:modelValue', { ...(props.modelValue as ContactEstablishedCondition), of: v })
}
/** `held_for` / `not` 的內層條件（單一，不是陣列）。 */
function setInner(c: Condition) {
  emit('update:modelValue', { ...(props.modelValue as WrapperCond), of: c })
}
/**
 * 多邊形以「每行一組 lng,lat」編輯。
 *
 * 編輯器沒有地圖，硬做點選要先端一張圖進來——那是另一張卡。文字輸入至少讓作者
 * **寫得出來**（現在是完全寫不出來），而且座標可以從 COP 的座標工具複製過來。
 * 解析失敗的行直接略過而不是清空整份——打字打到一半不該把已輸入的頂點吃掉。
 */
function polygonText(c: UnitInPolygonCondition): string {
  return (c.polygon ?? []).map(([lng, lat]) => `${lng},${lat}`).join('\n')
}
function setPolygon(raw: string | undefined) {
  const pts: Array<[number, number]> = []
  for (const line of (raw ?? '').split('\n')) {
    const [a, b] = line.split(',').map((s) => Number(s.trim()))
    if (Number.isFinite(a) && Number.isFinite(b)) pts.push([a as number, b as number])
  }
  emit('update:modelValue', { ...(props.modelValue as UnitInPolygonCondition), polygon: pts })
}

const BBOX_LABELS = ['最小經度', '最小緯度', '最大經度', '最大緯度']
</script>

<template>
  <div class="cb" :style="{ marginLeft: depth ? '0.75rem' : '0' }" data-testid="condition-builder">
    <Select
      :model-value="modelValue.type"
      :options="TYPE_OPTIONS"
      option-label="label"
      option-value="value"
      size="small"
      data-testid="cb-type"
      @update:model-value="setType"
    />

    <template v-if="modelValue.type === 'time'">
      <label class="cb-field">tick ≥
        <InputNumber
          :model-value="(modelValue as TimeCondition).at_tick"
          :min="0"
          size="small"
          @update:model-value="setAtTick"
        />
      </label>
    </template>

    <template v-else-if="modelValue.type === 'faction_eliminated'">
      <Select
        :model-value="(modelValue as FactionEliminatedCondition).faction"
        :options="factions"
        size="small"
        placeholder="陣營"
        @update:model-value="setFaction"
      />
    </template>

    <template v-else-if="modelValue.type === 'strength_below'">
      <Select
        :model-value="(modelValue as StrengthBelowCondition).faction"
        :options="factions"
        size="small"
        placeholder="陣營"
        @update:model-value="setFaction"
      />
      <label class="cb-field">戰力 &lt;
        <InputNumber
          :model-value="(modelValue as StrengthBelowCondition).value"
          :min="0"
          size="small"
          @update:model-value="setValue"
        />
      </label>
    </template>

    <template v-else-if="modelValue.type === 'unit_in_region'">
      <Select
        :model-value="(modelValue as UnitInRegionCondition).faction"
        :options="factions"
        size="small"
        placeholder="陣營"
        @update:model-value="setFaction"
      />
      <label v-for="(lbl, bi) in BBOX_LABELS" :key="bi" class="cb-field">{{ lbl }}
        <InputNumber
          :model-value="(modelValue as UnitInRegionCondition).bbox[bi]"
          :max-fraction-digits="6"
          size="small"
          @update:model-value="(v: number) => setBbox(bi, v)"
        />
      </label>
    </template>

    <template v-else-if="modelValue.type === 'unit_in_polygon'">
      <Select
        :model-value="(modelValue as UnitInPolygonCondition).faction"
        :options="factions"
        size="small"
        placeholder="陣營"
        @update:model-value="setFaction"
      />
      <label class="cb-field cb-wide">頂點（每行 lng,lat）
        <Textarea
          :model-value="polygonText(modelValue as UnitInPolygonCondition)"
          rows="3"
          data-testid="cb-polygon"
          placeholder="120.30,23.70"
          @update:model-value="setPolygon"
        />
      </label>
    </template>

    <template v-else-if="modelValue.type === 'contact_established'">
      <Select
        :model-value="(modelValue as ContactEstablishedCondition).faction"
        :options="factions"
        size="small"
        placeholder="觀測方"
        @update:model-value="setFaction"
      />
      <span class="cb-field">偵測到</span>
      <Select
        :model-value="(modelValue as ContactEstablishedCondition).of"
        :options="factions"
        size="small"
        placeholder="被觀測方"
        data-testid="cb-contact-of"
        @update:model-value="setOfFaction"
      />
    </template>

    <!-- `manual` 沒有任何欄位——它的意思就是「等白軍按鈕」。
         這一格特別說明，否則作者會以為是介面壞了。 -->
    <template v-else-if="modelValue.type === 'manual'">
      <span class="cb-note" data-testid="cb-manual-note">
        由白軍在控制台按「扣發」時成立——不會自己觸發。
      </span>
    </template>

    <template v-else-if="modelValue.type === 'after_ticks_of'">
      <label class="cb-field">事件
        <InputText
          :model-value="(modelValue as AfterTicksOfCondition).event"
          size="small"
          placeholder="MSEL 條目 id 或事件型別"
          data-testid="cb-after-event"
          @update:model-value="setEvent"
        />
      </label>
      <label class="cb-field">之後
        <InputNumber
          :model-value="(modelValue as AfterTicksOfCondition).ticks"
          :min="1"
          size="small"
          @update:model-value="setTicks"
        />
        tick
      </label>
    </template>

    <template v-else-if="modelValue.type === 'held_for' || modelValue.type === 'not'">
      <label v-if="modelValue.type === 'held_for'" class="cb-field">持續
        <InputNumber
          :model-value="(modelValue as HeldForCondition).ticks"
          :min="1"
          size="small"
          @update:model-value="setTicks"
        />
        tick
      </label>
      <!-- 內層是**單一**條件（不是陣列）——與 all/any 分開處理，後端 validate 也是。 -->
      <div class="cb-children">
        <ConditionBuilder
          :model-value="(modelValue as HeldForCondition | NotCondition).of"
          :factions="factions"
          :depth="depth + 1"
          data-testid="cb-inner"
          @update:model-value="setInner"
        />
      </div>
    </template>

    <template v-else>
      <div class="cb-children">
        <div v-for="(child, ci) in (modelValue as GroupCond).of" :key="ci" class="cb-child" data-testid="cb-child">
          <ConditionBuilder
            :model-value="child"
            :factions="factions"
            :depth="depth + 1"
            @update:model-value="(c: Condition) => setChild(ci, c)"
          />
          <Button size="small" text severity="danger" @click="removeChild(ci)">✕</Button>
        </div>
        <Button size="small" text data-testid="cb-add-child" @click="addChild">＋ 子條件</Button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cb { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
.cb-field { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.8125rem; color: #94a3b8; }
.cb-children { width: 100%; border-left: 2px solid #1e293b; padding-left: 0.5rem; }
.cb-wide { width: 100%; align-items: flex-start; }
.cb-note { font-size: 0.8125rem; color: #94a3b8; }
.cb-child { display: flex; gap: 0.4rem; align-items: flex-start; margin: 0.25rem 0; }
</style>
