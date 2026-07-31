/**
 * COP 下令／地圖編輯的**接線**測試——問的一律是「這個值有沒有真的送出去／讀得到」。
 *
 * 跑法（repo 沒有 vitest，用 Node 內建 test runner + 型別剝離）：
 *   cd platform && node --test tests/cop-wiring.test.ts
 *
 * 這一檔守的是本 repo 最常見的那個病：**存得進去、讀得回來、測試全綠、實際沒效果**。
 * 所以每一條測的都是「組裝點」——payload 裡有沒有那個鍵、請求有沒有帶那個欄位——
 * 而不是被呼叫函式自己的行為（那些函式一直都是對的，缺的是呼叫端）。
 *
 * 為什麼要自己接 resolve hook 與 Nuxt 全域樁：`~/` 是 Vite 別名、`$fetch`/`useCookie`/
 * `useRuntimeConfig`/`useSessionStreamStore` 是 Nuxt 自動匯入的全域。兩者在 Node 裡都不存在，
 * 但它們都在**呼叫時**才解析，所以掛到 globalThis 就能在無瀏覽器、無 Nuxt 執行期下驗證接線。
 * （本檔刻意放在 `tests/` 而不是 `tests/nuxt/`：後者被 `.nuxt/tsconfig.app.json` 收進
 *   `vue-tsc --build`，而該專案的 `types: []` 沒有 node 型別，`node:test` 會整批報錯。）
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

// ---- Nuxt 全域樁（必須在載入 composable 之前掛好）----
interface Captured {
  url: string
  method: string
  body: Record<string, unknown>
}
const requests: Captured[] = []

function respond(url: string): unknown {
  if (url.includes('/movement/preview')) {
    return {
      path: [
        [120.5, 24.1],
        [120.6, 24.2],
      ],
      distance_m: 14200,
      duration_ticks: 20,
      fuel_cost: 30,
      est_attrition: 2.5,
      feasible: true,
      forced: false,
      crossings: [],
      mobility_profile: 'TRACKED',
      speed_kmh: 42,
      terrain_impassable: false,
      terrain_routed: false,
      fuel_remaining: 400,
      fuel_sufficient: true,
    }
  }
  if (url.includes('/orders')) {
    return { id: 'o-1', status: 'VALIDATED', precheck: { feasible: true, checks: [] } }
  }
  if (url.includes('/map-features')) return []
  if (url.includes('/requests')) return { requests: [] }
  return {}
}

const g = globalThis as unknown as Record<string, unknown>
g.$fetch = async (url: unknown, opts: Record<string, unknown> = {}) => {
  const u = String(url)
  requests.push({
    url: u,
    method: String(opts.method ?? 'GET'),
    body: (opts.body ?? {}) as Record<string, unknown>,
  })
  return respond(u)
}
g.useNuxtApp = () => nuxtApp
g.useCookie = () => ({ value: 'test-token' })
g.useRuntimeConfig = () => ({ public: { apiBase: 'http://test.invalid' } })
g.useSessionStreamStore = () => ({ unitPatches: {} })
const nuxtApp: Record<string, unknown> = {}

const { computed, ref, nextTick } = await import('vue')
const { useCopOrdering } = await import('~/composables/useCopOrdering')
const { useMapEditor } = await import('~/composables/useMapEditor')
const { looksLikeNoStrikeName } = await import('~/composables/useMapFeatures')

// ---- 小工具 ----
interface Toast {
  severity?: string
  title?: string
  detail?: string
}
function makeToasts() {
  const list: Toast[] = []
  return { list, push: (t: Toast) => list.push(t) }
}
/** 去抖（180ms）+ 網路樁都是非同步的——給它足夠時間落地。 */
const settle = () => new Promise((r) => setTimeout(r, 260))
/**
 * 取最後一筆符合的請求。**method 一定要指定**：建立標註之後緊接著一次 `loadFeatures()` 的
 * GET，不篩就會抓到那筆空 body 的 GET，斷言全部變成「undefined 不等於期望值」。
 */
function lastRequest(match: string, method = 'POST'): Captured {
  const hit = [...requests].reverse().find((r) => r.url.includes(match) && r.method === method)
  assert.ok(hit, `沒有任何 ${method} 請求打到 ${match}`)
  return hit
}
function payloadOf(req: Captured): Record<string, unknown> {
  return (req.body.payload ?? {}) as Record<string, unknown>
}
function readSrc(rel: string): string {
  return readFileSync(fileURLToPath(new URL(rel, APP_DIR)), 'utf8')
}

function makeOrdering(toasts = makeToasts()) {
  const selectedId = ref<string | null>('u-1')
  return {
    toasts,
    o: useCopOrdering({
      sessionId: ref('s-1'),
      selectedId,
      selectedUnit: computed(() => ({ id: 'u-1', designation: 'B1' })) as never,
      selectedUnitFixed: computed(() => false),
      refresh: async () => {},
      toasts: toasts as never,
    }),
  }
}

/** `viewpoint`＝白軍套用中的陣營視角；補給點的歸屬就是靠它決定（見補給點那幾條）。 */
function makeEditor(toasts = makeToasts(), viewpoint = '') {
  return {
    toasts,
    ed: useMapEditor({
      sessionId: ref('s-1'),
      viewpoint: ref(viewpoint),
      canControl: computed(() => true),
      myFaction: ref('BLUE'),
      hiddenFeatureIds: ref([]),
      toasts: toasts as never,
    }),
  }
}

// ============================ M8：強行軍（tempo） ============================

test('MOVE 令要把行軍節奏送出去', async () => {
  /**
   * 抓的病：`MovePayload.tempo` 與 `movement.py` 的速度／耗損倍率後端全都做好了，
   * 但**唯一的送出端是 AI**（`ai_loop/orders_bridge.py`）——人類指揮官連按鈕都沒有，
   * 於是同一局裡 AI 陣營的機動速度上限比人類高，而畫面上沒有任何說明。
   * 這條會在「面板加了下拉但 buildPayload 沒帶」時轉紅——那正是本 repo 最常見的漏接形狀。
   */
  const { o } = makeOrdering()
  o.orderType.value = 'MOVE'
  o.destH3.value = '8a2a1072b59ffff'
  o.tempo.value = 'FORCED_MARCH'
  await o.submit()

  const payload = payloadOf(lastRequest('/orders'))
  assert.equal(payload.tempo, 'FORCED_MARCH')
})

test('未選強行軍時仍明確送出 NORMAL（不靠後端預設）', async () => {
  /**
   * 抓的病：只在 FORCED_MARCH 時才帶 tempo 的話，Ledger 上「指揮官選了常速」與
   * 「這道令根本沒有節奏欄位」會長得一模一樣，AAR 事後分不出哪一種。
   */
  const { o } = makeOrdering()
  o.orderType.value = 'MOVE'
  o.destH3.value = '8a2a1072b59ffff'
  await o.submit()

  assert.equal(payloadOf(lastRequest('/orders')).tempo, 'NORMAL')
})

test('移動預覽要帶與送出同一個行軍節奏', async () => {
  /**
   * 抓的病：**預覽與實跑不一致**。後端預覽端的 speed_kmh／duration_ticks／est_attrition
   * 三個數字全都乘了 tempo 係數，預覽不帶就等於拿常速去估一趟強行軍——
   * 面板顯示的距離、時間與耗損全部偏低，而送出去的是另一回事。
   */
  const { o } = makeOrdering()
  o.orderType.value = 'MOVE'
  o.destH3.value = '8a2a1072b59ffff'
  o.tempo.value = 'FORCED_MARCH'
  o.schedulePreview()
  await settle()

  const preview = lastRequest('/movement/preview')
  assert.equal(preview.body.tempo, 'FORCED_MARCH')

  await o.submit()
  assert.equal(
    payloadOf(lastRequest('/orders')).tempo,
    preview.body.tempo,
    '預覽與送出的節奏必須是同一個值',
  )
})

test('換節奏要重算預覽', async () => {
  /**
   * 抓的病：切到「強行軍」卻沒有任何數字變動——面板留著常速那份試算，
   * 使用者只能猜這個選項有沒有生效。（`watch(tempo, schedulePreview)` 被拿掉就會紅。）
   */
  const { o } = makeOrdering()
  o.orderType.value = 'MOVE'
  o.destH3.value = '8a2a1072b59ffff'
  o.schedulePreview()
  await settle()
  const before = requests.filter((r) => r.url.includes('/movement/preview')).length

  o.tempo.value = 'FORCED_MARCH'
  await settle()
  const after = requests.filter((r) => r.url.includes('/movement/preview')).length

  assert.ok(after > before, '換了行軍節奏卻沒有重新試算')
  assert.equal(lastRequest('/movement/preview').body.tempo, 'FORCED_MARCH')
})

test('換單位時強行軍要退回一般行軍', async () => {
  /**
   * 抓的病：強行軍是要付戰力代價的例外決定。沿用到下一個單位的話，
   * 那個單位的指揮官從來沒有做過這個決定，卻要付這個代價。
   */
  const { o } = makeOrdering()
  o.tempo.value = 'FORCED_MARCH'
  o.resetOrderForm()
  assert.equal(o.tempo.value, 'NORMAL')
})

// ============================ I1：繪製時的禁射級別 ============================

test('繪製時選的禁射級別要真的寫進 attributes', async () => {
  /**
   * 抓的病：`finishDraw()` 組 attributes 時完全沒有 `zone_class`——只有
   * 「先畫完 → 再選取 → 從編輯面板的下拉選一次」那一路寫得進去。
   * 後果是畫了一個叫「XX 禁射區」的多邊形，取名上色加備註都做了，
   * 它對火力裁決卻**完全沒有效力**，而中間沒有任何警告。
   */
  const { ed } = makeEditor()
  ed.drawZoneClass.value = 'NO_STRIKE'
  ed.startDraw('POLYGON', 'CONTROL_MEASURE')
  ed.addDraftPoint(120.5, 24.1)
  ed.addDraftPoint(120.6, 24.1)
  ed.addDraftPoint(120.6, 24.2)
  await ed.finishDraw()

  const attrs = lastRequest('/map-features').body.attributes as Record<string, unknown>
  assert.equal(attrs.zone_class, 'NO_STRIKE')
})

test('圓形／矩形畫出來的禁射區同樣有效', async () => {
  /**
   * 抓的病：圓與矩形是以兩點繪製、存成 POLYGON 的。若只認 `drawKind === 'POLYGON'`，
   * 用「圓形」圈出來的禁射區就會靜默失效——而那正是圈醫院/廟宇最順手的工具。
   */
  const { ed } = makeEditor()
  ed.drawZoneClass.value = 'RESTRICTED_FIRE'
  ed.startDraw('CIRCLE', 'CONTROL_MEASURE')
  ed.addDraftPoint(120.5, 24.1)
  ed.addDraftPoint(120.51, 24.1) // 第二點即完成（中心＋邊緣）
  await settle()

  const req = lastRequest('/map-features')
  assert.equal(req.body.geometry_type, 'POLYGON')
  assert.equal((req.body.attributes as Record<string, unknown>).zone_class, 'RESTRICTED_FIRE')
})

test('點／線不成區：禁射級別不寫入，而且要出聲', async () => {
  /**
   * 抓的病：後端 `no_strike._feature_zones` 只查 `geometry_type == "POLYGON"`，
   * 掛在點/線上的 zone_class 會被**靜默**丟掉。靜默是這裡真正的問題——
   * 使用者會以為自己已經圈好了一個禁射點。
   */
  const { ed, toasts } = makeEditor()
  ed.drawZoneClass.value = 'NO_STRIKE'
  ed.startDraw('POINT', 'ANNOTATION')
  ed.addDraftPoint(120.5, 24.1) // 點：一點即完成
  await settle()

  const attrs = (lastRequest('/map-features').body.attributes ?? {}) as Record<string, unknown>
  assert.equal(attrs.zone_class, undefined)
  assert.ok(
    toasts.list.some((t) => t.severity === 'warn' && String(t.title).includes('禁射')),
    '禁射級別被丟掉卻沒有任何提示',
  )
})

test('名字像禁射區卻沒選級別 → 表單即時提示，畫完再警告一次', async () => {
  /**
   * 抓的病：一個沒有 `zone_class` 的「XX 禁射區」在地圖上與真的禁射區長得一模一樣，
   * 沒有人會回頭去點一個「已經畫好」的區來檢查。禁射區是安全機制，
   * 「以為圈好了、其實沒有」比沒圈更危險。
   */
  assert.equal(looksLikeNoStrikeName('市立醫院禁射區'), true)
  assert.equal(looksLikeNoStrikeName('Restricted Fire Area 3'), true)
  assert.equal(looksLikeNoStrikeName('第三攻擊軸線'), false)

  const { ed, toasts } = makeEditor()
  ed.drawLabel.value = '市立醫院禁射區'
  await nextTick()
  assert.equal(ed.drawZoneNameUnset.value, true, '表單沒有即時提示')

  ed.startDraw('POLYGON', 'CONTROL_MEASURE')
  ed.addDraftPoint(120.5, 24.1)
  ed.addDraftPoint(120.6, 24.1)
  ed.addDraftPoint(120.6, 24.2)
  await ed.finishDraw()
  assert.ok(
    toasts.list.some((t) => t.severity === 'warn' && String(t.title).includes('無禁射效力')),
    '畫完了也沒有講「這個區沒有效力」',
  )

  ed.drawZoneClass.value = 'NO_STRIKE'
  ed.drawLabel.value = '市立醫院禁射區'
  await nextTick()
  assert.equal(ed.drawZoneNameUnset.value, false, '級別選了就不該再提示')
})

test('按下形狀鈕不可以清掉表單剛收的繪製屬性', async () => {
  /**
   * 抓的病（本批最深的一個）：屬性表單**只在「還沒開始畫」時顯示**——按下形狀鈕之後
   * 畫面上只剩「完成／取消」。而 `startDraw()` 原本會把 label/color/notes/sidc/
   * obstacle_type/density 全部歸零，於是使用者填的每一格都在按下形狀鈕的那一瞬間被清掉，
   * `finishDraw()` 讀到的永遠是空值。症狀：取名了卻顯示成 OBSTACLE、
   * 選了「雷區」卻建出一個沒有型別的純幾何障礙——而畫面上沒有任何跡象。
   * WP-C2 的障礙型別下拉就是這樣一路「編得動、送不出去」。
   */
  const { ed } = makeEditor()
  ed.drawFeatureKind.value = 'OBSTACLE'
  ed.drawLabel.value = '甲區雷場'
  ed.drawNotes.value = '工兵已標定'
  ed.drawObstacleType.value = 'MINEFIELD'
  ed.drawDensity.value = 0.6

  ed.startDraw('POLYGON', 'OBSTACLE')
  assert.equal(ed.drawLabel.value, '甲區雷場', '名稱在開始繪製時被清掉了')
  assert.equal(ed.drawObstacleType.value, 'MINEFIELD', '障礙型別在開始繪製時被清掉了')

  ed.addDraftPoint(120.5, 24.1)
  ed.addDraftPoint(120.6, 24.1)
  ed.addDraftPoint(120.6, 24.2)
  await ed.finishDraw()

  const req = lastRequest('/map-features')
  const attrs = req.body.attributes as Record<string, unknown>
  assert.equal(req.body.label, '甲區雷場')
  assert.equal(attrs.notes, '工兵已標定')
  assert.equal(attrs.obstacle_type, 'MINEFIELD')
  assert.equal(attrs.density, 0.6)
})

test('畫完一個之後屬性才清空（下一個標註不該繼承上一個的名字）', async () => {
  /** 抓的病：把清除搬離 startDraw 之後若忘了在 finishDraw 補上，第二個標註會沿用第一個的名稱與型別。 */
  const { ed } = makeEditor()
  ed.drawLabel.value = '甲區雷場'
  ed.drawZoneClass.value = 'NO_STRIKE'
  ed.startDraw('POLYGON', 'CONTROL_MEASURE')
  ed.addDraftPoint(120.5, 24.1)
  ed.addDraftPoint(120.6, 24.1)
  ed.addDraftPoint(120.6, 24.2)
  await ed.finishDraw()

  assert.equal(ed.drawLabel.value, '')
  assert.equal(ed.drawZoneClass.value, '')
})

// ============ 樣板讀取端（本 harness 不會編譯 SFC，故以來源掃描守「有沒有人讀」）============

test('下令面板要顯示武器最小射程', () => {
  /**
   * 抓的病：`WeaponView.min_range_m` 在 COP 只有 `max_range_m` 被顯示。
   * 後果很具體——迫砲有死角，操作員在卡片上只看到「4.2 km」，對近距目標下令後被預檢擋下，
   * 而畫面上沒有任何資訊解釋為什麼。
   */
  const src = readSrc('components/cop/UnitsOrderPanel.vue')
  assert.match(src, /min_range_m/, 'UnitsOrderPanel 沒有任何地方讀 min_range_m')
  assert.match(src, /data-testid="weapon-min-range"/, '沒有給選定武器的最小射程說明')
})

test('路徑試算要列出行軍耗損', () => {
  /**
   * 抓的病：`MovementPreviewView.est_attrition` 零讀取端。路徑試算列了距離/tick/油耗/
   * 速度/繞路/油量，唯獨不列行軍耗損——指揮官看不到長途行軍要付多少戰力代價，
   * 只有事後在事件流看到 MOVE_ATTRITION。
   */
  const src = readSrc('components/cop/UnitsOrderPanel.vue')
  assert.match(src, /est_attrition/, '路徑試算面板沒有讀 est_attrition')
  assert.match(src, /data-testid="move-attrition"/)
})

test('下令面板要有行軍節奏選項、地圖編輯要有禁射級別下拉', () => {
  /**
   * 抓的病：composable 帶了欄位但樣板沒有控制項＝使用者仍然選不到。
   * 這正是 tempo 過去的狀態（後端完整、契約有、就是沒有人按得到）。
   */
  const order = readSrc('components/cop/UnitsOrderPanel.vue')
  assert.match(order, /data-testid="move-tempo"/, '下令面板沒有行軍節奏選項')

  const editor = readSrc('components/cop/MapEditorPanel.vue')
  assert.match(editor, /data-testid="draw-zone-class"/, '繪製表單沒有禁射級別下拉')
  assert.match(editor, /data-testid="draw-zone-warn"/, '繪製表單沒有「名稱像禁射區卻沒選級別」的提示')
})

test('打開小工具要拉到最上層——否則會被別的視窗蓋住', async () => {
  const { useCopWidgets } = await import('~/composables/useCopWidgets')
  const w = useCopWidgets()

  // 先把別的視窗點到很上面（模擬「使用者剛操作過那個」）。
  w.focusWidget('units')
  const busyZ = w.widgets.value.units.z

  // 經別名旗標打開座標查詢——這條路徑原本只寫 open、不 focus，
  // 於是它保持預設 z（比 busyZ 低）→ 打開了卻被蓋住，看起來像「按了沒反應」。
  const coordQuery = w.openFlag('coords')
  coordQuery.value = true

  assert.equal(w.widgets.value.coords.open, true)
  assert.ok(
    w.widgets.value.coords.z > busyZ,
    `打開後 z 應高於已聚焦的視窗：coords=${w.widgets.value.coords.z} units=${busyZ}`,
  )
})

test('關閉小工具不會動層序（只有打開才拉上來）', async () => {
  const { useCopWidgets } = await import('~/composables/useCopWidgets')
  const w = useCopWidgets()
  const flag = w.openFlag('coords')
  flag.value = true
  const z = w.widgets.value.coords.z
  flag.value = false

  assert.equal(w.widgets.value.coords.z, z)
})

// ---- 單位屬性編輯器（番號/兵科/人數/戰力）----

test('單位屬性編輯器只送有改過的欄位（PATCH 語義）', () => {
  // 一次把整包送上去，會把別人剛改的欄位蓋回去——尤其戰力是活模擬持續在動的量。
  const src = readSrc('components/UnitAttributeEditor.vue')
  assert.match(src, /const changes = computed/)
  assert.match(src, /!== props\.designation/)
  assert.match(src, /!== props\.strength/)
  assert.ok(
    !/body: form/.test(src) && !/editUnitAttributes\([^)]*form\)/.test(src),
    '不可把整個 form 當 body 送出',
  )
})

test('單位屬性編輯器不提供作戰效能輸入欄', () => {
  // health 是由戰力比導出的顯示值，裁決層每次命中都會覆寫它。
  // 給一個輸入框等於讓統裁以為這裡可以「補血」，而下一次交戰就會把它打回去。
  const src = readSrc('components/UnitAttributeEditor.vue')
  assert.ok(!/health_status/.test(src), '不該送 health_status（後端回 422）')
  assert.match(src, /effectivenessPreview/, '應改為顯示算出來的戰力比')
  assert.match(src, /不可直接編輯/)
})

test('單位屬性編輯器會把「需重啟才生效」講出來', () => {
  // 改編制級別只有 runner 重啟後才會影響聚合裁決。不講的話使用者會以為已經生效。
  const src = readSrc('components/UnitAttributeEditor.vue')
  assert.match(src, /restart_required/)
  assert.match(src, /重新啟動/)
})

test('單位資訊卡有掛上單位屬性編輯器', () => {
  // 這條盯的是「元件寫好了但沒人用」——這個 repo 反覆出現的那類缺陷。
  const src = readSrc('components/cop/UnitDetailCard.vue')
  assert.match(src, /<UnitAttributeEditor/)
  assert.match(src, /toggle-attrs/)
  assert.match(src, /:unit-level="unit\.unit_level"/)
})

// ============================ WP-C7：補給點與補給水位 ============================

test('補給點的庫存要真的送進 attributes.stock', async () => {
  /**
   * 抓的病：庫存是補給點**唯一的實質內容**。表單編得動、送出時漏帶的話，
   * 建出來的是一個空倉庫——它在地圖上與有貨的補給點長得一模一樣，
   * 而 `draw_from()` 一份補給都撥不出去，下游單位的水位就是不會回升。
   * 這正是 WP-C2 障礙型別走過的那條「編得動、送不出去」的路。
   */
  const { ed } = makeEditor(makeToasts(), 'BLUE')
  ed.drawFeatureKind.value = 'SUPPLY_POINT'
  await nextTick() // 換類別的副作用（高度只對障礙/建築有意義）要先落地，如同使用者操作面板
  ed.drawSupplyStock.value.I = 500
  ed.drawSupplyStock.value.IX = 80
  ed.startDraw('POINT', 'SUPPLY_POINT')
  ed.addDraftPoint(121.25, 23.75) // 點：一點即完成
  await settle()

  const req = lastRequest('/map-features')
  assert.equal(req.body.kind, 'SUPPLY_POINT')
  assert.equal(req.body.geometry_type, 'POINT')
  assert.equal(req.body.owner_faction, 'BLUE', '補給點沒有帶陣營＝落共同層＝沒有單位拉得到')
  assert.deepEqual(req.body.attributes, { stock: { I: 500, IX: 80 } })
})

test('沒填任何庫存的補給點不送出，而且要出聲', async () => {
  /** 空倉庫要明寫 0——「忘了填」與「這裡真的沒貨」是不同的事，靜默建立會讓兩者分不出來。 */
  const { ed, toasts } = makeEditor(makeToasts(), 'BLUE')
  ed.drawFeatureKind.value = 'SUPPLY_POINT'
  ed.startDraw('POINT', 'SUPPLY_POINT')
  const before = requests.filter((r) => r.url.includes('/map-features') && r.method === 'POST').length
  ed.addDraftPoint(121.25, 23.75)
  await settle()

  const after = requests.filter((r) => r.url.includes('/map-features') && r.method === 'POST').length
  assert.equal(after, before, '沒有庫存的補給點不該被建立')
  assert.ok(
    toasts.list.some((t) => t.severity === 'warn' && String(t.title).includes('庫存')),
    '擋下來了卻沒有說為什麼',
  )
})

test('沒有陣營視角時不建補給點（共同層的補給點沒有人拉得到）', async () => {
  /**
   * 抓的病：白軍不指定視角時，`finishDraw` 送的 owner_faction 是 null → 後端落 WHITE_CELL。
   * 其他每一種標註放共同層都是對的（全體可見），只有補給點例外——
   * `nearest_usable()` 只找**同陣營**的點，共同層的補給點一份都撥不出去。
   */
  const { ed, toasts } = makeEditor(makeToasts(), '') // 白軍未套用陣營視角
  ed.drawFeatureKind.value = 'SUPPLY_POINT'
  ed.drawSupplyStock.value.I = 500
  ed.startDraw('POINT', 'SUPPLY_POINT')
  const before = requests.filter((r) => r.url.includes('/map-features') && r.method === 'POST').length
  ed.addDraftPoint(121.25, 23.75)
  await settle()

  assert.equal(
    requests.filter((r) => r.url.includes('/map-features') && r.method === 'POST').length,
    before,
  )
  assert.ok(toasts.list.some((t) => String(t.title).includes('陣營')), '沒有講「補給點要有陣營」')
})

test('編輯庫存是整包換掉，不是併進舊的', async () => {
  /**
   * 抓的病：PATCH 對 attributes 是 **merge**。把某一格改回「不備」時若只送剩下的類別，
   * 舊值會活下來——畫面上那一格已經清空，撥交端卻還撥得出來。
   * 「顯示的跟送出的不一樣」在後勤上的具體形式就是這個。
   */
  const { ed } = makeEditor()
  ed.mapFeatures.value = [
    {
      id: 'f-1',
      kind: 'SUPPLY_POINT',
      geometry_type: 'POINT',
      geometry: [121.25, 23.75],
      owner_faction: 'BLUE',
      attributes: { stock: { I: 500, IX: 80 } },
    },
  ] as never
  ed.onFeatureClick({ id: 'f-1' })
  assert.deepEqual(
    { I: ed.editFeatStock.value.I, IX: ed.editFeatStock.value.IX },
    { I: 500, IX: 80 },
    '選取補給點時沒有把既有庫存讀進表單',
  )

  ed.editFeatStock.value.IX = null // 這一格改成「不備」
  await ed.saveFeatureEdit()

  const attrs = lastRequest('/map-features', 'PATCH').body.attributes as Record<string, unknown>
  assert.deepEqual(attrs.stock, { I: 500 }, 'stock 沒有被整包換掉，舊的 IX 會活下來')
})

test('活補給水位吃得下 STATE_DIFF 的熱狀態形狀', async () => {
  /**
   * 抓的病：**同一件事有兩種形狀**。STATE_DIFF 推的是熱狀態原形
   * `{"I": [存量, 容量]}`（後端為雜湊穩定而定的編碼），`GET /units` 給的是
   * `SupplyLevelView[]`。只認得後者的話，串流推來的更新會被整個忽略——
   * 症狀是「水位永遠停在開局值」，與「補給系統沒生效」在畫面上完全一樣。
   */
  const { useLiveState } = await import('~/composables/useLiveState')
  const patches: Record<string, Record<string, unknown>> = {}
  const live = useLiveState({ unitPatches: patches, lastTick: 7 } as never)
  const unit = {
    id: 'u-1',
    supply: [{ supply_class: 'I', on_hand: 400, capacity: 400, fraction: 1 }],
    starved_days: 0,
  } as never

  // ① 沒有 patch → 用 GET /units 的快照。
  assert.equal(live.liveSupply(unit)[0]!.fraction, 1)

  // ② 有 patch → 熱狀態形狀要解得開，而且未編制（容量 0）的類別不列。
  patches['u-1'] = { supply: { I: [100, 400], IX: [0, 0] }, starved_days: 2.5 }
  const levels = live.liveSupply(unit)
  assert.deepEqual(levels.map((s) => s.supply_class), ['I'])
  assert.equal(levels[0]!.fraction, 0.25)
  assert.equal(live.liveStarvedDays(unit), 2.5)
})

test('斷補天數翻成效能倍率的階梯要與後端一致', async () => {
  // 後端只送天數，倍率是前端算的（見 useLabels.STARVATION_STEPS 的說明）。
  // 兩份的一致性由 core/tests/unit/test_supply_point_api.py 守；這裡守的是階梯語義本身
  // ——**是階梯不是內插**，2.9 日還在 ×0.75，滿 3 日才掉到 ×0.5。
  const { starvationModifier } = await import('~/composables/useLabels')
  assert.equal(starvationModifier(0), 1)
  assert.equal(starvationModifier(2.9), 0.75)
  assert.equal(starvationModifier(3), 0.5)
  assert.equal(starvationModifier(99), 0.25)
})

test('地圖編輯器要有補給點庫存欄，而且補給點只給得出「點」', () => {
  /**
   * 抓的病：composable 帶了欄位但樣板沒有控制項＝使用者仍然填不到（同 tempo 那條）。
   * 形狀鈕那一半同樣要守：補給點存成線/面時 `read_point()` 解不開會**整筆略過**，
   * 而它在地圖上與有效的補給點長得一模一樣。
   */
  const src = readSrc('components/cop/MapEditorPanel.vue')
  assert.match(src, /data-testid="draw-supply-stock"/, '繪製表單沒有補給點庫存欄')
  assert.match(src, /data-testid="edit-supply-stock"/, '編輯面板改不了既有補給點的庫存')
  assert.match(src, /data-testid="supply-destroyed"/, '補給點被打掉了畫面上看不出來')
  assert.match(src, /drawPointOnly/, '補給點仍然畫得成線/面（畫得出來、撥交端讀不到）')
})

test('單位資訊卡要顯示補給水位與斷補，且頁面真的把活值傳下去', () => {
  /**
   * 這條盯的是「元件寫好了但沒人用」——本 repo 反覆出現的那類缺陷。
   * 兩端都要驗：卡片有那兩列（不然後勤在 COP 上完全看不見），
   * 而 `cop.vue` 有把 `liveSupply`/`liveStarvedDays` 傳下去（不然卡片永遠拿不到值）。
   */
  const card = readSrc('components/cop/UnitDetailCard.vue')
  assert.match(card, /data-testid="unit-supply"/, '單位卡沒有補給水位那一列')
  assert.match(card, /data-testid="unit-starved"/, '斷補沒有明顯的視覺提示')
  // ⚠ 只在整檔搜 `starvationModifier` 會被 **import 那一行**餵飽——把樣板裡的呼叫拿掉，
  // 測試照樣綠（突變測試抓出來的）。所以只看 `<template>` 那一段。
  const tpl = card.slice(card.indexOf('<template>'))
  assert.match(tpl, /starvationModifier\(/, '斷補只講天數不講效能——操作員無從判斷嚴重程度')

  const page = readSrc('pages/session/[id]/cop.vue')
  assert.match(page, /:live-supply="liveSupply"/, 'cop.vue 沒有把活補給水位傳給單位卡')
  assert.match(page, /:live-starved-days="liveStarvedDays"/)
})
