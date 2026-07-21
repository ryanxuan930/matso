<script setup lang="ts">
// AAR 儀表板（O8，SPEC §14）——時間軸 + 統計 + 敘事 + 匯出。
import {
  aarExportUrl,
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

async function load() {
  try {
    ;[replay.value, stats.value, report.value] = await Promise.all([
      aarReplay(sessionId),
      aarStats(sessionId),
      aarReport(sessionId),
    ])
  } catch (e) {
    error.value = `讀取 AAR 失敗：${(e as { message?: string }).message ?? e}`
  }
}
onMounted(load)
</script>

<template>
  <div class="aar" data-testid="aar-dashboard">
    <h1>行動後檢討（AAR） · {{ sessionId }}</h1>
    <p v-if="error" class="err">{{ error }}</p>

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
      <a :href="aarExportUrl(sessionId, 'json', false)" data-testid="export-json">JSON</a>
      <a :href="aarExportUrl(sessionId, 'csv', false)">CSV</a>
      <a :href="aarExportUrl(sessionId, 'csv', true)" data-testid="export-anon">CSV（匿名化）</a>
    </section>
  </div>
</template>

<style scoped>
.aar { max-width: 900px; margin: 1rem auto; padding: 0 1rem; }
section { border-top: 1px solid #ccc; padding-top: 0.5rem; margin-top: 1rem; }
.ok { color: #2a6; font-size: 0.8rem; }
.err { color: #c33; }
a { margin-right: 1rem; }
</style>
