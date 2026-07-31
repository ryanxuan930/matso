/**
 * 戰況 feed 的**細節渲染**——後端轉發出來的 `detail` 欄位有沒有真的印到操作員眼前。
 *
 * 跑法：`cd platform && node --test tests/cop-feed.test.ts`
 *
 * 這一檔守的是 UI-P1 的另一半。後端的 `broadcaster._DETAIL_KEYS` 已經把移動里程、
 * 剩油量、觸雷障礙、工兵工期轉發出來了，但前端只印事件型別與原因碼——
 * 資料一路送到瀏覽器然後被丟掉，**而畫面上完全看不出少了東西**。
 * 那正是本 repo 最常見的病的鏡像版：這次不是後端沒做，是前端沒接。
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

const { useCopFeed } = await import('~/composables/useCopFeed')

type UnitView = import('~/composables/useOrders').UnitView
const UNITS = [
  { id: 'u-blue', designation: '1-1 裝甲連' },
  { id: 'u-red', designation: '敵 3 連' },
] as unknown as UnitView[]

function render(event: Record<string, unknown>): string {
  const { formatEvent } = useCopFeed(() => UNITS)
  return formatEvent(event)
}

test('行進耗損要說出走了多遠、用什麼走的、掉了多少戰力', () => {
  // 抓的病：只印「1-1 裝甲連 行進耗損 −3.2」的話，指揮官分不出那是地形磨的
  // 還是自己下強行軍催出來的——而那兩件事的處置完全不同。
  const line = render({
    event_type: 'MOVE_ATTRITION',
    initiator_id: 'u-blue',
    distance_km: 2.4,
    profile: 'TRACKED',
    tempo: 'FORCED',
    strength_before: 100,
    strength_after: 96.8,
  })
  assert.match(line, /1-1 裝甲連/)
  assert.match(line, /2\.4 km/)
  assert.match(line, /履帶/)
  assert.match(line, /強行軍/)
  assert.match(line, /戰力 100\.0→96\.8/)
})

test('拋錨要說出還剩多少油與每公里燒多少', () => {
  // 指揮官據此決定要不要派油罐車、派得及不及。
  const line = render({
    event_type: 'MOVE_HALTED_FUEL',
    initiator_id: 'u-blue',
    reason: 'NO_FUEL',
    fuel_remaining: 0,
    fuel_burn_per_km: 18,
  })
  assert.match(line, /每公里 18\.0/)
})

test('觸雷要說出踩到哪一道障礙、工兵在不在場', () => {
  const line = render({
    event_type: 'MINE_STRIKE',
    initiator_id: 'u-blue',
    label: '1 號雷區',
    engineer: true,
  })
  assert.match(line, /1 號雷區/)
  assert.match(line, /工兵在場/)
})

test('工兵作業要說出是破障還是設障、何時完成', () => {
  const line = render({
    event_type: 'ENGINEER_WORK_STARTED',
    initiator_id: 'u-blue',
    action: 'BREACH',
    eta_tick: 480,
  })
  assert.match(line, /破障/)
  assert.match(line, /預計 T480 完成/)
})

test('原因與細節分開括，不要糊成一團', () => {
  // `MOVE_HALTED_FUEL` 同時有 reason 與剩油量。塞進同一個括號會讀成一句話。
  const line = render({
    event_type: 'MOVE_HALTED_FUEL',
    initiator_id: 'u-blue',
    reason: 'NO_FUEL',
    fuel_burn_per_km: 18,
  })
  assert.match(line, /（.+）［.+］/)
})

test('沒有 detail 的事件不得長出空括號', () => {
  // 絕大多數事件不帶 detail。多印一組空的 ［］ 會讓整條 feed 變成雜訊。
  const line = render({ event_type: 'MOVE_COMPLETED', initiator_id: 'u-blue' })
  assert.ok(!line.includes('［'), `不該有細節括號：${line}`)
  assert.ok(!line.includes('（）'), `不該有空原因括號：${line}`)
})

test('後端轉發的每一個 detail 鍵，前端都要有渲染路徑', () => {
  // **這一條是重點**：兩邊各改各的正是這個洞的成因——後端加了轉發，前端沒人回來接，
  // 而且兩邊的測試都是綠的。這裡把 `broadcaster._DETAIL_KEYS` 讀出來逐一比對，
  // 後端日後再加鍵而前端沒跟上就會紅。
  const py = readFileSync(new URL('../../core/app/state/broadcaster.py', import.meta.url), 'utf8')
  const block = py.match(/_DETAIL_KEYS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/)
  assert.ok(block, '找不到 _DETAIL_KEYS——後端改了結構，這條測試要跟著改')
  const keys = [...block[1]!.matchAll(/^\s*"([a-z_]+)",/gm)].map((m) => m[1]!)
  assert.ok(keys.length >= 15, `只解析到 ${keys.length} 個鍵，正則大概壞了`)

  const ts = readFileSync(new URL('../app/composables/useCopFeed.ts', import.meta.url), 'utf8')
  // 這三個不走 `detailsOf`：reason 走 whyOf（翻成中文原因），
  // order_id 是給「點回那道令」用的關聯鍵、不是給人讀的文字。
  const handledElsewhere = new Set(['reason', 'order_id'])
  const missing = keys.filter((k) => !handledElsewhere.has(k) && !ts.includes(k))
  assert.deepEqual(missing, [], `後端轉發了但前端沒接：${missing.join('、')}`)
})
