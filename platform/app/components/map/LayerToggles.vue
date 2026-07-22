<script setup lang="ts">
// 圖層開關（SPEC §13.2）。O4.2 提供 hex/hillshade；天氣/通訊/偵測層於後續卡。
const hex = defineModel<boolean>('hex', { default: false })
const hillshade = defineModel<boolean>('hillshade', { default: false })
// 地形陰影需 tileserver 提供 hillshade 瓦片；無 tileUrl 時停用並註記（避免 no-op 勾選誤導）。
withDefaults(defineProps<{ hillshadeEnabled?: boolean }>(), { hillshadeEnabled: true })
</script>

<template>
  <div class="toggles">
    <div class="title">圖層</div>
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
}
.title {
  font-weight: 600;
  margin-bottom: 0.25rem;
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
