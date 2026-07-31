/**
 * 觸發條件 DSL 的**兩邊對齊**（P6）。
 *
 * 跑法：`cd platform && node --test tests/condition-dsl.test.ts`
 *
 * `useConditionDsl.ts` 的開頭寫著「type 與各欄位須與後端逐字對齊」——
 * 但那只是一句註解，**沒有任何東西在守它**。實況是後端支援 12 種條件型別，
 * 而劇本編輯器只給得出 6 種，缺的那幾種正好包括：
 *
 * - `manual`：白軍控制台的「扣發／跳過」按鈕**早就做好了**，
 *   而劇本產不出 `manual` 條件，於是那兩顆按鈕沒有東西可以按。
 * - `held_for` / `after_ticks_of`：寫不出「持續 N tick」與「A 之後 B」的狀況。
 * - `contact_established`：寫不出以情報為觸發點的狀況。
 *
 * 這一檔把那句註解變成會紅的測試。
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

const { CONDITION_FIELDS, CONDITION_LABELS, emptyCondition } = await import(
  '~/composables/useConditionDsl'
)

/** 從後端原始碼解析 `_CONDITION_FIELDS`——**以後端為準**，不是各自維護一份清單。 */
function backendConditionFields(): Record<string, string[]> {
  const py = readFileSync(
    new URL('../../core/app/scenario/triggers.py', import.meta.url),
    'utf8',
  )
  const block = py.match(/_CONDITION_FIELDS: dict\[str, tuple\[str, \.\.\.\]\] = \{([\s\S]*?)\n\}/)
  assert.ok(block, '找不到 _CONDITION_FIELDS——後端改了結構，這條測試要跟著改')
  const out: Record<string, string[]> = {}
  for (const m of block[1]!.matchAll(/^\s*"([a-z_]+)":\s*\(([^)]*)\),/gm)) {
    out[m[1]!] = [...m[2]!.matchAll(/"([a-z_]+)"/g)].map((f) => f[1]!)
  }
  return out
}

test('前端認得後端支援的每一種條件型別', () => {
  const backend = backendConditionFields()
  assert.ok(Object.keys(backend).length >= 10, '後端型別數解析得太少，正則大概壞了')

  const missing = Object.keys(backend).filter((k) => !(k in CONDITION_FIELDS))
  assert.deepEqual(missing, [], `後端支援但編輯器產不出來：${missing.join('、')}`)

  // 反方向也要守：前端多出一種後端不認得的，想定會在載入時被拒而作者不知道為什麼。
  const extra = Object.keys(CONDITION_FIELDS).filter((k) => !(k in backend))
  assert.deepEqual(extra, [], `編輯器產得出但後端不認得：${extra.join('、')}`)
})

test('每一種型別的必填欄位與後端逐字一致', () => {
  const backend = backendConditionFields()
  for (const [type, fields] of Object.entries(backend)) {
    assert.deepEqual(
      [...(CONDITION_FIELDS as Record<string, readonly string[]>)[type]!].sort(),
      [...fields].sort(),
      `${type} 的必填欄位與後端不一致`,
    )
  }
})

test('每一種型別都有中文標籤與可用的預設值', () => {
  for (const type of Object.keys(CONDITION_FIELDS)) {
    assert.ok(
      (CONDITION_LABELS as Record<string, string>)[type],
      `${type} 沒有中文標籤——下拉會印英文代號`,
    )
    const made = emptyCondition(type as never)
    assert.equal((made as { type: string }).type, type)
    // **預設值要能通過後端的必填檢查**：作者選了型別還沒填內容就存檔是常態，
    // 而後端是載入時驗證——給一個缺欄位的預設等於讓他存下一份載不進去的想定。
    for (const key of (CONDITION_FIELDS as Record<string, readonly string[]>)[type]!) {
      assert.ok(
        key in (made as Record<string, unknown>),
        `${type} 的預設值缺必填欄位 ${key}——存出去的想定會載不進來`,
      )
    }
  }
})

test('manual 是白軍扣發按鈕的唯一來源', () => {
  // 這一條單獨寫出來，因為它是整批裡唯一「後端與 UI 都做好了、只差劇本產不出來」的。
  assert.ok('manual' in CONDITION_FIELDS)
  assert.deepEqual(emptyCondition('manual'), { type: 'manual' })
})

test('條件建構器的下拉涵蓋每一種型別，且不把新型別當成群組', () => {
  /**
   * `ConditionBuilder` 的 `v-else` 分支把**所有未處理的型別**當成群組條件
   * （讀 `.of` 當陣列）。直接加型別會壞掉：`manual` 沒有 `of`，
   * `held_for` / `not` 的 `of` 是**單一條件**不是陣列——那是後端 `validate_condition`
   * 也特別分開處理的地方。
   *
   * 這條釘住兩件事：選單由 `CONDITION_LABELS` 導出（不再手抄），
   * 而單一 `of` 的兩型有自己的分支。
   */
  const src = readFileSync(
    fileURLToPath(new URL('../app/components/ConditionBuilder.vue', import.meta.url)),
    'utf8',
  )
  assert.match(src, /Object\.keys\(CONDITION_LABELS\)/, '型別選單仍是手抄的清單')
  assert.match(
    src,
    /modelValue\.type === 'held_for' \|\| modelValue\.type === 'not'/,
    '單一 of 的兩型沒有自己的分支——會被當成群組而讀爆',
  )
  assert.match(src, /data-testid="cb-manual-note"/, 'manual 沒有說明，作者會以為介面壞了')
})
