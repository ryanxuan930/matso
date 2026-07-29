<script setup lang="ts">
/**
 * 座標查詢讀值（#10）——點地圖任一點顯示經緯度 + MGRS。
 *
 * 底下的 `.coord-readout` 基底樣式是「獨立浮在地圖上」的長相（絕對定位、外框、底色）；
 * 現在它一律裝在「座標查詢」小工具裡，由 cop.vue 的 `:deep(.fw .coord-readout)`
 * 整條中和掉（定位/外框/底色/內距全歸零）。兩條規則刻意分居兩檔：
 * 中和規則要選到祖先 `.fw`，那是本元件選不到的東西。
 */
defineProps<{
  point: { lng: number; lat: number } | null
  mgrs: string
}>()
</script>

<template>
<div class="coord-readout" data-testid="coord-readout">
  <div class="cr-hd">座標查詢 · 點地圖任一點</div>
  <template v-if="point">
    <div class="cr-row"><span>緯度</span><code>{{ point.lat.toFixed(5) }}</code></div>
    <div class="cr-row"><span>經度</span><code>{{ point.lng.toFixed(5) }}</code></div>
    <div class="cr-row"><span>MGRS</span><code data-testid="coord-mgrs">{{ mgrs }}</code></div>
  </template>
  <div v-else class="cr-hint">尚未點選</div>
</div>
</template>

<style scoped>
.coord-readout {
  position: absolute;
  top: 1rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 11;
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid #6b2a52;
  background: rgba(15, 23, 42, 0.95);
  color: #e2e8f0;
  font-size: 0.78rem;
  min-width: 12rem;
}
.coord-readout .cr-hd {
  color: #f472b6;
  font-size: 0.72rem;
  margin-bottom: 0.3rem;
}
.coord-readout .cr-row {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
}
.coord-readout .cr-row span {
  color: #94a3b8;
}
.coord-readout code {
  font-family: ui-monospace, monospace;
  color: #e2e8f0;
}
.coord-readout .cr-hint {
  color: #64748b;
}
</style>
