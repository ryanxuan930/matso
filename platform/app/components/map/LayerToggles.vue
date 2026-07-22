<script setup lang="ts">
// 圖層開關 + 底圖切換（SPEC §13.2）。O4.2 提供 hex/hillshade；底圖來源可抽換（#2）。
import type { BasemapSource } from '~/composables/useMapStyle'

const hex = defineModel<boolean>('hex', { default: false })
const hillshade = defineModel<boolean>('hillshade', { default: false })
const basemap = defineModel<string>('basemap', { default: 'offline' })
// 地形陰影需 tileserver 提供 hillshade 瓦片；無 tileUrl 時停用並註記（避免 no-op 勾選誤導）。
withDefaults(
  defineProps<{ hillshadeEnabled?: boolean; basemaps?: BasemapSource[] }>(),
  { hillshadeEnabled: true, basemaps: () => [] },
)
</script>

<template>
  <div class="toggles">
    <div class="title">底圖</div>
    <select v-if="basemaps.length > 1" v-model="basemap" data-testid="basemap-select" class="basemap">
      <option v-for="b in basemaps" :key="b.id" :value="b.id">{{ b.label }}</option>
    </select>
    <div v-else class="single">離線格線（無向量瓦片）</div>

    <div class="title spaced">圖層</div>
    <label>
      <input v-model="hex" data-testid="toggle-hex" type="checkbox">
      <span>六角網格</span>
    </label>
    <label :class="{ disabled: !hillshadeEnabled }">
      <input
        v-model="hillshade"
        data-testid="toggle-hillshade"
        type="checkbox"
        :disabled="!hillshadeEnabled"
      >
      <span>地形陰影<em v-if="!hillshadeEnabled"> · 需底圖</em></span>
    </label>
  </div>
</template>

<style scoped>
.toggles {
  position: absolute;
  top: 1rem;
  right: 1rem;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  background: rgba(15, 23, 42, 0.9);
  color: #e2e8f0;
  font-size: 0.8125rem;
  min-width: 9rem;
}
.title {
  font-weight: 600;
  margin-bottom: 0.25rem;
}
.title.spaced {
  margin-top: 0.5rem;
}
.basemap {
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.25rem 0.375rem;
}
.single {
  color: #64748b;
  font-size: 0.72rem;
}
label {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  cursor: pointer;
}
label.disabled {
  cursor: default;
  color: #64748b;
}
label em {
  font-style: normal;
  color: #64748b;
  font-size: 0.7rem;
}
</style>
