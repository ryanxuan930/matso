/**
 * 白軍控制台的**接線**測試——問的一律是「這個值有沒有真的送到後端讀得到的位置」。
 *
 * 跑法：`cd platform && npm test`（harness 說明見 cop-wiring.test.ts）
 *
 * ## 這一檔守的兩類病
 *
 * 1. **元件寫好了但沒人用**：注入表單長出了結構化欄位，白軍控制台卻仍以舊型態掛它。
 * 2. **送出的欄位與畫面顯示的不一致**：最兇的一種在這裡是「位置對不上」——
 *    `msel_actions.make_applier` 讀的是 `inject` 的**最上層**（`inject["unit_id"]`），
 *    塞進 `payload` 的話會落到帳本的 `ai_decision` 裡看起來像有設定，套用層一個都讀不到。
 *    那正是本 repo 的招牌缺陷：存得進去、讀得回來、測試全綠、實際沒效果。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { registerHooks } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

/**
 * **時區要釘死**：後端送的時間字串不帶時區，而「當成本地時間」與「當成 UTC」在 UTC 機器上
 * 完全一樣——不釘的話，時區解析的測試會在 CI（UTC）綠、在使用者機器（UTC+8）上才爆。
 * 必須在任何 Date 運算之前設定。
 */
process.env.TZ = 'Asia/Taipei'

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

// ---- Nuxt 全域樁（必須在載入 composable 之前掛好）----
interface Captured {
  url: string
  method: string
  body: Record<string, unknown>
}
const requests: Captured[] = []
const g = globalThis as unknown as Record<string, unknown>
g.$fetch = async (url: unknown, opts: Record<string, unknown> = {}) => {
  requests.push({
    url: String(url),
    method: String(opts.method ?? 'GET'),
    body: (opts.body ?? {}) as Record<string, unknown>,
  })
  return { seq: 1 }
}
const nuxtApp: Record<string, unknown> = {}
g.useNuxtApp = () => nuxtApp
g.useCookie = () => ({ value: 'test-token' })
g.useRuntimeConfig = () => ({ public: { apiBase: 'http://test.invalid' } })

const {
  INJECT_ACTION_KEYS,
  hasBlockingIssue,
  injectActionIssues,
  setInjectAction,
} = await import('~/composables/useConditionDsl')
const { ageOf, checkpointLabel, eventAudience, eventTick, injectEvent, parseSimTime, simTimeIso } =
  await import('~/composables/useWhiteCell')

type InjectLike = Record<string, unknown>

function readSrc(rel: string): string {
  return readFileSync(fileURLToPath(new URL(rel, APP_DIR)), 'utf8')
}
/**
 * 去掉註解後的來源——**「不該出現」的斷言一律用這個**。
 * 不去的話，「不要再用 window.prompt」這句註解本身就會讓測試紅，
 * 於是下一個人會把註解刪掉（而不是把程式改對），這條測試就變成了在懲罰說明。
 */
function readCode(rel: string): string {
  return readSrc(rel)
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^[ \t]*\/\/.*$/gm, ' ')
}
function lastRequest(match: string, method = 'POST'): Captured {
  const hit = [...requests].reverse().find((r) => r.url.includes(match) && r.method === method)
  assert.ok(hit, `沒有任何 ${method} 請求打到 ${match}`)
  return hit
}

// ==================== A：注入動作的欄位位置與必填檢查 ====================

test('注入動作的欄位放在 inject 最上層，不是 payload', () => {
  /**
   * 抓的病：把 `unit_id`/`strength` 寫進 `payload`。後端 `make_applier` 只讀最上層，
   * 於是這條注入**照樣觸發、照樣落帳、單位一動也不動**——而畫面上完全看不出來。
   */
  const a = setInjectAction(
    { event_type: 'X', payload: { note: 'n' }, unit_id: 'u-1', strength: 42 } as InjectLike,
    'MODIFY_UNIT',
  ) as InjectLike

  assert.equal(a.action, 'MODIFY_UNIT')
  assert.equal(a.unit_id, 'u-1', 'unit_id 必須在 inject 最上層')
  assert.equal(a.strength, 42)
  assert.deepEqual(a.payload, { note: 'n' }, 'payload 不該被動作欄位污染')
  // 反向：動作鍵表不得把後端沒讀的東西列進去（列錯就會送出一堆沒有效果的欄位）。
  assert.deepEqual([...INJECT_ACTION_KEYS.MODIFY_UNIT], ['unit_id', 'strength', 'lat', 'lng'])
})

test('換注入動作要清掉前一個動作的欄位', () => {
  /**
   * 抓的病：MODIFY_UNIT → MESSAGE 之後 `unit_id`/`strength` 還留在 inject 裡。
   * 匯出的想定檔多出一組沒人讀的欄位，下一個人打開它會以為那是設定的一部分；
   * 改回來時看到的還是**上一次的舊值**，而畫面上沒有任何跡象說那是舊的。
   */
  const modify = setInjectAction(
    { event_type: 'X', unit_id: 'u-1', strength: 10, lat: 24, lng: 120 } as InjectLike,
    'MODIFY_UNIT',
  )
  const message = setInjectAction(modify, 'MESSAGE') as InjectLike

  assert.equal(message.action, 'MESSAGE')
  assert.equal(message.unit_id, undefined, 'unit_id 沒被清掉')
  assert.equal(message.strength, undefined, 'strength 沒被清掉')
  assert.equal(message.lat, undefined)
  assert.equal(message.event_type, 'X', '基本欄位不該被清掉')
})

test('選「純事件通知」要把 action 一起拿掉', () => {
  // 留著 `action: ''` 或舊值的話，後端 `str(inject.get("action")).upper()` 仍會去比對。
  const cleared = setInjectAction(
    { event_type: 'X', action: 'PAUSE', reason: 'r' } as InjectLike,
    '',
  ) as InjectLike
  assert.equal(cleared.action, undefined)
  assert.equal(cleared.reason, undefined)
})

test('必填檢查對得上後端會丟例外的每一個條件', () => {
  /**
   * 抓的病：這些錯誤**在演習進行中才會爆**——觸發那一刻落一筆注入失敗事件，
   * 而事件流只看得到例外的類別名。編輯時就擋下來，差別是整場演習。
   */
  const spawn = injectActionIssues({ event_type: 'R', action: 'SPAWN_UNITS', units: [{}] })
  assert.ok(hasBlockingIssue(spawn))
  assert.ok(spawn.some((i) => i.text.includes('陣營')), '缺生成陣營沒被擋（後端會丟例外）')
  assert.ok(spawn.some((i) => i.text.includes('經緯度')), '增援單位缺座標沒被擋')

  const ok = injectActionIssues({
    event_type: 'R',
    action: 'SPAWN_UNITS',
    faction: 'RED',
    units: [{ lat: 24.1, lng: 120.5 }],
  })
  assert.equal(hasBlockingIssue(ok), false, '填齊了還在擋')

  assert.ok(
    hasBlockingIssue(injectActionIssues({ event_type: 'X', action: 'MODIFY_UNIT' })),
    '缺 unit_id 沒被擋',
  )
  assert.ok(
    hasBlockingIssue(injectActionIssues({ event_type: 'X', action: 'MESSAGE' })),
    '缺收件陣營沒被擋',
  )
  assert.ok(hasBlockingIssue(injectActionIssues({ event_type: '' })), '空事件型別沒被擋')
})

test('只填經度或只填緯度要擋下來——後端會整組忽略', () => {
  /**
   * 抓的病：`msel_actions._modify_unit` 是 `if "lat" in inject and "lng" in inject`。
   * 只填一個時**沒有錯誤、沒有事件、位置就是不會變**，是最難查的一種。
   */
  const half = injectActionIssues({ event_type: 'X', action: 'MODIFY_UNIT', unit_id: 'u', lat: 24 })
  assert.ok(hasBlockingIssue(half), '半組座標沒被擋')

  const both = injectActionIssues({
    event_type: 'X',
    action: 'MODIFY_UNIT',
    unit_id: 'u',
    lat: 24,
    lng: 120,
  })
  assert.equal(hasBlockingIssue(both), false)
})

test('即時注入送出的事件型別／受眾／payload 就是表單上的那三個', async () => {
  // 抓的病：受眾漏送 → 後端 faction=None ＝**廣播全體**。統裁以為只發給藍軍，
  // 紅軍也收到了——這是迷霧漏洞，不是顯示問題。
  await injectEvent('s-1', 'BRIDGE_DESTROYED', { initiator_id: 'u-1' }, 'BLUE')
  const req = lastRequest('/inject')
  assert.equal(req.body.event_type, 'BRIDGE_DESTROYED')
  assert.equal(req.body.faction, 'BLUE')
  assert.deepEqual(req.body.payload, { initiator_id: 'u-1' })
})

// ==================== B：事件流的資訊密度 ====================

test('事件的 tick 讀的是 payload 而不是頂層', () => {
  /**
   * 抓的病：`build_event_envelope` 把 tick 寫在 **payload 裡**；讀頂層的話所有戰況事件
   * 都會顯示成「沒有 tick」。而 API 直發的事件（注入／時間控制）兩邊都沒有 tick——
   * 那時要回 null，不能回 0：`tick 0` 會被讀成「開局那一刻發生的」，那是假的。
   */
  assert.equal(eventTick({ payload: { event_type: 'X', tick: 42 } }), 42)
  assert.equal(eventTick({ tick: 7, payload: {} }), 7, '心跳型 envelope 的頂層 tick 也要認')
  assert.equal(eventTick({ payload: { event_type: 'X' } }), null, '沒有 tick 時不得捏一個 0')
})

test('受眾標籤分得出「某幾軍」「全體」與「僅統裁」', () => {
  /**
   * 抓的病：統裁在講評時最容易講錯的就是「藍軍那時知不知道這件事」。
   * 三種受眾在後端是三種不同的標籤（見 stream/faction_filter.py），畫面上要分得出來。
   */
  assert.equal(eventAudience({ factions: ['BLUE', 'RED'] }), 'BLUE、RED')
  assert.equal(eventAudience({ faction: 'BLUE' }), 'BLUE')
  assert.equal(eventAudience({}), '全體')
  assert.equal(eventAudience({ factions: [] }), '僅統裁', '空受眾清單＝只有全知角色收得到')
})

// ==================== C：快照點的可讀性 ====================

test('後端的時間字串沒有時區，要當成 UTC 讀', () => {
  /**
   * 抓的病（實測抓到的）：`GET /checkpoints` 回的是 `2026-07-30T23:55:53.589000`
   * ——**沒有 Z、沒有偏移**，而 JS 對這種格式的規定是「當成本地時間」。
   * 於是在 UTC+8 的機器上，一個 4 分鐘前的快照會被算成「8 小時 3 分前」。
   * 本卡把時間變成快照點的主要選擇依據，這個偏移會讓統裁選錯回溯點——
   * 而畫面上不會有任何跡象顯示是解析錯了。
   */
  assert.equal(
    parseSimTime('2026-07-30T23:55:53.589000'),
    Date.parse('2026-07-30T23:55:53.589Z'),
    '不帶時區的字串沒有當成 UTC',
  )
  // 已經帶時區的不得再補一個 Z（那會把時間往前推整個偏移量）。
  assert.equal(parseSimTime('2026-07-30T23:55:53+08:00'), Date.parse('2026-07-30T15:55:53Z'))
  assert.equal(parseSimTime('2026-07-30T23:55:53Z'), Date.parse('2026-07-30T23:55:53Z'))

  assert.equal(ageOf('2026-07-30T23:55:53.000', Date.parse('2026-07-30T23:59:53Z')), '4 分鐘前')
  // 共用時間列自己會 Date.parse，所以交給它之前要補上明確時區。
  assert.equal(simTimeIso('2026-07-30T16:27:56.993000'), '2026-07-30T16:27:56.993Z')
  assert.equal(simTimeIso(null), null)
})

test('快照點的一行字以時間為主、雜湊只是校驗碼', () => {
  /**
   * 抓的病：舊寫法是 `tick n · seq n · 雜湊前 8 碼`。統裁在回溯時要回答的問題是
   * 「回到什麼時候」——`3f9a1c2e` 不回答那個問題，它是給人核對用的。
   */
  const iso = '2026-07-31T02:00:00.000Z'
  const now = Date.parse(iso) + 6 * 60_000
  const label = checkpointLabel(
    { tick: 600, ledger_seq: 120, state_hash: '3f9a1c2e55667788', created_at: iso },
    now,
    '交戰命中 B1 → R2',
  )

  assert.match(label, /6 分鐘前/, '沒有「多久以前」——那是統裁腦中真正的座標系')
  assert.match(label, /校驗碼/, '雜湊沒有標示成校驗碼，會被誤當成選擇依據')
  assert.match(label, /交戰命中 B1/, '知道那個 tick 發生什麼卻沒寫出來')
  assert.match(label, /600/, 'tick 是回滾的實際單位，不能省')
})

test('不知道那個 tick 發生什麼就不要寫', () => {
  // 兵推系統裡編一句聽起來合理的敘述，比留白危險得多。
  const label = checkpointLabel(
    { tick: 600, ledger_seq: 120, state_hash: 'abcdef0123', created_at: '2026-07-31T02:00:00Z' },
    Date.parse('2026-07-31T02:00:30Z'),
  )
  assert.ok(!label.includes('當時'), '沒有資料卻生出了「當時：」')
})

// ==================== 樣板讀取端（harness 不編譯 SFC，故掃來源守「有沒有人用」）====================

test('白軍控制台以 live 型態掛注入表單，並把單位清單交給它', () => {
  /**
   * 抓的病（本檔最重要的一條）：表單長出了型別選單與單位下拉，控制台卻仍用舊寫法掛它
   * ——元件寫好了但沒人用。`variant="live"` 還有語義：即時注入端點**不套用動作**，
   * 掛成 msel 型態等於給統裁一排按了沒效果的選項。
   */
  const src = readSrc('pages/session/[id]/white-cell.vue')
  assert.match(src, /<InjectActionForm[\s\S]*?variant="live"/, '注入表單沒有用 live 型態')
  assert.match(src, /<InjectActionForm[\s\S]*?:units="units"/, '沒把單位清單交給表單（又要手抄 UUID）')
  assert.match(src, /data-testid="wc-inject-preview"/, '沒有送出前的戰況流預覽')
  assert.match(src, /injectBlocked/, '有必填問題時仍送得出去')
})

test('事件流要列出 tick 與受眾（統裁要追事件鏈）', () => {
  const src = readSrc('pages/session/[id]/white-cell.vue')
  assert.match(src, /eventTick/, '事件流沒有 tick')
  assert.match(src, /data-testid="wc-event-audience"/, '事件流沒有受眾欄')
  assert.match(src, /data-testid="wc-event-filter"/, '事件流沒有篩選（追一條鏈要翻幾百則）')
  assert.ok(
    !/events\.slice\(-20\)/.test(readCode('pages/session/[id]/white-cell.vue')),
    '事件流仍只留 20 則',
  )
})

test('演習中途打開控制台要能看到已經發生的事', () => {
  /**
   * 抓的病：握手不帶 last_seq 時後端把你當全新 client，一則歷史都不補
   * （`stream/backfill.plan_resume`）。統裁在 H+40 打開控制台，事件流是一片空白。
   */
  const src = readSrc('pages/session/[id]/white-cell.vue')
  assert.match(src, /stream\.lastSeq = 0/, '沒有要求補送歷史事件')
  assert.match(src, /if \(!stream\.events\.length\)/, '無條件補送會在已有緩衝時收到重複的一份')
})

test('回溯要二段確認，且不得回頭用 window 對話框', () => {
  /**
   * 抓的病：回溯把該點之後的推演全部作廢且無法復原，而它是一顆與「暫停」長得一模一樣的按鈕。
   * 另一半是回歸防護：這張卡就是來拔掉 `window.prompt` 的，不能又長回來。
   */
  const src = readSrc('pages/session/[id]/white-cell.vue')
  assert.match(src, /data-testid="wc-rollback-confirm"/, '回溯沒有確認步驟')
  assert.match(src, /data-testid="wc-rollback-yes"/)
  assert.ok(
    !/window\.(prompt|confirm)\s*\(/.test(readCode('pages/session/[id]/white-cell.vue')),
    '又用回 window 對話框了',
  )
  assert.match(src, /data-testid="wc-checkpoints-refresh"/, '快照點清單沒有重抓的辦法')
  assert.match(src, /setInterval\(loadCheckpoints/, '快照點只在開頁時抓一次，開久了就過期')
})

test('快照點下拉用共用的敘述函式（不是在樣板裡切雜湊）', () => {
  const src = readSrc('pages/session/[id]/white-cell.vue')
  assert.match(src, /checkpointLabel/, '沒有用 checkpointLabel')
  // 共用時間列自己 Date.parse，交給它的字串必須先補上時區——否則「執行時間」會少算整個時差
  // （UTC+8 上開局時間變成 8 小時後的未來，算出負數直接顯示成「—」）。
  assert.match(src, /simTimeIso\(list\.find/, '開局時間沒補時區就交給時間列')
  assert.ok(
    !/state_hash\.slice\(0, 8\)/.test(readCode('pages/session/[id]/white-cell.vue')),
    '樣板又自己切了一次雜湊',
  )
})

test('注入表單有型別下拉、逃生口與必填提示', () => {
  const src = readSrc('components/InjectActionForm.vue')
  assert.match(src, /data-testid="iaf-action"/, '沒有注入動作下拉')
  assert.match(src, /data-testid="iaf-json"/, '沒有「直接編 JSON」的逃生口')
  assert.match(src, /data-testid="iaf-json-error"/, 'JSON 打錯了沒有說錯在哪')
  assert.match(src, /data-testid="iaf-issues"/, '沒有必填問題提示')
  // 動作欄位一律經 patch()/setInjectAction 寫到最上層——不得再有人往 payload 裡塞動作。
  assert.ok(
    !/payload:\s*\{[^}]*unit_id/.test(readCode('components/InjectActionForm.vue')),
    '有人把動作欄位塞進 payload（後端讀不到）',
  )
})

test('動作選單只在腳本型態出現——即時注入端點不會套用它', () => {
  /**
   * 抓的病：即時注入走 `api/inject.py` 的 `publish_event`，**完全不經過套用層**。
   * 在那裡給一排「增援生成／調整單位」的選項，是讓統裁按一顆什麼都不會發生的按鈕。
   */
  const src = readSrc('components/InjectActionForm.vue')
  assert.match(src, /v-if="variant === 'msel'"[\s\S]{0,400}?iaf-action/, '動作下拉沒有限定腳本型態')
  assert.match(src, /data-testid="iaf-live-note"/, '即時注入沒有講清楚它不會改變世界')
})

test('想定編輯器仍拿得到動作編輯能力（預設型態＝腳本）', () => {
  // 想定編輯器是這五種動作**唯一**真的會生效的入口；預設值改錯的話它會靜靜地退化成純事件通知。
  const editor = readSrc('pages/scenario-editor.vue')
  assert.match(editor, /<InjectActionForm[^>]*v-model="m\.inject"/)
  assert.ok(
    !/<InjectActionForm[^>]*variant=/.test(readCode('pages/scenario-editor.vue')),
    '想定編輯器不該覆寫型態（預設就是腳本）',
  )

  const form = readSrc('components/InjectActionForm.vue')
  assert.match(form, /variant: 'msel'/, '預設型態不是腳本，想定編輯器會失去動作選單')
})
