/**
 * 劇本編輯器模型的匯出/匯入純邏輯測試（E6/E7）。
 *
 * 跑法（repo 沒有 vitest，用 Node 內建 test runner + 型別剝離）：
 *   cd platform && node --test tests/*.test.ts
 *
 * `~/` 是 Nuxt/Vite 的別名、Node 不認得，故沿用 aar-replay.test.ts 的 resolve hook。
 * `useScenarioEditor` 沒有任何執行期相依（只有 `import type`），所以無需 Nuxt 執行期即可驗。
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

const { emptyScenario, exportScenario, importScenario, SURVIVABILITY_DEFAULTS } = await import(
  '~/composables/useScenarioEditor'
)
type ScenarioModel = import('~/composables/useScenarioEditor').ScenarioModel

/** 五項想定設定全開的模型（各項都用非預設值，才驗得出「有沒有真的帶出去」）。 */
function fullyConfigured(): ScenarioModel {
  return {
    ...emptyScenario(),
    bbox: [119.0, 22.0, 122.5, 25.5],
    tickRateMs: 30000,
    hexResolution: 8,
    aggregateAdjudicationLevel: 'BRIGADE',
    requestQuotas: { AIR_RECON: 4, FIRE_SUPPORT: 2 },
    dayNight: { sunriseMin: 5 * 60 + 40, sunsetMin: 18 * 60 + 20, startMin: 4 * 60 },
    allowFratricide: true,
    indirectFireRequiresApproval: true,
    survivabilityMove: { enabled: true, missionsBeforeMove: 2, minKm: 0.8, maxKm: 3 },
  }
}

test('五項想定設定必須真的寫進匯出的 bundle', () => {
  // 抓的病（E6）：這五個鍵過去只靠 passthrough 保住，編輯器本身沒有任何欄位也沒有匯出路徑——
  // 就算 UI 設了值，只要 exportScenario 沒帶它，存檔出去的想定就是沒設定，而且不會有任何錯誤。
  const s = exportScenario(fullyConfigured()).scenario
  assert.deepEqual(s.request_quotas, { AIR_RECON: 4, FIRE_SUPPORT: 2 })
  assert.deepEqual(s.day_night, { sunrise_min: 340, sunset_min: 1100, start_min: 240 })
  assert.equal(s.allow_fratricide, true)
  assert.equal(s.indirect_fire_requires_approval, true)
  assert.deepEqual(s.survivability_move, {
    enabled: true,
    missions_before_move: 2,
    min_km: 0.8,
    max_km: 3,
  })
})

test('四個 meta 欄位（bbox/tick/hex/彙整層級）改了要能存活一次 roundtrip', () => {
  // 抓的病（E7）：meta 區過去只有名稱/版本/模式，這四項在模型裡有、UI 沒有，
  // 於是用編輯器新建的想定戰場永遠是 emptyScenario() 寫死的 bbox、節奏永遠 60000ms。
  const m = importScenario(exportScenario(fullyConfigured()))
  assert.deepEqual(m.bbox, [119.0, 22.0, 122.5, 25.5])
  assert.equal(m.tickRateMs, 30000)
  assert.equal(m.hexResolution, 8)
  assert.equal(m.aggregateAdjudicationLevel, 'BRIGADE')
})

test('五項想定設定要能存活一次 roundtrip（匯入後仍是同一份設定）', () => {
  // 抓的病：匯出/匯入是編輯器唯一的持久化路徑，任一側漏一個鍵就是「編得動、存不住」。
  const before = fullyConfigured()
  const after = importScenario(exportScenario(before))
  assert.deepEqual(after.requestQuotas, before.requestQuotas)
  assert.deepEqual(after.dayNight, before.dayNight)
  assert.equal(after.allowFratricide, true)
  assert.equal(after.indirectFireRequiresApproval, true)
  assert.deepEqual(after.survivabilityMove, before.survivabilityMove)
})

test('沒設定的想定設定不得憑空出現在匯出結果', () => {
  // 抓的病：把未宣告寫成 false/{} 會讓「作者沒碰過」變成「作者明確關掉」——
  // 想定是給人讀的文件，而且既有想定的 diff 會整片變髒。
  const s = exportScenario(emptyScenario()).scenario
  for (const key of [
    'request_quotas',
    'day_night',
    'allow_fratricide',
    'indirect_fire_requires_approval',
    'survivability_move',
  ]) {
    assert.equal(key in s, false, `${key} 未設定卻被寫進想定`)
  }
})

test('在 UI 關掉的設定不得被 passthrough 的舊值復活', () => {
  // 抓的病：同一份狀態有兩處寫入端。新建模的欄位若忘了加進 MODELLED_SCENARIO_KEYS，
  // 匯入時舊值會留在 passthrough，而 exportScenario 是「先攤開 passthrough 再覆蓋明確欄位」——
  // 使用者在 UI 關掉的設定會被舊值原封不動寫回去，畫面上關了、檔案裡還開著。
  const imported = importScenario({
    scenario: {
      name: 'X',
      version: '1',
      bbox: [120, 23, 121, 24],
      mode: 'REALTIME',
      tick_rate_ms: 60000,
      factions: [{ id: 'BLUE' }],
      victory_conditions: [{ faction: 'BLUE', condition: { type: 'time', at_tick: 10 } }],
      request_quotas: { AIR_RECON: 3 },
      day_night: { sunrise_min: 360, sunset_min: 1080 },
      allow_fratricide: true,
      indirect_fire_requires_approval: true,
      survivability_move: { enabled: true, missions_before_move: 5 },
    },
  })
  assert.equal(imported.passthrough, undefined, '五項設定不該落進 passthrough')

  // 使用者在 UI 把五項全部關掉/清空
  imported.requestQuotas = undefined
  imported.dayNight = undefined
  imported.allowFratricide = false
  imported.indirectFireRequiresApproval = false
  imported.survivabilityMove = undefined

  const s = exportScenario(imported).scenario
  for (const key of [
    'request_quotas',
    'day_night',
    'allow_fratricide',
    'indirect_fire_requires_approval',
    'survivability_move',
  ]) {
    assert.equal(key in s, false, `${key} 已在 UI 關掉，卻被 passthrough 的舊值復活`)
  }
})

test('配額 0 與「不限」是兩件事，0 必須寫得出去', () => {
  // 抓的病：用 falsy 判斷「有沒有填」會把 0 當成沒填 →
  // 想定作者寫「這種申請一張都不准提」，存出去卻變成「不限」，而且 C2 面板顯示（不限）。
  const m: ScenarioModel = { ...emptyScenario(), requestQuotas: { FIRE_SUPPORT: 0 } }
  assert.deepEqual(exportScenario(m).scenario.request_quotas, { FIRE_SUPPORT: 0 })
  assert.deepEqual(importScenario(exportScenario(m)).requestQuotas, { FIRE_SUPPORT: 0 })
})

test('陣地變換停用時整段不寫；啟用時未填的參數不得被補成常數', () => {
  // 抓的病：匯入時把未指定的參數補成前端常數，會讓「作者沒指定（沿用後端預設）」
  // 悄悄變成「作者指定了這個值」——日後後端改預設，這些想定就再也跟不上。
  const off: ScenarioModel = {
    ...emptyScenario(),
    survivabilityMove: { enabled: false, missionsBeforeMove: 9 },
  }
  assert.equal('survivability_move' in exportScenario(off).scenario, false)

  const partial = importScenario({
    scenario: {
      ...(exportScenario(emptyScenario()).scenario as Record<string, unknown>),
      survivability_move: { enabled: true },
    },
  })
  assert.deepEqual(partial.survivabilityMove, { enabled: true })
  assert.deepEqual(exportScenario(partial).scenario.survivability_move, { enabled: true })
  // 常數本身只在 UI 勾選「啟用」時當初值用，必須與後端 fires/survivability.py 的預設一致
  // （core 端另有 test_scenario_editor_ui_coverage.py 逐項比對）。
  assert.deepEqual(SURVIVABILITY_DEFAULTS, { missionsBeforeMove: 3, minKm: 1, maxKm: 2 })
})

test('晝夜只填一半時不得寫出半殘的宣告', () => {
  // 抓的病：schema 要求 sunrise/sunset 同時存在，寫出半殘宣告會讓整份想定存檔失敗，
  // 而錯誤訊息指向 day_night，作者不會知道是哪一格沒填。
  const half = {
    ...emptyScenario(),
    dayNight: { sunriseMin: 360, sunsetMin: Number.NaN },
  } as ScenarioModel
  assert.equal('day_night' in exportScenario(half).scenario, false)
})
