/**
 * AAR 重播/引用查核的純邏輯測試。
 *
 * 跑法（repo 沒有 vitest，這裡用 Node 內建的 test runner + 型別剝離）：
 *   cd platform && node --test tests/*.test.ts
 *
 * 為什麼要自己接 resolve hook：`~/` 是 Nuxt/Vite 的別名，Node 不認得。
 * 被測的 composable 只有 `vue` 一個執行期相依（其餘皆 `import type`），
 * 所以把 `~/` 映到 `app/` 就能在無瀏覽器、無 Nuxt 執行期的情況下驗證邏輯。
 *
 * 為什麼放在 `tests/` 而不是 `tests/nuxt/`：後者被 `.nuxt/tsconfig.app.json` 收進
 * `vue-tsc --build`，而該組態沒有 `@types/node`，`node:test` 等內建模組會整排標紅。
 */
import assert from 'node:assert/strict'
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

const { ref } = await import('vue')
const { auditCitations } = await import('~/composables/useAar')
const { useAarReplay } = await import('~/composables/useAarReplay')
const { buildUnitFeatures, sidcForOwnUnit } = await import('~/composables/useUnits')
type AarReplayStates = import('~/composables/useAar').AarReplayStates
type AarReport = import('~/composables/useAar').AarReport

// `onBeforeUnmount` 在元件外呼叫會被 Vue 警告（composable 本身沒問題，是測試沒有元件實例）。
// 靜音是為了讓真正的失敗訊息不被噪音蓋掉。
console.warn = () => {}

function statesFixture(): AarReplayStates {
  return {
    units: [
      {
        id: 'u-1',
        designation: '第一營',
        faction: 'BLUE',
        unit_level: 'BATTALION',
        is_fixed: false,
        authorized_strength: 500,
        base_lat: 24.1,
        base_lng: 120.5,
        base_health: 100,
      },
      {
        id: 'u-2',
        designation: '砲兵連',
        faction: 'RED',
        unit_level: 'COMPANY',
        is_fixed: true,
        authorized_strength: 120,
        base_lat: null, // 從未被記錄過座標 → 畫不到圖上，但仍應出現在清單
        base_lng: null,
        base_health: 100,
      },
    ],
    frames: [
      { tick: 5, changes: [{ unit_id: 'u-1', lat: 24.2, lng: 120.6, health: 60, strength: 300 }] },
      { tick: 9, changes: [{ unit_id: 'u-1', health: 0, strength: 140 }] },
    ],
    max_tick: 9,
  }
}

test('重播地圖單位帶番號（E1：後端有回 designation，組 OwnUnit 時整個沒放）', () => {
  const { unitsAt } = useAarReplay(ref(statesFixture()), ref(5))
  const u = unitsAt.value.find((x) => x.id === 'u-1')
  assert.ok(u, '有座標的單位應該出現在地圖資料裡')
  assert.equal(u.designation, '第一營')
  // 真正會不會畫出番號，決定權在 buildUnitFeatures 的 uniqueDesignation 選項——
  // 只斷言欄位有值不算數（值存在但被忽略正是這個 repo 反覆出的病）。
  const { icons, collection } = buildUnitFeatures([u], [], 5)
  assert.ok(
    icons.some((i) => i.options.uniqueDesignation === '第一營'),
    '番號沒有進到符號選項＝地圖上仍是無名方塊',
  )
  // 座標也要一起驗：改寫 unitsAt 時漏掉 lat/lng，圖標會被畫到 (undefined, undefined)，
  // 而上面那三條斷言照樣全綠——「測試全綠、實際沒效果」的典型。
  assert.deepEqual(collection.features[0]!.geometry.coordinates, [120.6, 24.2])
})

test('重播地圖單位帶編制層級（E1：SIDC 第 12 位恆為 "-"，連/營/旅在圖上一模一樣）', () => {
  const { unitsAt } = useAarReplay(ref(statesFixture()), ref(5))
  const u = unitsAt.value.find((x) => x.id === 'u-1')!
  assert.equal(u.unitLevel, 'BATTALION')
  // BATTALION → APP-6A Table IV 的 'F'（SIDC 第 12 位，索引 11）。
  assert.equal(sidcForOwnUnit(u)[11], 'F')
})

test('戰力點累加到重播清單（E1：後端每 tick 送 strength，前端只讀 health 全丟掉）', () => {
  const tick = ref(5)
  const { rosterAt } = useAarReplay(ref(statesFixture()), tick)
  const at5 = rosterAt.value.find((r) => r.id === 'u-1')!
  assert.equal(at5.strength, 300)
  assert.equal(at5.authorizedStrength, 500)
  tick.value = 9
  const at9 = rosterAt.value.find((r) => r.id === 'u-1')!
  assert.equal(at9.strength, 140)
  // 效能% 歸零≠被殲滅：戰力點還有 140，這正是清單必須並列兩個數字的理由。
  assert.equal(at9.health, 0)
})

test('無座標紀錄的單位不出現在地圖、但要出現在清單（否則它就從檢討會上消失了）', () => {
  const { unitsAt, rosterAt } = useAarReplay(ref(statesFixture()), ref(9))
  assert.equal(
    unitsAt.value.some((u) => u.id === 'u-2'),
    false,
  )
  const r = rosterAt.value.find((x) => x.id === 'u-2')!
  assert.equal(r.onMap, false)
  assert.equal(r.designation, '砲兵連')
})

test('重播是純函數：同一份帳本、同一個 tick 必得同一份畫面', () => {
  const tick = ref(9)
  const { unitsAt } = useAarReplay(ref(statesFixture()), tick)
  const a = JSON.stringify(unitsAt.value)
  tick.value = 0
  tick.value = 9
  assert.equal(JSON.stringify(unitsAt.value), a)
})

function reportFixture(invalid: number[]): AarReport {
  return {
    summary: '摘要',
    paragraphs: [
      { text: '第一段', cited_seqs: [1, 2] },
      { text: '第二段', cited_seqs: [7] },
      { text: '第三段', cited_seqs: [] },
    ],
    lessons: [],
    citations: { valid: invalid.length === 0, invalid_seqs: invalid },
  }
}

test('引用查核指得出是哪一段（D-aar：只有「有捏造」三個字，無從查起）', () => {
  const a = auditCitations(reportFixture([7, 2]))
  assert.equal(a.total, 2)
  assert.deepEqual(a.invalidSorted, [2, 7]) // 後端給的順序不保證；畫面列出來要每次一樣
  assert.deepEqual(a.byParagraph.get(0), [2]) // 第一段只有 #2 是假的，#1 仍有效
  assert.deepEqual(a.byParagraph.get(1), [7])
  assert.equal(a.byParagraph.has(2), false) // 沒引用的段落不該被標記
  assert.deepEqual(a.orphans, [])
})

test('後端說捏造、卻沒有段落引用它的 seq 不可被默默吞掉（前後端不同步的訊號）', () => {
  const a = auditCitations(reportFixture([99]))
  assert.deepEqual(a.orphans, [99])
  assert.equal(a.byParagraph.size, 0)
  assert.equal(a.total, 1) // 仍要算進總數，否則畫面會說「全部有效」
})

test('引用全部有效時不得誤報（valid 路徑不能被新邏輯弄壞）', () => {
  const a = auditCitations(reportFixture([]))
  assert.equal(a.total, 0)
  assert.equal(a.byParagraph.size, 0)
  assert.deepEqual(a.orphans, [])
  assert.equal(auditCitations(null).total, 0) // 尚未載入時不得炸掉畫面
})
