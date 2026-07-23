<script setup lang="ts">
// AAR 儀表板（O8，SPEC §14）——時間軸 + 統計 + 敘事 + 匯出。
import {
  aarExportDownload,
  aarReplay,
  aarReport,
  aarStats,
  type AarReplay,
  type AarReport,
  type AarStats,
} from '~/composables/useAar'

const route = useRoute()
const sessionId = route.params.id as string

const replay = ref<AarReplay | null>(null)
const stats = ref<AarStats | null>(null)
const report = ref<AarReport | null>(null)
const scrubTick = ref(0)
const error = ref('')
const loading = ref(true) // 後端彙整/敘事可能耗時 → 顯示載入動畫，避免誤判系統故障（補充 1）

async function load() {
  loading.value = true
  error.value = ''
  try {
    ;[replay.value, stats.value, report.value] = await Promise.all([
      aarReplay(sessionId),
      aarStats(sessionId),
      aarReport(sessionId),
    ])
  } catch (e) {
    error.value = `讀取 AAR 失敗：${(e as { message?: string }).message ?? e}`
  } finally {
    loading.value = false
  }
}
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
      <input v-model.number="scrubTick" type="range" min="0" :max="replay.max_tick" data-testid="scrub">
      <span>tick {{ scrubTick }}</span>
      <h3>書籤</h3>
      <ul>
        <li v-for="b in replay.bookmarks" :key="b.seq">
          <button @click="scrubTick = b.tick">tick {{ b.tick }} · {{ b.label }}</button>
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
</style>
