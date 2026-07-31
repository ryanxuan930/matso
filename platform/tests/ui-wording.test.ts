/**
 * UI 用詞防漂移測試——掃 `app/` 的**樣板區**，把「內部代號漏到畫面上」擋在紅燈這一側。
 *
 * 跑法：`cd platform && npm test`（Node 內建 test runner + 型別剝離；harness 說明見 cop-wiring.test.ts）
 *
 * ## 為什麼是掃來源而不是渲染
 *
 * 這一類缺陷沒有行為可以斷言——`{{ s.status }}` 印出 `ACTIVE` 的元件「功能完全正常」，
 * 壞掉的只有使用者讀到的那個字。渲染測試看不出 `ACTIVE` 與「進行中」哪個才對，
 * 但**掃來源**問得出「這裡有沒有經過中文對照表」。
 *
 * ## 只看使用者看得到的東西
 *
 * 程式註解裡引用 SPEC/工作包編號是**對的**（那是給開發者的線索），故只取：
 *   1. 文字節點（標籤之間的內容，去掉 `{{ }}`）
 *   2. `title` / `placeholder` / `aria-label` / `data-tip*` 這些會被使用者讀到的屬性
 *   3. 插值與上述屬性繫結裡的**含中文**字串字面量
 *
 * 第 3 點限定「含中文」是刻意的：運算式裡的純 ASCII 字面量幾乎都是列舉值或 CSS 類名
 * （`o.status === 'EXECUTING' ? '停止' : '取消'`、`intelFidelityLabel('DETECTED')`），
 * 那些是**程式在比對代號**，不是印給人看的字。以「有沒有中文」區分，比逐條白名單耐用。
 */
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const APP_DIR = fileURLToPath(new URL('../app/', import.meta.url))

// ---------------------------------------------------------------- 樣板抽取

function vueFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) vueFiles(path, out)
    else if (path.endsWith('.vue')) out.push(path)
  }
  return out
}

/** 取 `<template>` 區（保留前置換行，讓行號與原檔一致）。 */
function templateBlock(src: string): string {
  const open = src.indexOf('<template>')
  if (open < 0) return ''
  const close = src.lastIndexOf('</template>')
  const body = src.slice(open + '<template>'.length, close < 0 ? src.length : close)
  return src.slice(0, open).replace(/[^\n]/g, '') + body
}

/** 註解換成等長空白——保住行號，同時讓註解裡的 SPEC 引用不進掃描範圍。 */
function stripComments(tpl: string): string {
  return tpl.replace(/<!--[\s\S]*?-->/g, (m) => m.replace(/[^\n]/g, ' '))
}

type Part = { kind: 'text' | 'tag'; body: string; at: number }

/**
 * 把樣板切成文字節點與標籤。
 *
 * **手寫掃描而不是 `/<[^>]*>/`**：`v-if="x > 0"` 的 `>` 在屬性值裡，正則會在那裡就把標籤切斷，
 * 後半截屬性（含 `:class="{ 'a-b': x.some_field }"`）就會被當成「使用者看得到的文字」——
 * 那會生出一整批假紅燈。故遇引號整段跳過。
 */
function splitTemplate(tpl: string): Part[] {
  const parts: Part[] = []
  let i = 0
  while (i < tpl.length) {
    const lt = tpl.indexOf('<', i)
    if (lt < 0) {
      parts.push({ kind: 'text', body: tpl.slice(i), at: i })
      break
    }
    if (lt > i) parts.push({ kind: 'text', body: tpl.slice(i, lt), at: i })
    let j = lt + 1
    let quote = ''
    for (; j < tpl.length; j++) {
      const c = tpl[j]
      if (quote) {
        if (c === quote) quote = ''
      } else if (c === '"' || c === "'") quote = c
      else if (c === '>') break
    }
    parts.push({ kind: 'tag', body: tpl.slice(lt, j + 1), at: lt })
    i = j + 1
  }
  return parts
}

const HAS_CJK = /[一-鿿]/
/** 會被使用者讀到的屬性（tooltip / 佔位字 / 無障礙名稱）。 */
const VISIBLE_ATTRS = /^:?(title|placeholder|aria-label|data-tip|data-tip2)$/

/**
 * 運算式裡的**中文**字串字面量。
 *
 * 先把比較運算的另一側消掉（`x === 'ACTIVE'`），再取字面量；`${...}` 內插的部分不是字面量，
 * 換成空白（那是另一條規則的守備範圍——見 `裸 enum 插值`）。
 */
function chineseLiterals(expr: string): string[] {
  const cleaned = expr
    .replace(/(?:===|!==|==|!=)\s*(['"`])(?:\\.|(?!\1).)*\1/g, ' ')
    .replace(/(['"`])(?:\\.|(?!\1).)*\1\s*(?:===|!==|==|!=)/g, ' ')
  const out: string[] = []
  for (const m of cleaned.matchAll(/(['"`])((?:\\.|(?!\1)[\s\S])*)\1/g)) {
    const text = m[2].replace(/\$\{[^}]*\}/g, ' ')
    if (HAS_CJK.test(text)) out.push(text)
  }
  return out
}

type Visible = { line: number; text: string }

/** 一個檔案裡所有「使用者讀得到的字串」。 */
function visibleText(src: string): Visible[] {
  const tpl = stripComments(templateBlock(src))
  const lineAt = (offset: number) => tpl.slice(0, offset).split('\n').length
  const out: Visible[] = []
  const push = (offset: number, text: string) => {
    const trimmed = text.trim()
    if (trimmed) out.push({ line: lineAt(offset), text: trimmed })
  }

  for (const part of splitTemplate(tpl)) {
    if (part.kind === 'text') {
      // 插值另外處理：文字節點只留標籤之間的實字。
      let cursor = part.at
      for (const seg of part.body.split(/(\{\{[\s\S]*?\}\})/)) {
        if (seg.startsWith('{{')) {
          for (const lit of chineseLiterals(seg.slice(2, -2))) push(cursor, lit)
        } else {
          push(cursor, seg)
        }
        cursor += seg.length
      }
      continue
    }
    for (const m of part.body.matchAll(/([:@]?[\w.-]+)="([^"]*)"/g)) {
      if (!VISIBLE_ATTRS.test(m[1])) continue
      const offset = part.at + (m.index ?? 0)
      if (m[1].startsWith(':')) for (const lit of chineseLiterals(m[2])) push(offset, lit)
      else push(offset, m[2])
    }
  }
  return out
}

const FILES = vueFiles(APP_DIR).sort()
const rel = (p: string) => relative(APP_DIR, p)

/**
 * 已知未修的漏字——**逐條列出實際字串**，不是整檔豁免。
 *
 * 這些落在別條任務卡的檔案裡（本卡的規則是「不碰別人的檔案」）。逐條列的用意是：
 * 同一個檔案裡**新**出現的漏字仍然會轉紅，只有這幾行是既有債。
 * 下方的「已知漏字清單不得留下殘骸」會盯著它們——修好了就必須把條目刪掉，否則測試轉紅。
 */
const KNOWN_GAPS: { file: string; text: string; why: string }[] = [
  {
    file: 'pages/armory.vue',
    text: 'range_max_m → base_ph',
    why: '軍械庫編的就是裝備範本 JSON，鍵名是被編輯的資料本身（另卡：決定要不要全部改中文欄位標題）',
  },
  { file: 'pages/armory.vue', text: '自訂 armor_class', why: '同上' },
  { file: 'pages/armory.vue', text: 'armor_class → P(kill|hit)', why: '同上' },
  { file: 'pages/armory.vue', text: 'range_max_m → p_detect', why: '同上' },
  {
    file: 'pages/armory.vue',
    text: 'AMMO_556, AMMO_AP',
    why: '彈種代號的輸入範例——這個欄位存的就是代號字串（另卡：彈種是否要有中文詞彙表）',
  },
  {
    file: 'pages/system-settings.vue',
    text: 'MATSO_DTED_PATH',
    why: '唯讀系統資訊區顯示的是環境變數名——管理員要照著這個名字去設，翻成中文反而沒用',
  },
]

/**
 * 整檔豁免——**只給「後端代號本身就是被編輯的資料」的畫面**。
 *
 * 白軍的事件注入表單填的就是一則後端事件：事件型別、payload 的鍵。把 `event_type` 翻成中文，
 * 操作員反而打不出後端收得下的東西（該表單自己的警語講的正是「型別不認得就會顯示成原始代號」）。
 * 這種畫面的正確作法是**旁邊附中文說明**，不是把代號藏起來，故整檔排除而不是逐條列。
 */
const EXEMPT_FILES: { file: string; why: string }[] = [
  {
    file: 'components/InjectActionForm.vue',
    why: '白軍任意事件注入器：欄位內容就是後端事件型別與 payload 鍵，代號即資料',
  },
]

function knownGap(file: string, text: string): boolean {
  if (EXEMPT_FILES.some((e) => e.file === file)) return true
  return KNOWN_GAPS.some((g) => g.file === file && text.includes(g.text))
}

/** 逐檔逐條套規則，回可讀的違規清單。 */
function scan(rule: (text: string) => string | null): string[] {
  const hits: string[] = []
  for (const file of FILES) {
    for (const v of visibleText(readFileSync(file, 'utf8'))) {
      const why = rule(v.text)
      if (why && !knownGap(rel(file), v.text)) {
        hits.push(`${rel(file)}:${v.line} → ${why}\n      「${v.text.slice(0, 90)}」`)
      }
    }
  }
  return hits
}

test('已知漏字清單不得留下殘骸', () => {
  /**
   * 豁免清單最常見的失效方式是「東西早就修好了，條目還留著」——留著的條目會把**後來**
   * 出現的同型漏字一起蓋掉。故每一條都必須仍對得上一段真實文字。
   */
  const stale = KNOWN_GAPS.filter(
    (g) => !visibleText(readFileSync(join(APP_DIR, g.file), 'utf8')).some((v) => v.text.includes(g.text)),
  ).map((g) => `${g.file}「${g.text}」`)
  assert.deepEqual(stale, [], `已修好但沒刪掉的豁免條目（請刪除）：\n  ${stale.join('\n  ')}`)
})

// ---------------------------------------------------------------- 規則

test('畫面上不得出現 SPEC 條號／工作包編號／護欄代號', () => {
  /**
   * 抓的病：`（SPEC_V2 WP-D1）`、`（§12.1）`、「會被護欄 G4 剔除」。
   * 這些是開發者的座標，對統裁與參謀是雜訊——而且暗示「要懂這套編號才用得了系統」。
   *
   * ⚠ `G[1-6]` 必須綁著「護欄」兩字才算違規：G1–G6 是本系統的護欄編號，
   * 但 G1/G3/G4 在幕僚編組裡另有意義（人事/作戰/後勤），未來若真要顯示幕僚科別不該被這條擋下。
   */
  const hits = scan((t) => {
    if (/§/.test(t)) return 'SPEC 條號（§）'
    if (/SPEC[_ ][A-Z]/.test(t)) return 'SPEC 文件代號'
    if (/\bWP-[A-Z]\d*/.test(t)) return '工作包編號（WP-）'
    if (/護欄\s*G[1-6]\b|\bG[1-6]\s*護欄/.test(t)) return '護欄內部編號'
    if (/\bO\d+\.\d+\b/.test(t)) return '任務卡號'
    return null
  })
  assert.deepEqual(hits, [], `樣板裡有內部編號漏到畫面上：\n  ${hits.join('\n  ')}`)
})

test('畫面上不得出現後端欄位名（snake_case）', () => {
  /**
   * 抓的病：下令面板寫「須工兵單位（ORBAT 的 `unit_kind=ENGINEER`）」。
   * 使用者在畫面上找不到叫 `unit_kind` 的東西——寫錯的說明比沒有說明更糟。
   */
  const hits = scan((t) => {
    const m = t.match(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/)
    return m ? `後端欄位名 ${m[0]}` : null
  })
  assert.deepEqual(hits, [], `樣板裡有後端欄位名漏到畫面上：\n  ${hits.join('\n  ')}`)
})

/**
 * 可以出現在畫面上的全大寫詞——**軍事/系統慣用縮寫**，不是內部代號。
 * 判準：使用者在別處（教範、既有面板標題）本來就會讀到它。
 */
const ALLOWED_ACRONYMS = new Set([
  'AI', 'LLM', 'RAG', // AI 模式設定頁就是這樣寫的
  'COP', 'AAR', 'ORBAT', 'MSEL', 'ROE', 'C2', 'MGRS', 'WGS84', 'NATO', // 兵推/北約慣用
  'FSO', 'S2', 'S3', 'S4', // 幕僚席位（見 useParticipants 的 SEAT_ROLE_LABELS）
  'CEP', 'RHA', 'RCS', 'PH', 'PK', // 武器諸元（軍械庫）
  'MATSO', // 產品名
  'JSON', 'API', 'URL', 'ID', 'UUID', 'HTTP', 'HTTPS', 'CSV', // 技術欄位說明（想定/軍械庫匯入匯出）
  'DB', 'ENV', 'E2E', 'DTED', 'GPU', 'CPU', // 系統設定的唯讀環境資訊區
])

test('畫面上不得出現裸的英文列舉值', () => {
  /**
   * 抓的病：「銷毀推演資料限系統管理員（ADMIN）」。角色名早就有中文對照表
   * （`useLabels.USER_ROLE_LABELS`），括號裡再補一個後端代號只是把 enum 攤在使用者面前。
   */
  const hits = scan((t) => {
    for (const m of t.matchAll(/\b[A-Z][A-Z0-9]{1,}(?:_[A-Z0-9]+)*\b/g)) {
      if (!ALLOWED_ACRONYMS.has(m[0])) return `裸列舉值／內部代號 ${m[0]}`
    }
    return null
  })
  assert.deepEqual(hits, [], `樣板裡有裸列舉值漏到畫面上：\n  ${hits.join('\n  ')}`)
})

/**
 * 會帶回英文列舉值的欄位名。直接 `{{ x.status }}` 就是把後端 enum 印在畫面上。
 * 新增這類欄位時要一併補中文對照表（`useLabels.ts`），不是回來把欄位名從這裡刪掉。
 */
const ENUM_FIELDS = new Set([
  'status', 'mode', 'kind', 'phase', 'state', 'relation', 'posture',
  'branch', 'unit_level', 'session_role', 'seat_role', 'event_type', 'severity',
])

test('畫面上不得直接插值後端列舉欄位', () => {
  /**
   * 抓的病：演習面板 `{{ s.status }}`、歷史推演 `{{ s.mode }}`、地圖編輯「編輯：{{ kind }}」。
   * `useLabels.ts` 早就有 `SESSION_STATUS_LABELS` / `SESSION_MODE_LABELS`——**漏接而不是缺表**，
   * 而漏接不會有任何錯誤，只會安靜地印出英文。
   */
  const hits: string[] = []
  for (const file of FILES) {
    const tpl = stripComments(templateBlock(readFileSync(file, 'utf8')))
    for (const m of tpl.matchAll(/\{\{([\s\S]*?)\}\}/g)) {
      if (EXEMPT_FILES.some((e) => e.file === rel(file))) continue
      // `LABELS[x.kind] ?? x.kind` 是**規定的**寫法（查無原樣回傳，見 useLabels 模組說明），
      // 右邊那個兜底不是漏接。只要這段插值查過對照表就整段放行。
      if (/LABELS\s*\[|\w+Label\s*\(/.test(m[1])) continue
      // `a.label || c.kind` 這種兜底寫法要逐段看——漏接常常就藏在 `||` 的右邊。
      for (const operand of m[1].split(/\|\||\?\?/)) {
        const expr = operand.trim()
        if (!/^[A-Za-z_$][\w$]*(?:\??\.[\w$]+)+$/.test(expr)) continue
        const last = expr.split('.').pop() as string
        if (!ENUM_FIELDS.has(last)) continue
        const line = tpl.slice(0, m.index ?? 0).split('\n').length
        hits.push(`${rel(file)}:${line} → 未經對照表的列舉欄位 {{ ${expr} }}`)
      }
    }
  }
  assert.deepEqual(hits, [], `樣板直接印了後端列舉：\n  ${hits.join('\n  ')}`)
})

test('下令預檢清單要經過中文對照表', () => {
  /**
   * 抓的病：`useLabels.PRECHECK_LABELS` 建好了卻**零消費者**——下令面板仍逐條印
   * `✗ line_of_sight`。「對照表有沒有被接上」光看對照表本身看不出來（這正是本 repo
   * 「存得進去、讀得回來、實際沒效果」的形狀），故盯讀取端。
   *
   * 預檢項目名是後端執行期字串，靜態掃描讀不到它的值，驗得了的只有「有沒有查表」。
   */
  const panel = readFileSync(join(APP_DIR, 'components/cop/UnitsOrderPanel.vue'), 'utf8')
  assert.match(panel, /precheckLabel\(c\.name\)/, '預檢清單沒有查中文對照表（會印出後端鍵名）')
})

test('編得動卻不影響推演的欄位要標「未實作」', () => {
  /**
   * 抓的病：軍械庫的「抗反制」與整組無人機欄位在 `core/app` **零消費端**——
   * 使用者填了、存了，推演時毫無影響。這比缺功能更糟：缺功能看得出來，
   * 假功能看不出來，而兵推的參數是要拿來論證的。
   *
   * 已有先例：劇本編輯器對 WEGO／IGO_UGO 就是這樣標的。
   *
   * ⚠ 驗證方式是**去 core/app 數消費端**，不是背一份清單——
   * 哪天無人機子系統做出來了，這條會逼人回來把標示拿掉。
   */
  const armory = readFileSync(
    fileURLToPath(new URL('../app/pages/armory.vue', import.meta.url)),
    'utf8',
  )
  assert.match(armory, /data-testid="armory-drone-unimplemented"/, '無人機區塊沒有標未實作')
  assert.match(armory, /抗反制 0–1<span class="dim">（未實作）<\/span>/, '抗反制沒有標未實作')
})
