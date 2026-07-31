/**
 * AAR 統計區塊的**渲染**測試（WP-D6.2）。
 *
 * 跑法：`cd platform && npm test`（harness 說明見 aar-replay.test.ts）
 *
 * ## 為什麼要渲染，不只測純函式
 *
 * 這個 repo 的招牌病是「存得進去、讀得回來、測試全綠、實際沒效果」。
 * 統計口徑改了，後端也回了新欄位，但只要樣板少繫一行，畫面上仍是舊的那個數字——
 * 而純函式測試會一路綠燈。所以這裡**把 `aar.vue` 的統計區塊真的渲染出來**，
 * 斷言的是「使用者讀到的那串字」。樣板拆掉繫結 → 這裡會紅。
 *
 * 判斷式全部來自 `useAar` 的真函式（頁面 computed 也是呼叫它們），
 * 測試不自己算一份——自己算就變成「測我的測試」。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { registerHooks } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'

const APP_DIR = new URL('../app/', import.meta.url)
registerHooks({
  resolve(spec, ctx, next) {
    if (spec.startsWith('~/')) {
      return {
        url: pathToFileURL(fileURLToPath(new URL(`${spec.slice(2)}.ts`, APP_DIR))).href,
        shortCircuit: true,
      }
    }
    return next(spec, ctx)
  },
})

const { AAR_STATS_VERSION, aarHitRateLabel, aarRejectedCount, aarStatsVersionNote } =
  await import('~/composables/useAar')
type AarStats = import('~/composables/useAar').AarStats

/** 從頁面原始碼切出統計區塊——樣板是**唯一**事實來源，測試不另抄一份。 */
function statsTemplate(): string {
  const src = readFileSync(fileURLToPath(new URL('pages/session/[id]/aar.vue', APP_DIR)), 'utf8')
  const open = '<section v-if="stats" data-testid="aar-stats">'
  const start = src.indexOf(open)
  assert.notEqual(start, -1, 'aar.vue 找不到 data-testid="aar-stats" 區塊（改名了？）')
  const end = src.indexOf('</section>', start)
  assert.notEqual(end, -1, '統計區塊沒有結尾 </section>')
  return src.slice(start, end + '</section>'.length)
}

async function renderStats(stats: AarStats | null): Promise<string> {
  const app = createSSRApp({
    template: statsTemplate(),
    setup: () => ({
      stats,
      // 與 aar.vue 的 computed 同一組函式；樣板要什麼名字，這裡就給什麼名字。
      rejectedCount: aarRejectedCount(stats),
      hitRateLabel: aarHitRateLabel(stats),
      statsVersionNote: aarStatsVersionNote(stats),
    }),
  })
  return renderToString(app)
}

/** 取自真實 API 回應（session `e2e-orders`，2026-07-31 實測）。 */
function realStats(over: Partial<AarStats> = {}): AarStats {
  return {
    total_events: 63,
    engagements: 55,
    attempts: 55,
    engagements_fired: 48,
    hits: 42,
    hit_rate: 0.875,
    total_damage: 1315,
    guardrail_blocks: 1,
    damage_by_faction: { RED: 1315 },
    event_counts: { ENGAGEMENT_RESOLVED: 55 },
    stats_version: AAR_STATS_VERSION,
    ...over,
  }
}

test('三個交戰數字都上得了畫面（下令／實射／未射出）', async () => {
  const html = await renderStats(realStats())
  assert.match(html, /下令交火：55 次/)
  assert.match(html, /實際射出 48 次/)
  assert.match(html, /未射出 7 次/) // 55 − 48，畫面自己算得出被拒次數
})

test('命中率印的是實射分母，且分子分母都看得見', async () => {
  const html = await renderStats(realStats())
  assert.match(html, /命中率：88%/) // 42 ÷ 48；舊口徑分母 55 會印成 76%
  assert.match(html, /42 ÷ 48 次實射/) // 沒有這一行，讀者無從判斷 88% 是怎麼來的
})

test('一發未發時不印 0%——那會被讀成「打了但全沒中」', async () => {
  const html = await renderStats(realStats({ attempts: 7, engagements_fired: 0, hits: 0, hit_rate: 0 }))
  assert.match(html, /無一發射出/)
  assert.doesNotMatch(html, /命中率：0%/)
  assert.match(html, /未射出 7 次/)
})

test('口徑相符時不出現警告條（否則每一局都在喊狼來了）', async () => {
  const html = await renderStats(realStats())
  assert.doesNotMatch(html, /stats-version-note/)
})

test('舊口徑（封存於 D6.2 之前）在畫面上要看得出來', async () => {
  const html = await renderStats(realStats({ stats_version: undefined }))
  assert.match(html, /stats-version-note/)
  assert.match(html, /不可直接相比/)
})

// ---- 純判斷式（樣板繫結之外的另一半） ----

test('未射出次數不會是負的（後端若給了矛盾的數字也不能印負數）', () => {
  assert.equal(aarRejectedCount(realStats({ attempts: 3, engagements_fired: 9 })), 0)
  assert.equal(aarRejectedCount(null), 0)
})

test('口徑註記分得出「沒有版本」與「版本不對」', () => {
  assert.match(aarStatsVersionNote(realStats({ stats_version: undefined })), /舊口徑（v1）/)
  assert.match(aarStatsVersionNote(realStats({ stats_version: 99 })), /v99/)
  assert.equal(aarStatsVersionNote(realStats()), '')
})
