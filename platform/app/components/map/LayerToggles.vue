<script setup lang="ts">
// 圖層開關 + 底圖切換 + 透明度/套疊順序/等高線間距（SPEC §13.2；#8/#9）。
import type { BasemapSource } from '~/composables/useMapStyle'

const hex = defineModel<boolean>('hex', { default: false })
const hillshade = defineModel<boolean>('hillshade', { default: false })
const contour = defineModel<boolean>('contour', { default: false })
const basemap = defineModel<string>('basemap', { default: 'offline' })
// #9 透明度乘數（0–1，key＝群）＋套疊順序（上→下）；#8 主/次等高線間距（m）。
const layerOpacity = defineModel<Record<string, number>>('layerOpacity', { default: () => ({}) })
const layerOrder = defineModel<string[]>('layerOrder', { default: () => ['hex', 'contour', 'hillshade'] })
const contourMajor = defineModel<number>('contourMajor', { default: 100 })
const contourMinor = defineModel<number>('contourMinor', { default: 50 })
// #9 座標網格：經緯度網格 / MGRS 標記 / 密度（度）。
const latlngGrid = defineModel<boolean>('latlngGrid', { default: false })
const mgrsGrid = defineModel<boolean>('mgrsGrid', { default: false })
const gridStepDeg = defineModel<number>('gridStepDeg', { default: 0.5 })
// 六角網格最細解析度上限 + 交戰範圍限制（km；降低運算量）。
const hexMaxRes = defineModel<number>('hexMaxRes', { default: 8 })
const hexLimitKm = defineModel<number>('hexLimitKm', { default: 0 })
// #6 日照視覺（晨昏/夜間色調）+ 一日時間。
const dayNight = defineModel<boolean>('dayNight', { default: false })
const timeOfDay = defineModel<number>('timeOfDay', { default: 12 })

// 地形陰影/等高線需 tileserver 提供瓦片；無 tileUrl 時停用並註記（避免 no-op 勾選誤導）。
withDefaults(
  defineProps<{ hillshadeEnabled?: boolean; contourEnabled?: boolean; basemaps?: BasemapSource[] }>(),
  { hillshadeEnabled: true, contourEnabled: true, basemaps: () => [] },
)

const OVERLAY_LABEL: Record<string, string> = { hex: '六角網格', contour: '等高線', hillshade: '地形陰影' }

function opacityOf(key: string): number {
  return layerOpacity.value[key] ?? 1
}
function setOpacity(key: string, e: Event) {
  layerOpacity.value = { ...layerOpacity.value, [key]: Number((e.target as HTMLInputElement).value) }
}
function move(key: string, dir: -1 | 1) {
  const arr = [...layerOrder.value]
  const i = arr.indexOf(key)
  const j = i + dir
  if (i < 0 || j < 0 || j >= arr.length) return
  ;[arr[i], arr[j]] = [arr[j]!, arr[i]!]
  layerOrder.value = arr
}
</script>

<template>
  <div class="toggles">
    <div class="title">底圖</div>
    <select v-if="basemaps.length > 1" v-model="basemap" data-testid="basemap-select" class="basemap">
      <option v-for="b in basemaps" :key="b.id" :value="b.id">{{ b.label }}</option>
    </select>
    <div v-else class="single">離線格線（無向量瓦片）</div>
    <div v-if="basemap !== 'offline'" class="op">
      <span>透明度</span>
      <input
        type="range" min="0" max="1" step="0.05" :value="opacityOf('basemap')"
        data-testid="opacity-basemap" @input="setOpacity('basemap', $event)"
      >
    </div>

    <div class="title spaced">圖層</div>
    <div class="lyr">
      <label>
        <input v-model="hex" data-testid="toggle-hex" type="checkbox">
        <span>六角網格</span>
      </label>
      <input
        type="range" min="0" max="1" step="0.05" :value="opacityOf('hex')"
        data-testid="opacity-hex" @input="setOpacity('hex', $event)"
      >
    </div>
    <div v-if="hex" class="intervals">
      <label>最細解析度
        <input v-model.number="hexMaxRes" type="number" min="3" max="9" data-testid="hex-max-res">
      </label>
      <label>交戰範圍(km)
        <input v-model.number="hexLimitKm" type="number" min="0" step="5" data-testid="hex-limit-km">
      </label>
    </div>
    <div class="lyr" :class="{ disabled: !hillshadeEnabled }">
      <label>
        <input v-model="hillshade" data-testid="toggle-hillshade" type="checkbox" :disabled="!hillshadeEnabled">
        <span>地形陰影<em v-if="!hillshadeEnabled"> · 需底圖</em></span>
      </label>
      <input
        type="range" min="0" max="1" step="0.05" :value="opacityOf('hillshade')"
        data-testid="opacity-hillshade" :disabled="!hillshadeEnabled" @input="setOpacity('hillshade', $event)"
      >
    </div>
    <div class="lyr" :class="{ disabled: !contourEnabled }">
      <label>
        <input v-model="contour" data-testid="toggle-contour" type="checkbox" :disabled="!contourEnabled">
        <span>等高線<em v-if="!contourEnabled"> · 需底圖</em></span>
      </label>
      <input
        type="range" min="0" max="1" step="0.05" :value="opacityOf('contour')"
        data-testid="opacity-contour" :disabled="!contourEnabled" @input="setOpacity('contour', $event)"
      >
    </div>
    <div v-if="contour && contourEnabled" class="intervals">
      <label>主等高線（粗）
        <input v-model.number="contourMajor" type="number" min="10" step="10" data-testid="contour-major"> m
      </label>
      <label>次等高線（細）
        <input v-model.number="contourMinor" type="number" min="10" step="10" data-testid="contour-minor"> m
      </label>
    </div>

    <div class="title spaced">座標網格</div>
    <div class="lyr">
      <label>
        <input v-model="latlngGrid" data-testid="toggle-latlng-grid" type="checkbox">
        <span>經緯度網格</span>
      </label>
    </div>
    <div class="lyr">
      <label>
        <input v-model="mgrsGrid" data-testid="toggle-mgrs-grid" type="checkbox">
        <span>MGRS 標記</span>
      </label>
    </div>
    <div v-if="latlngGrid || mgrsGrid" class="intervals">
      <label>密度（度）
        <input v-model.number="gridStepDeg" type="number" min="0.05" step="0.05" data-testid="grid-step">
      </label>
    </div>

    <div class="title spaced">日照</div>
    <div class="lyr">
      <label>
        <input v-model="dayNight" data-testid="toggle-daynight" type="checkbox">
        <span>晨昏/夜間色調</span>
      </label>
    </div>
    <div v-if="dayNight" class="intervals">
      <label>時間 {{ Math.floor(timeOfDay) }}:{{ String(Math.round((timeOfDay % 1) * 60)).padStart(2, '0') }}
        <input v-model.number="timeOfDay" type="range" min="0" max="24" step="0.5" data-testid="time-of-day">
      </label>
    </div>

    <div class="title spaced">疊放順序（上＝上層）</div>
    <ul class="order" data-testid="layer-order">
      <li v-for="(k, i) in layerOrder" :key="k">
        <span>{{ OVERLAY_LABEL[k] ?? k }}</span>
        <span class="ord-btns">
          <button :disabled="i === 0" :data-testid="`order-up-${k}`" @click="move(k, -1)">▲</button>
          <button :disabled="i === layerOrder.length - 1" :data-testid="`order-down-${k}`" @click="move(k, 1)">▼</button>
        </span>
      </li>
    </ul>
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
  min-width: 11rem;
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
.lyr {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.lyr.disabled label {
  color: #64748b;
}
.lyr input[type='range'],
.op input[type='range'] {
  width: 4.5rem;
  accent-color: #38bdf8;
}
.op {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  color: #94a3b8;
  font-size: 0.72rem;
}
label {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  cursor: pointer;
}
label em {
  font-style: normal;
  color: #64748b;
  font-size: 0.7rem;
}
.intervals {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.25rem 0.375rem;
  border-left: 2px solid #334155;
  color: #94a3b8;
  font-size: 0.72rem;
}
.intervals label {
  justify-content: space-between;
}
.intervals input[type='number'] {
  width: 3.5rem;
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.1rem 0.25rem;
}
.order {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.order li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.15rem 0.375rem;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
}
.ord-btns button {
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  font-size: 0.7rem;
  padding: 0 0.15rem;
}
.ord-btns button:disabled {
  color: #334155;
  cursor: default;
}
.ord-btns button:not(:disabled):hover {
  color: #e2e8f0;
}
</style>
