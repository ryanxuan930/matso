<script setup lang="ts">
// 北約符號選擇器（#25）：PrimeVue Select + 篩選 + 內嵌符號預覽（milsymbol）。
import { NATO_SYMBOLS } from '~/composables/useMapFeatures'
import { symbolDataUrl } from '~/composables/useMilsymbol'

const model = defineModel<string>({ default: '' }) // 15 碼 SIDC（空＝無符號）
const selectedLabel = computed(
  () => NATO_SYMBOLS.find((s) => s.sidc === model.value)?.label ?? '選北約符號…',
)
</script>

<template>
  <Select
    v-model="model"
    :options="NATO_SYMBOLS"
    option-value="sidc"
    option-label="label"
    filter
    reset-filter-on-hide
    filter-placeholder="搜尋符號…"
    placeholder="選北約符號…"
    scroll-height="16rem"
    class="nato-select"
    data-testid="nato-symbol-select"
  >
    <template #value>
      <span class="nato-opt">
        <img v-if="model" :src="symbolDataUrl(model)" class="nato-ico" alt="">
        <span v-else class="nato-dot">●</span>
        <span class="nato-lbl">{{ selectedLabel }}</span>
      </span>
    </template>
    <template #option="{ option }">
      <span class="nato-opt">
        <img v-if="option.sidc" :src="symbolDataUrl(option.sidc)" class="nato-ico" alt="">
        <span v-else class="nato-dot">●</span>
        <span class="nato-lbl">{{ option.label }}</span>
      </span>
    </template>
  </Select>
</template>

<style scoped>
.nato-select {
  width: 100%;
}
.nato-opt {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  min-width: 0;
}
.nato-ico {
  width: 22px;
  height: 22px;
  flex: none;
  object-fit: contain;
}
.nato-dot {
  color: #94a3b8;
  width: 22px;
  text-align: center;
  flex: none;
}
.nato-lbl {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
