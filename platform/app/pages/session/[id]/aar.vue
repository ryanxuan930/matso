<script setup lang="ts">
// AAR 儀表板（O8，SPEC §14）——時間軸 + 統計 + 敘事 + 匯出。
import {
  aarExportDownload,
  aarReplay,
  aarReplayStates,
  aarReport,
  aarStats,
  type AarReplay,
  type AarReplayStates,
  type AarReport,
  type AarStats,
} from '~/composables/useAar'
import { useAarReplay } from '~/composables/useAarReplay'

const route = useRoute()
const sessionId = route.params.id as string

const replay = ref<AarReplay | null>(null)
const replayStates = ref<AarReplayStates | null>(null)
const stats = ref<AarStats | null>(null)
const report = ref<AarReport | null>(null)
const scrubTick = ref(0)
const error = ref('')
const loading = ref(true) // 後端彙整/敘事可能耗時 → 顯示載入動畫，避免誤判系統故障（補充 1）

async function load() {
  loading.value = true
  error.value = ''
  try {
    ;[replay.value, replayStates.value, stats.value, report.value] = await Promise.all([
      aarReplay(sessionId),
      aarReplayStates(sessionId),
      aarStats(sessionId),
      aarReport(sessionId),
    ])
  } catch (e) {
    error.value = `讀取 AAR 失敗：${(e as { message?: string }).message ?? e}`
  } finally {
    loading.value = false
  }
}
// 地圖重播：拖時間軸＝本地重算（見 composable 說明），播放/倍速在那裡。
const { playing, speed, unitsAt, toggle, stop } = useAarReplay(replayStates, scrubTick)
// 換 tick 時把該 tick 的事件列出來，讓「看到什麼」與「為什麼」對得起來。
// 重播視野：框住所有單位的基準位置。AAR 的單位常擠在數百公尺內，
// 用 MapCanvas 預設的台灣全景會什麼都看不到（實測就是一片空白）。
const fitBounds = computed<[[number, number], [number, number]] | null>(() => {
  const us = (replayStates.value?.units ?? []).filter(
    (u) => u.base_lat != null && u.base_lng != null,
  )
  if (!us.length) return null
  const lats = us.map((u) => u.base_lat as number)
  const lngs = us.map((u) => u.base_lng as number)
  const pad = 0.002 // 全部單位同點時仍要有框（fitBounds 不吃零面積）
  return [
    [Math.min(...lngs) - pad, Math.min(...lats) - pad],
    [Math.max(...lngs) + pad, Math.max(...lats) + pad],
  ]
})
const tickEvents = computed(
  () => replay.value?.frames.find((f) => f.tick === scrubTick.value)?.event_types ?? [],
)
onMounted(load)
</script>

<template>
  <div class="aar" data-testid="aar-dashboard">
    <header class="aar-bar">
      <button data-testid="aar-back-cop" @click="navigateTo(`/session/${sessionId}/cop`)">← 圖台</button>
      <h1>行動後檢討（AAR） · {{ sessionId }}</h1>
    </header>
    <p v-if="error" class="err" data-testid="aar-error">{{ error }}</p>

    <div v-if="loading" class="aar-loading" data-testid="aar-loading">
      <span class="spinner" />
      <div>
        <strong>正在彙整行動後檢討…</strong>
        <p>統計、時間軸與 AI 敘事報告產製中，資料量大時需稍候，請勿關閉。</p>
      </div>
    </div>

    <template v-else>
    <section v-if="stats" data-testid="aar-stats">
      <h2>統計</h2>
      <ul>
        <li>總事件：{{ stats.total_events }}</li>
        <li>交戰次數：{{ stats.engagements }}</li>
        <li>命中率：{{ (stats.hit_rate * 100).toFixed(0) }}%</li>
        <li>總戰損：{{ stats.total_damage }}</li>
        <li>護欄攔截：{{ stats.guardrail_blocks }}</li>
        <li v-for="(v, f) in stats.damage_by_faction" :key="f">{{ f }} 承受戰損：{{ v }}</li>
      </ul>
    </section>

    <section v-if="replay" data-testid="aar-timeline">
      <h2>時間軸重播（0–{{ replay.max_tick }}）</h2>
      <div class="scrub-row">
        <button class="play" data-testid="replay-play" :title="playing ? '暫停' : '播放'" @click="toggle">
          <i :class="playing ? 'pi pi-pause' : 'pi pi-play'" />
        </button>
        <input
          v-model.number="scrubTick"
          type="range"
          min="0"
          :max="replay.max_tick"
          data-testid="scrub"
          @mousedown="stop"
        >
        <span class="tickno" data-testid="replay-tick">tick {{ scrubTick }}</span>
        <select v-model.number="speed" class="speed" data-testid="replay-speed">
          <option :value="1">1×</option>
          <option :value="2">2×</option>
          <option :value="4">4×</option>
        </select>
      </div>

      <ClientOnly>
        <div class="replay-map" data-testid="replay-map">
          <MapCanvas :own-units="unitsAt" :current-tick="scrubTick" :fit-bounds="fitBounds" />
        </div>
        <template #fallback>
          <div class="replay-map loading">地圖載入中…</div>
        </template>
      </ClientOnly>
      <p class="mapnote">
        單位數 {{ unitsAt.length }}
        <span v-if="tickEvents.length"> · 本 tick 事件：{{ tickEvents.join('、') }}</span>
      </p>

      <h3>書籤</h3>
      <ul>
        <li v-for="b in replay.bookmarks" :key="b.seq">
          <button data-testid="bookmark" @click="stop(); scrubTick = b.tick">
            tick {{ b.tick }} · {{ b.label }}
          </button>
        </li>
      </ul>
    </section>

    <section v-if="report" data-testid="aar-report">
      <h2>AI 敘事報告
        <span :class="report.citations.valid ? 'ok' : 'err'">
          （引用查核：{{ report.citations.valid ? '全部有效' : '有捏造' }}）
        </span>
      </h2>
      <p>{{ report.summary }}</p>
      <p v-for="(p, i) in report.paragraphs" :key="i">
        {{ p.text }}
        <small v-if="p.cited_seqs.length">[引用 #{{ p.cited_seqs.join(', #') }}]</small>
      </p>
      <h3>教訓</h3>
      <ul><li v-for="(l, i) in report.lessons" :key="i">{{ l }}</li></ul>
    </section>

    <section>
      <h2>匯出</h2>
      <button class="exp" data-testid="export-json" @click="aarExportDownload(sessionId, 'json', false)">JSON</button>
      <button class="exp" @click="aarExportDownload(sessionId, 'csv', false)">CSV</button>
      <button class="exp" data-testid="export-anon" @click="aarExportDownload(sessionId, 'csv', true)">CSV（匿名化）</button>
    </section>
    </template>
  </div>
</template>

<style scoped>
.aar { max-width: 900px; margin: 0 auto; padding: 1rem; color: #e2e8f0; }
.aar-bar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.aar-bar h1 { font-size: 1.25rem; margin: 0; }
.aar-bar button { padding: 0.375rem 0.75rem; border: 1px solid #334155; border-radius: 0.25rem; background: #1e293b; color: #e2e8f0; cursor: pointer; }
.aar-bar button:hover { border-color: #2563eb; }
.aar-bar .help { margin-left: auto; font-size: 0.8125rem; color: #60a5fa; text-decoration: none; }
.aar-bar .help:hover { text-decoration: underline; }
section { border-top: 1px solid #1e293b; padding-top: 0.75rem; margin-top: 1rem; }
h2 { font-size: 0.9375rem; color: #94a3b8; }
.ok { color: #4ade80; font-size: 0.8rem; }
.err { color: #f87171; }
a { margin-right: 1rem; color: #60a5fa; }
.exp {
  margin-right: 0.75rem;
  padding: 0.3rem 0.75rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #60a5fa;
  cursor: pointer;
}
.exp:hover { border-color: #2563eb; }
.aar-loading {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-top: 2rem;
  padding: 1.5rem;
  border: 1px solid #1e293b;
  border-radius: 0.5rem;
  background: #0f172a;
  color: #94a3b8;
}
.aar-loading strong { color: #e2e8f0; }
.aar-loading p { margin: 0.25rem 0 0; font-size: 0.85rem; }
.spinner {
  flex: none;
  width: 2rem;
  height: 2rem;
  border: 3px solid #1e293b;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: aar-spin 0.8s linear infinite;
}
@keyframes aar-spin { to { transform: rotate(360deg); } }
.scrub-row { display: flex; align-items: center; gap: 0.6rem; }
.scrub-row input[type='range'] { flex: 1 1 auto; }
.scrub-row .play {
  flex: none;
  width: 2rem; height: 2rem;
  border: 1px solid #334155; border-radius: 50%;
  background: #1e293b; color: #e2e8f0; cursor: pointer;
}
.scrub-row .play:hover { border-color: #2563eb; }
.scrub-row .tickno { font-variant-numeric: tabular-nums; color: #94a3b8; font-size: 0.8rem; }
.scrub-row .speed {
  border: 1px solid #334155; border-radius: 0.25rem;
  background: #1e293b; color: #e2e8f0; font-size: 0.8rem; padding: 0.15rem 0.3rem;
}
/* 重播地圖：固定高度，MapCanvas 需要一個有尺寸的容器才初始化得起來。 */
.replay-map { position: relative; height: 22rem; margin-top: 0.6rem; border: 1px solid #1e293b; border-radius: 0.375rem; overflow: hidden; }
.replay-map.loading { display: flex; align-items: center; justify-content: center; color: #64748b; font-size: 0.85rem; }
.mapnote { margin: 0.35rem 0 0; font-size: 0.78rem; color: #64748b; }
</style>
