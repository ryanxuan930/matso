<script setup lang="ts">
/**
 * 座標查詢——**兩個方向**：
 *
 * 1. 點地圖任一點 → 顯示十進位度與 MGRS。
 * 2. **輸入座標 → 在地圖上標出來並飛過去**。
 *
 * 第 2 條是操作上真正常用的那一半：座標會從各種地方抄過來（無線電通報是度分秒、
 * 射擊諸元是 MGRS、電子文件是十進位度），過去畫面上只能「點」不能「查」，
 * 要在地圖上找一個報過來的點只能靠目視估算。
 *
 * 解析在 `useCoordParse`（純函式、有測試）——**這裡不猜**：解析失敗就把原因原話
 * 顯示出來，不要退回一個看起來合理的點。座標打錯在兵推裡的後果是火力落在別的地方。
 *
 * 底下的 `.coord-readout` 基底樣式是「獨立浮在地圖上」的長相（絕對定位、外框、底色）；
 * 現在它一律裝在「座標查詢」小工具裡，由 cop.vue 的 `:deep(.fw .coord-readout)`
 * 整條中和掉（定位/外框/底色/內距全歸零）。兩條規則刻意分居兩檔：
 * 中和規則要選到祖先 `.fw`，那是本元件選不到的東西。
 */
import { ref } from 'vue'
import { COORD_FORMAT_LABELS, parseCoordInput } from '~/composables/useCoordParse'

defineProps<{
  point: { lng: number; lat: number } | null
  mgrs: string
}>()

const emit = defineEmits<{ (e: 'locate', p: { lat: number; lng: number }): void }>()

const query = ref('')
const error = ref('')
const hit = ref('')
const warn = ref('')

function submit() {
  error.value = ''
  hit.value = ''
  warn.value = ''
  const result = parseCoordInput(query.value)
  if (!result.ok) {
    error.value = result.reason
    return
  }
  const { lat, lng, format, warning } = result.value
  warn.value = warning ?? ''
  // **回饋「我把它讀成什麼格式」**——輸入 `24 05 50` 時使用者以為是度分秒、
  // 系統讀成十進位度，不講出來就會變成一個安靜的誤解。
  hit.value = `依${COORD_FORMAT_LABELS[format]}判讀 → ${lat.toFixed(5)}, ${lng.toFixed(5)}`
  emit('locate', { lat, lng })
}
</script>

<template>
<div class="coord-readout" data-testid="coord-readout">
  <div class="cr-hd">座標查詢</div>

  <form class="cr-form" @submit.prevent="submit">
    <input
      v-model="query"
      data-testid="coord-input"
      placeholder="24.0972, 120.1705 ／ 51QTG1234567890 ／ 24°05'50&quot;N 120°10'14&quot;E"
      aria-label="座標輸入"
    >
    <button type="submit" data-testid="coord-locate">標定</button>
  </form>
  <div v-if="error" class="cr-err" data-testid="coord-error">{{ error }}</div>
  <template v-else-if="hit">
    <div class="cr-hit" data-testid="coord-hit">{{ hit }}</div>
    <div v-if="warn" class="cr-warn" data-testid="coord-warn">⚠ {{ warn }}</div>
  </template>

  <div class="cr-sep">或點地圖任一點</div>
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
  min-width: 15rem;
}
.coord-readout .cr-form {
  display: flex;
  gap: 0.35rem;
  margin-bottom: 0.35rem;
}
.coord-readout .cr-form input {
  flex: 1;
  min-width: 0;
  padding: 0.2rem 0.4rem;
  font-size: 0.72rem;
}
.coord-readout .cr-form button {
  flex: none;
  padding: 0.2rem 0.6rem;
  font-size: 0.72rem;
}
.coord-readout .cr-err {
  color: #f87171;
  margin-bottom: 0.35rem;
  line-height: 1.35;
}
.coord-readout .cr-hit {
  color: #4ade80;
  margin-bottom: 0.35rem;
}
.coord-readout .cr-warn {
  color: #fbbf24;
  margin-bottom: 0.35rem;
  line-height: 1.35;
}
.coord-readout .cr-sep {
  color: #64748b;
  font-size: 0.72rem;
  margin: 0.35rem 0 0.2rem;
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
