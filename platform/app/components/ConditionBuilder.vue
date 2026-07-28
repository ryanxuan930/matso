<script setup lang="ts">
// 遞迴 condition 建構器（GOAL#7）——鏡像 triggers.py 的 condition DSL；all/any 巢狀自我引用。
import {
  emptyCondition,
  type AllCondition,
  type AnyCondition,
  type Condition,
  type ConditionType,
  type FactionEliminatedCondition,
  type StrengthBelowCondition,
  type TimeCondition,
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
type FactionCond = FactionEliminatedCondition | StrengthBelowCondition | UnitInRegionCondition
type GroupCond = AllCondition | AnyCondition

const TYPE_OPTIONS: { label: string; value: ConditionType }[] = [
  { label: '到達 tick', value: 'time' },
  { label: '陣營被殲滅', value: 'faction_eliminated' },
  { label: '戰力低於', value: 'strength_below' },
  { label: '單位進入區域', value: 'unit_in_region' },
  { label: '全部成立（AND）', value: 'all' },
  { label: '任一成立（OR）', value: 'any' },
]

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
.cb-child { display: flex; gap: 0.4rem; align-items: flex-start; margin: 0.25rem 0; }
</style>
