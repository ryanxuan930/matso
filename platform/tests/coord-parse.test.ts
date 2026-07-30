/**
 * 座標輸入解析——問的一律是「操作員打的這個字串，會被讀成哪個點」。
 *
 * 跑法：`cd platform && npm test`（Node 內建 test runner + 型別剝離）。
 *
 * 這一檔守的是一件很具體的事：**座標打錯在兵推裡的後果是火力落在別的地方**。
 * 所以每一條都驗實際換算出來的緯經度，而不只是「有沒有回 ok」；
 * 認不得的輸入也要驗它**明確失敗**——回一個瞎猜的座標比拒絕危險得多。
 */
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const APP_DIR = new URL('../app/', import.meta.url)
const MGRS_SHIM = new URL('./_mgrs-esm.mjs', import.meta.url).href

registerHooks({
  resolve(spec, ctx, next) {
    // ⚠ **轉接層自己 require('mgrs') 時不可以再被導回來**——那會變成自我循環
    // （ERR_REQUIRE_CYCLE_MODULE）。用 parentURL 把它排除掉。
    if (spec === 'mgrs' && ctx.parentURL !== MGRS_SHIM) {
      return { url: MGRS_SHIM, shortCircuit: true }
    }
    if (spec.startsWith('~/')) {
      return {
        url: pathToFileURL(fileURLToPath(new URL(`${spec.slice(2)}.ts`, APP_DIR))).href,
        shortCircuit: true,
      }
    }
    return next(spec, ctx)
  },
})

const { parseCoordInput } = await import('../app/composables/useCoordParse.ts')

/** 台中霧峰一帶，三種寫法指同一個點——這是本檔的錨點。 */
const ANCHOR = { lat: 24.0972, lng: 120.1705 }

function ok(raw: string) {
  const r = parseCoordInput(raw)
  assert.equal(r.ok, true, `應該解析得出來：${raw}（${r.ok ? '' : r.reason}）`)
  return r.ok ? r.value : null!
}

function fails(raw: string) {
  const r = parseCoordInput(raw)
  assert.equal(r.ok, false, `應該要拒絕：${raw}`)
  return r.ok ? '' : r.reason
}

test('十進位度：逗號與空白都收，緯度在前', () => {
  for (const raw of ['24.0972, 120.1705', '24.0972 120.1705', '  24.0972,120.1705  ']) {
    const v = ok(raw)
    assert.equal(v.format, 'DECIMAL')
    assert.ok(Math.abs(v.lat - ANCHOR.lat) < 1e-6, raw)
    assert.ok(Math.abs(v.lng - ANCHOR.lng) < 1e-6, raw)
  }
})

test('十進位度：負值（南半球/西半球）', () => {
  const v = ok('-33.8688, 151.2093')
  assert.ok(Math.abs(v.lat + 33.8688) < 1e-6)
  assert.ok(Math.abs(v.lng - 151.2093) < 1e-6)
})

test('MGRS：緊接與帶空白都收，且換算回同一個點', () => {
  for (const raw of ['51QTG1234567890', '51Q TG 12345 67890', '51qtg1234567890']) {
    const v = ok(raw)
    assert.equal(v.format, 'MGRS')
    // 1 公尺精度的 MGRS → 與錨點差距在百公尺內（同一個 100km 方格內的同一位置）
    assert.ok(Math.abs(v.lat - ANCHOR.lat) < 0.01, `${raw} lat=${v.lat}`)
    assert.ok(Math.abs(v.lng - ANCHOR.lng) < 0.01, `${raw} lng=${v.lng}`)
  }
})

test('度分秒：符號與純空白兩種寫法，半球字母在前或在後都收', () => {
  for (const raw of [`24°05'50"N 120°10'14"E`, `N24 05 50 E120 10 14`]) {
    const v = ok(raw)
    assert.equal(v.format, 'DMS')
    assert.ok(Math.abs(v.lat - 24.0972) < 0.001, `${raw} lat=${v.lat}`)
    assert.ok(Math.abs(v.lng - 120.1706) < 0.001, `${raw} lng=${v.lng}`)
  }
})

test('度分秒：南/西半球取負', () => {
  const v = ok(`33°52'08"S 151°12'33"E`)
  assert.ok(v.lat < 0, `南緯應為負，得到 ${v.lat}`)
  assert.ok(v.lng > 0)
})

test('MGRS 的數字位數是奇數 → 明確拒絕，不猜', () => {
  const reason = fails('51QTG123456789')
  assert.match(reason, /偶數/)
})

test('超出範圍的十進位度 → 明確拒絕並回報讀到什麼', () => {
  const reason = fails('999, 999')
  assert.match(reason, /超出範圍/)
  assert.match(reason, /999/)
})

test('認不得的字串 → 拒絕，而且訊息要說得出支援哪些格式', () => {
  const reason = fails('這不是座標')
  assert.match(reason, /MGRS/)
  assert.match(reason, /度分秒/)
})

test('空字串 → 拒絕（不是回 0,0）', () => {
  const reason = fails('   ')
  assert.match(reason, /請輸入/)
})

test('MGRS 不會被十進位度先吃掉', () => {
  // 解析順序若把最寬鬆的十進位度放前面，`51QTG1234567890` 裡的數字會先被抓走
  // ——結果是一個完全錯誤的點，而且**看起來像成功了**。
  const v = ok('51QTG1234567890')
  assert.equal(v.format, 'MGRS')
})

test('沒有半球字母的三段數字不會被誤讀成度分秒', () => {
  // `24 05 50` 三個數字若被當成 DMS，會變成 24°05'50"——但沒有 N/S 就不知道是哪一邊。
  // 應該落到十進位度（取前兩個數）或拒絕，而不是猜一個半球。
  const r = parseCoordInput('24 05 50')
  assert.equal(r.ok, true)
  assert.equal(r.ok && r.value.format, 'DECIMAL')
})
