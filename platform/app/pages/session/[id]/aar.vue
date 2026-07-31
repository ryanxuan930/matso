<script setup lang="ts">
// AAR 儀表板（O8，SPEC §14）——時間軸 + 統計 + 敘事 + 匯出。
import {
  aarExportDownload,
  aarHitRateLabel,
  aarRejectedCount,
  aarReplay,
  aarReplayStates,
  aarReport,
  aarStats,
  aarStatsVersionNote,
  auditCitations,
  type AarReplay,
  type AarReplayStates,
  type AarReport,
  type AarStats,
} from '~/composables/useAar'
import { useAarReplay } from '~/composables/useAarReplay'
import { unitLevelLabel } from '~/composables/useUnits'

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
// 交戰次數的三個數字與口徑註記（WP-D6.2）。判斷式放在 `useAar` 而不是這裡：
// 樣板繫結沒有測試接得上，搬成純函式才驗得了「畫面上那個數字是不是對的」。
const rejectedCount = computed(() => aarRejectedCount(stats.value))
const hitRateLabel = computed(() => aarHitRateLabel(stats.value))
const statsVersionNote = computed(() => aarStatsVersionNote(stats.value))
// 地圖重播：拖時間軸＝本地重算（見 composable 說明），播放/倍速在那裡。
const { playing, speed, unitsAt, rosterAt, toggle, stop } = useAarReplay(replayStates, scrubTick)
/**
 * 引用查核明細（D-aar）。**沒有它，「有捏造」三個字等於把整份報告作廢**
 * ——統裁看不出是哪一段被 AI 編出來，只能整份不採信。
 */
const citeAudit = computed(() => auditCitations(report.value))
/** 書籤的 seq → tick：讓報告裡的引用可以直接跳到時間軸上那一格（引用是 seq，滑桿吃 tick）。 */
const bookmarkTickBySeq = computed(() => {
  const m = new Map<number, number>()
  for (const b of replay.value?.bookmarks ?? []) m.set(b.seq, b.tick)
  return m
})
function gotoSeq(seq: number): void {
  const t = bookmarkTickBySeq.value.get(seq)
  if (t === undefined) return
  stop()
  scrubTick.value = t
}
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
      <!-- 口徑註記：舊局的數字是舊定義算出來的，擺在一起比會得到錯誤結論。 -->
      <p v-if="statsVersionNote" class="stats-ver" data-testid="stats-version-note">
        ⚠ {{ statsVersionNote }}
      </p>
      <ul>
        <li>總事件：{{ stats.total_events }}</li>
        <li>交戰事件：{{ stats.engagements }}（含營級以上聚合交戰）</li>
        <li data-testid="stat-attempts">
          下令交火：{{ stats.attempts }} 次 · 實際射出 {{ stats.engagements_fired }} 次 ·
          未射出 {{ rejectedCount }} 次（超射程／無彈／無視線／ROE）
        </li>
        <li data-testid="stat-hit-rate">
          命中率：{{ hitRateLabel }}
          <small>（{{ stats.hits }} ÷ {{ stats.engagements_fired }} 次實射；未射出者不計入分母）</small>
          <!-- 這一行不是免責聲明，是讀數說明：齊射/聯合兵種走期望值裁決，
               「命中」的判準是該次交戰有沒有造成戰力損失。以這兩條路徑為主的局
               本來就會逼近 100%，不寫出來會被當成「我方神準」。 -->
          <small class="caveat">
            齊射與聯合兵種以期望值裁決，「命中」＝該次交戰造成戰力損失，非彈著命中率。
          </small>
        </li>
        <li>總戰損：{{ stats.total_damage }}（全場雙方相加）</li>
        <li>護欄攔截：{{ stats.guardrail_blocks }}</li>
        <li v-for="(v, f) in stats.damage_by_faction" :key="f">{{ f }} 承受戰損：{{ v }}</li>
      </ul>
      <!-- 事件類型分布：後端一直有回 `event_counts`，畫面卻只顯示總數。
           「這場推演到底發生了哪些種類的事」是檢討的第一個問題，總數答不了。 -->
      <template v-if="stats.event_counts && Object.keys(stats.event_counts).length">
        <h3>事件類型分布</h3>
        <ul class="evt-counts" data-testid="aar-event-counts">
          <li v-for="(n, t) in stats.event_counts" :key="t">{{ t }}：{{ n }}</li>
        </ul>
      </template>
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

      <!-- 部隊狀況表：地圖只表達得了位置與效能%，戰力點（人員/平台數）沒有欄位可放
           （見 useAarReplay 的 AarReplayRosterRow 說明），只能落在這裡。
           另外，沒有座標紀錄的單位畫不到圖上，唯有這張表交代得出它們的去向。 -->
      <h3>本 tick 部隊狀況</h3>
      <div class="roster-wrap">
        <table class="roster" data-testid="replay-roster">
          <thead>
            <tr>
              <th>番號</th><th>陣營</th><th>編制</th><th>作戰效能</th><th>戰力</th><th>圖上</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rosterAt" :key="r.id" data-testid="roster-row">
              <td class="desig">{{ r.designation }}</td>
              <td>{{ r.faction }}</td>
              <td>{{ unitLevelLabel(r.unitLevel) }}</td>
              <td>
                {{ Math.round(r.health) }}%
                <!-- 效能 0 不等於被殲滅：效能曲線在戰力比 0.30 就歸零，
                     那支部隊還在戰場上、還會被打。這行就是為了擋掉這個必然的誤讀。 -->
                <small v-if="r.health <= 0 && (r.strength ?? 0) > 0" class="warn">
                  戰鬥不能（仍在戰場）
                </small>
              </td>
              <td>
                <template v-if="r.strength != null">
                  {{ Math.round(r.strength) }}<template v-if="r.authorizedStrength">
                    / {{ Math.round(r.authorizedStrength) }}</template>
                </template>
                <template v-else>—</template>
              </td>
              <td>{{ r.onMap ? '是' : '無位置紀錄' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="mapnote">
        戰力欄為「當前／滿編」戰力點；顯示「—」表示帳本沒有記錄該單位的戰力後態
        （個體交戰只記作戰效能%，戰力點僅聚合交戰會記）。
      </p>

      <h3>書籤</h3>
      <ul>
        <li v-for="b in replay.bookmarks" :key="b.seq">
          <button data-testid="bookmark" @click="stop(); scrubTick = b.tick">
            <!-- 顯示 seq：敘事報告的引用也是 seq，兩邊對得起來才查得下去。 -->
            #{{ b.seq }} · tick {{ b.tick }} · {{ b.label }}
          </button>
        </li>
      </ul>
    </section>

    <section v-if="report" data-testid="aar-report">
      <h2>AI 敘事報告
        <span :class="citeAudit.total ? 'err' : 'ok'" data-testid="citation-verdict">
          （引用查核：{{ citeAudit.total ? `查無事件 ${citeAudit.total} 筆` : '全部有效' }}）
        </span>
      </h2>
      <!-- 捏造引用清單：只說「有捏造」而不說是哪幾條，等於整份報告作廢卻無從查證。 -->
      <p v-if="citeAudit.total" class="cite-warn" data-testid="citation-warning">
        下列引用在推演帳本中查無對應事件（AI 捏造），標記段落之敘述未經帳本佐證，不得採信：
        <span v-for="s in citeAudit.invalidSorted" :key="s" class="bad-seq">#{{ s }}</span>
        <span v-if="citeAudit.orphans.length" class="orphan">
          （其中 #{{ citeAudit.orphans.join('、#') }} 未出現在任何段落，請回報系統管理員）
        </span>
      </p>
      <p>{{ report.summary }}</p>
      <p
        v-for="(p, i) in report.paragraphs"
        :key="i"
        :class="{ fabricated: citeAudit.byParagraph.has(i) }"
        :data-testid="citeAudit.byParagraph.has(i) ? 'para-fabricated' : 'para'"
      >
        {{ p.text }}
        <small v-if="p.cited_seqs.length">
          [引用<template v-for="(s, j) in p.cited_seqs" :key="j"><template v-if="j">,</template>
            <button
              class="cite"
              :class="{ bad: citeAudit.invalid.has(s) }"
              :disabled="!bookmarkTickBySeq.has(s)"
              :title="citeAudit.invalid.has(s) ? '帳本查無此事件（捏造）' : bookmarkTickBySeq.has(s) ? '跳至該事件所在 tick' : '該事件不在書籤中'"
              @click="gotoSeq(s)"
            >#{{ s }}</button></template>]
        </small>
        <small v-if="citeAudit.byParagraph.has(i)" class="bad">
          ← 本段引用 #{{ citeAudit.byParagraph.get(i)!.join('、#') }} 帳本查無此事件
        </small>
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
/* 部隊狀況表：長局單位多，容器自己捲，不要讓整頁橫向捲動。 */
.roster-wrap { max-height: 18rem; overflow: auto; border: 1px solid #1e293b; border-radius: 0.375rem; }
.roster { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
.roster th, .roster td { padding: 0.25rem 0.5rem; text-align: left; white-space: nowrap; }
.roster thead th { position: sticky; top: 0; background: #0f172a; color: #94a3b8; font-weight: 500; }
.roster tbody tr:nth-child(even) { background: #0f172a80; }
.roster .desig { color: #e2e8f0; font-weight: 500; }
.roster .warn { margin-left: 0.3rem; color: #f59e0b; }
.evt-counts { columns: 2; font-size: 0.85rem; }
/* 讀數說明：比數字暗一階、獨立成行——要讀得到，但不跟數字搶。 */
.caveat { display: block; color: #64748b; font-size: 0.75rem; }
/* 口徑不符：黃色警示條——不是錯誤，但看數字之前必須先讀到。 */
.stats-ver {
  margin: 0 0 0.5rem;
  padding: 0.35rem 0.6rem;
  border-left: 3px solid #f59e0b;
  background: #78350f20;
  font-size: 0.82rem;
  color: #fbbf24;
}
/* 捏造引用：紅字 + 左側紅槓，掃一眼就知道哪一段不能念。 */
.cite-warn { padding: 0.4rem 0.6rem; border-left: 3px solid #f87171; background: #7f1d1d20; font-size: 0.85rem; }
.cite-warn .bad-seq { margin-left: 0.35rem; color: #f87171; font-variant-numeric: tabular-nums; }
.cite-warn .orphan { display: block; margin-top: 0.25rem; color: #94a3b8; font-size: 0.78rem; }
p.fabricated { border-left: 3px solid #f87171; padding-left: 0.6rem; }
.cite {
  padding: 0 0.1rem;
  border: 0;
  background: transparent;
  color: #60a5fa;
  font: inherit;
  cursor: pointer;
}
.cite:disabled { color: #64748b; cursor: default; }
.cite.bad, small.bad { color: #f87171; text-decoration: line-through; }
small.bad { margin-left: 0.35rem; text-decoration: none; }
</style>
