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

test('bundle 頂層的 roe / overrides 要能存活一次 roundtrip', () => {
  // 抓的病（UI-P0）：`passthrough` 只保住 `scenario` **裡面**的未知鍵，而 `roe`
  // （陣營交戰規則，例如「本局禁用 MLRS」）與 `overrides`（機動覆寫矩陣）是
  // `scenario` 的**兄弟**，在 bundle 的頂層。於是「未知鍵自動存活」這個保證對它們
  // 完全不適用——用編輯器開一個有 ROE 禁令的想定再存回去，禁令整段消失，
  // 而且畫面上完全看不出東西掉了。統裁以為鎖住了 MLRS，實際上沒有。
  //
  // 後端的 `api/scenarios.ScenarioBundle` 已經修好會收這兩段，洞剩在前端。
  const roe = { BLUE: { forbidden_weapons: ['MLRS'] } }
  const overrides = { mobility_matrix: { MOUNTAIN: { FOOT: 2.5 } } }
  const loaded = importScenario({ ...exportScenario(fullyConfigured()), roe, overrides })

  // ⚠ `roe` 已於 P6 升級為**一級欄位**（可編輯），所以不再走 passthrough；
  // `overrides` 仍是 passthrough（尚無編輯介面）。**兩者的 roundtrip 保證不變**——
  // 那才是這條測試要守的東西，欄位住哪裡是實作細節。
  assert.deepEqual(loaded.roe, roe)
  assert.deepEqual(loaded.bundlePassthrough?.overrides, overrides)

  const saved = exportScenario(loaded)
  assert.deepEqual(saved.roe, roe, 'ROE 在存回去時消失了')
  assert.deepEqual(saved.overrides, overrides, '機動覆寫在存回去時消失了')
})

test('單位上編輯器沒建模的欄位（編裝/補給宣告）要能存活一次 roundtrip', () => {
  // 同一個病的另一半：`importScenario` 只挑 7 個單位欄位，其餘一律丟。
  // 一支帶著完整編裝與補給宣告的單位在編輯器裡開一次再存回去，就只剩番號與座標——
  // 而 ORBAT 樹上看起來一切正常。
  const equipment = [{ template: 'M1A2', quantity: 14 }]
  const attributes = { supply: { I: [3, 3] } }
  const bundle = {
    ...exportScenario(fullyConfigured()),
    orbat: {
      BLUE: {
        faction: 'BLUE',
        units: [
          {
            designation: '1-1 裝甲連',
            unit_level: 'COMPANY',
            lat: 23.7,
            lng: 120.3,
            equipment,
            attributes,
            authorized_strength: 87,
          },
        ],
      },
    },
  }
  const unit = exportScenario(importScenario(bundle)).orbat as Record<
    string,
    { units: Array<Record<string, unknown>> }
  >

  const saved = unit.BLUE!.units[0]!
  assert.deepEqual(saved.equipment, equipment, '編裝在 roundtrip 中消失了')
  assert.deepEqual(saved.attributes, attributes, '補給宣告在 roundtrip 中消失了')
  assert.equal(saved.authorized_strength, 87)
  assert.equal(saved.designation, '1-1 裝甲連') // 建模欄位仍以模型為準
})

test('編裝可編輯，且「沿用預設」與「刻意不帶」分得開', () => {
  /**
   * P6 分水嶺：**沒有編裝的單位打不了仗**（`ENGAGE` 找不到武器、預檢直接不可行），
   * 而編輯器過去產不出帶編裝的單位——那是「編輯器能不能獨立產出一份能打的想定」的關鍵。
   *
   * ⚠ **`undefined` 與 `[]` 是兩件事**（`orbat.schema.json` 寫明了）：
   * 省略＝沿用開局旗標 `seed_default_equipment` 的預設配發；
   * 空陣列＝這支單位刻意什麼都不帶。混為一談的後果是作者把裝備刪光存檔之後，
   * 開局時系統又幫他配回一套預設武器——而畫面上看不出來。
   */
  const withEquip: ScenarioModel = {
    ...emptyScenario(),
    units: [
      {
        faction: 'BLUE',
        designation: '1-1',
        unitLevel: 'COMPANY',
        equipment: [{ template: 'MBT', quantity: 14, ammo: 40 }],
      },
      { faction: 'BLUE', designation: '1-2', unitLevel: 'COMPANY', equipment: [] },
      { faction: 'BLUE', designation: '1-3', unitLevel: 'COMPANY' },
    ],
  }
  const units = (exportScenario(withEquip).orbat as Record<
    string,
    { units: Array<Record<string, unknown>> }
  >).BLUE!.units

  assert.deepEqual(units[0]!.equipment, [{ template: 'MBT', quantity: 14, ammo: 40 }])
  assert.deepEqual(units[1]!.equipment, [], '刻意不帶裝備被寫成「沿用預設」了')
  assert.ok(!('equipment' in units[2]!), '沒宣告的單位不該長出一個空陣列')

  // roundtrip：三種狀態都要活著回來。
  const back = importScenario(exportScenario(withEquip)).units
  assert.deepEqual(back[0]!.equipment, [{ template: 'MBT', quantity: 14, ammo: 40 }])
  assert.deepEqual(back[1]!.equipment, [])
  assert.equal(back[2]!.equipment, undefined)
})

test('編裝範本一律從軍械庫清單挑，不讓人手打', () => {
  // `equipment[].template` 參照的是 `EquipmentTemplate.name`。打錯字要到**開局**才報錯
  // （`_create_declared_equipment` 找不到名稱就整局載不起來），而想定編輯與開局之間
  // 隔著幾天——那時候沒有人記得打了什麼。
  const src = readFileSync(
    fileURLToPath(new URL('../app/pages/scenario-editor.vue', import.meta.url)),
    'utf8',
  )
  assert.match(src, /fetchEquipmentTemplates\(\)/, '沒有去抓軍械庫範本')
  assert.match(src, /:options="templateNames"/, '範本欄位不是下拉——會變成手打')
  assert.match(src, /data-testid="equip-no-templates"/, '沒有範本時要說明，不是給一個空下拉')
})

test('ROE 可編輯，而且未宣告與宣告了空的分得開', () => {
  /**
   * P0 只做到「開了想定再存回去不會把 `roe` 弄丟」，還**不能編**。
   * 而 ROE 是統裁對「這一局怎麼打」的唯一約束手段——寫不出來就只能靠口頭宣布，
   * 事後檢討也無從評量。
   */
  const withRoe: ScenarioModel = {
    ...emptyScenario(),
    roe: {
      default_fire_policy: { BLUE: 'SMALL_ARMS_ONLY' },
      weapon_restrictions: [{ forbid_categories: ['MISSILE'], reason: '本次演習不驗證飛彈鏈' }],
    },
  }
  const saved = exportScenario(withRoe)
  assert.deepEqual((saved.roe as Record<string, unknown>).default_fire_policy, {
    BLUE: 'SMALL_ARMS_ONLY',
  })
  assert.equal(importScenario(saved).roe?.weapon_restrictions?.[0]?.reason, '本次演習不驗證飛彈鏈')

  // 沒宣告 ROE 的想定不該長出一個空的 roe 區段——那會讓讀的人以為作者設定過。
  assert.ok(!('roe' in exportScenario(emptyScenario())))
})

test('武器禁令缺理由要在存檔前擋下來，不是等後端載入時才報', () => {
  // `roe.schema.json` 把 `reason` 設成必填，說明寫著「無理由的限制在事後檢討時無法評量」。
  // 後端只會在**載入時**報錯，那時候作者已經離開編輯器了。
  const src = readFileSync(
    fileURLToPath(new URL('../app/pages/scenario-editor.vue', import.meta.url)),
    'utf8',
  )
  assert.match(src, /roeIssues/, '沒有存檔前檢查')
  assert.match(src, /缺「理由」/)
  assert.match(src, /data-testid="roe-issues"/, '檢查結果沒有顯示出來')
})
