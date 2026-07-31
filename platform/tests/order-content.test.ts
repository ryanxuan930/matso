/**
 * 令與申請單的**內容**渲染（UI-P3）——火協鏈在畫面上是不是連得起來。
 *
 * 跑法：`cd platform && node --test tests/order-content.test.ts`
 *
 * 三個欄位（`OrderResponse.payload`、`.precheck`、`RequestView.params`）的資料
 * 一直都在回應體裡，**畫面上一個字都沒有**。加起來的後果是火協鏈
 * （申請 → 核准 → 掛單射擊 → 檢討）在畫面上是斷的：
 * 核覆者不知道自己核的是哪個座標、指令列看不出打了哪裡、預檢不可行看不出是哪一關沒過。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { registerHooks } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

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

globalThis.$fetch = (() => Promise.resolve({})) as never
const { orderPayloadSummary, requestParamsSummary, failedCheckLabels } = await import(
  '~/composables/useLabels'
)
function readSrc(rel: string): string {
  return readFileSync(fileURLToPath(new URL(rel, APP_DIR)), 'utf8')
}

test('火力任務要說出打哪裡、幾發、什麼彈', () => {
  const s = orderPayloadSummary('FIRE_MISSION', {
    target_lat: 23.7,
    target_lng: 120.3,
    rounds: 6,
    ammo_type: 'SMOKE',
    ttl_ticks: 30,
  })
  assert.match(s, /落點 23\.70000, 120\.30000/)
  assert.match(s, /6 發/)
  assert.match(s, /發煙/)
  assert.match(s, /時效 30 tick/)
})

test('移動要說出往哪裡、怎麼走', () => {
  const s = orderPayloadSummary('MOVE', {
    to_lat: 24.1,
    to_lng: 121.0,
    mobility_profile: 'TRACKED',
    tempo: 'FORCED_MARCH',
  })
  assert.match(s, /往 24\.10000, 121\.00000/)
  assert.match(s, /履帶/)
  assert.match(s, /強行軍/)
})

test('隊形令要說出隊形與行軍間隔', () => {
  const s = orderPayloadSummary('FORMATION', { formation: 'COLUMN', column_spacing_km: 0.35 })
  assert.match(s, /縱隊/)
  assert.match(s, /間隔 0\.35 km/)
})

test('缺欄位就略過，不印 undefined 也不硬吐 JSON', () => {
  assert.equal(orderPayloadSummary('FIRE_MISSION', {}), '')
  assert.equal(orderPayloadSummary('FIRE_MISSION', null), '')
  // 認不得的令型回空字串——指令列一行塞一坨 JSON 比留白更難讀。
  assert.equal(orderPayloadSummary('SOMETHING_NEW', { a: 1 }), '')
})

test('申請單要說出目標座標——核覆者按核准時得知道自己核的是什麼', () => {
  const s = requestParamsSummary({ target_lat: 23.5, target_lng: 120.9 })
  assert.match(s, /目標 23\.50000, 120\.90000/)
})

test('申請單認不得的鍵要原樣列出，不得丟掉', () => {
  // params 是開放結構。丟掉未知鍵等於讓核覆者在資訊不全的情況下簽字。
  const s = requestParamsSummary({ target_lat: 1, target_lng: 2, priority: 'URGENT', rounds: 12 })
  assert.match(s, /priority: URGENT/)
  assert.match(s, /rounds: 12/)
})

test('預檢不可行要說出是哪一關沒過', () => {
  const labels = failedCheckLabels({
    checks: [
      { name: 'line_of_sight', passed: true },
      { name: 'ammo', passed: false, detail: '彈藥不足' },
      { name: 'trajectory', passed: false },
    ],
  })
  assert.equal(labels.length, 2)
  assert.match(labels[0]!, /彈藥不足/)
  assert.ok(!labels.some((l) => l.includes('line_of_sight')), '通過的關卡不該列出來')
})

test('兩個面板都真的接上了 renderer', () => {
  // 純函式對了但沒人呼叫，就是本 repo 最常見的那種綠燈。
  const orders = readSrc('components/cop/OrdersPanel.vue')
  assert.match(orders, /data-testid="order-payload"/)
  assert.match(orders, /data-testid="order-failed-checks"/)
  assert.match(orders, /orderPayloadSummary\(o\.order_type/)
  assert.match(orders, /v-if="payloadText\(o\)"/, '樣板沒有真的呼叫')

  const c2 = readSrc('components/cop/C2Panel.vue')
  assert.match(c2, /data-testid="c2-request-params"/)
  assert.match(c2, /requestParamsSummary\(r\.params/)
  assert.match(c2, /v-if="paramsText\(r\)"/, '樣板沒有真的呼叫')
})

test('AAR 頁面真的接上了任務時間軸', () => {
  /**
   * 抓的病：`/aar/missions` 是 67 條業務端點裡**唯一一條完全沒接的**——
   * curl 就有真資料，畫面上零蹤影。任務級下令是這個系統最貴的功能，
   * 而「執行得好不好」過去沒有任何量化畫面。
   */
  const page = readSrc('pages/session/[id]/aar.vue')
  assert.match(page, /aarMissions\(sessionId\)/, '沒有真的去拉資料')
  assert.match(page, /data-testid="aar-missions"/)
  assert.match(page, /data-testid="aar-mission-leg"/)
  // 評估失敗次數要看得見——一道壞任務不該拖垮整局，但也不該無聲無息。
  assert.match(page, /data-testid="aar-mission-errors"/)

  const composable = readSrc('composables/useAar.ts')
  assert.match(composable, /aar\/missions/)
  // 型別要走契約生成，不再手抄——那正是 P4 點名的漂移。
  assert.match(composable, /components\['schemas'\]\['MissionTimeline'\]/)
})

test('還在進行中的階段不編一個時長出來', () => {
  // `to_tick`/`duration_ticks` 為 null ＝局結束時仍在該階段。
  // 印 0 或空白都會被讀成「這一階段瞬間完成」。
  const page = readSrc('pages/session/[id]/aar.vue')
  assert.match(page, /leg\.duration_ticks != null/)
  assert.match(page, /（仍在此階段）/)
})

test('AAR 型別走契約生成，不再手抄 interface（G5）', () => {
  /**
   * 抓的病：`useAar.ts` 曾經手寫 `AarReplay`／`AarReplayStates`／`AarReport`／`AarStats`
   * 四份 interface，而契約與 `types/api.ts` 早就有（或本輪補上）對應的定義。
   * 契約先行做到一半、前端沒接：後端改個欄位名，型別檢查照樣過，畫面靜默變空白。
   *
   * ⚠ `AarStats` 是**刻意的例外**：它從契約推導但把 `stats_version` 放寬成 optional，
   * 因為欄位缺席正是要示警的情況（前端新、後端容器舊）。這條同時釘住那個例外，
   * 免得後人「順手統一」把它改回必填而讓版本落差偵測失效。
   */
  const src = readSrc('composables/useAar.ts')
  for (const name of ['AarReplay', 'AarReplayStates', 'AarReport']) {
    assert.match(
      src,
      new RegExp(`export type ${name} = components\\['schemas'\\]\\['${name}'\\]`),
      `${name} 沒有走契約生成型別`,
    )
  }
  assert.ok(!/export interface Aar/.test(src), 'useAar.ts 仍有手寫的 Aar* interface')
  assert.match(
    src,
    /Omit<components\['schemas'\]\['AarStats'\], 'stats_version'> & \{\s*stats_version\?: number/,
    'AarStats 的刻意放寬不見了——版本落差偵測會失效',
  )
})

test('AI 失控保護的餘量要看得見（P5）', async () => {
  /**
   * 抓的病：runaway 守衛（O11.8）觸發時 AI worker **直接停止決策**，
   * 而畫面上只會表現成「AI 忽然不動了」。白軍要的是**事前**看得到快到上限，
   * 不是事後翻 log 找 runaway 警告。
   *
   * 過去 `total_submitted` 後端有送、前端型別也有，但 `describeAiStatus` 沒帶出來，
   * 而**上限根本沒進遙測**——就算讀了累計數也不知道分母是多少。
   */
  const { describeAiStatus } = await import('~/composables/useAiStatus')
  const row = describeAiStatus({
    faction: 'RED',
    state: 'idle',
    seconds_until_next: 10,
    heartbeat_s: 45,
    total_submitted: 480,
    max_total_orders: 500,
  } as never)

  assert.equal(row.totalSubmitted, 480)
  assert.equal(row.maxTotalOrders, 500)
  assert.equal(row.ordersUntilGuard, 20)

  // 上限未知 → **不編一個數字出來**（畫面寧可不顯示也不要顯示錯的餘量）。
  const noCap = describeAiStatus({
    faction: 'BLUE', state: 'idle', seconds_until_next: 5, total_submitted: 3,
  } as never)
  assert.equal(noCap.ordersUntilGuard, null)

  const page = readSrc('pages/session/[id]/autonomy.vue')
  assert.match(page, /ai-guard-\$\{a\.faction\}/, '自主主控台沒有渲染失控保護餘量')
})
