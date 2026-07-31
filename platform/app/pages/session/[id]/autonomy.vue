<script setup lang="ts">
// 自主推演主控台（O11.7）——為本 session 指派哪些陣營由 AI 控制 + 任務目標 + 決策心跳。
// 存 PUT /sessions/{id}/autonomy（Redis）；sim runner 起跑時讀取並為每個 AI 陣營起決策 worker。
// 限統裁/白軍/管理（後端 RBAC 亦把關）。
import { apiFetch } from '~/composables/useApi'
import { describeAiStatus, useAiStatus } from '~/composables/useAiStatus'

const route = useRoute()
const sessionId = String(route.params.id)

const auth = useAuthStore()
const canManage = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF', 'ADMIN'].includes(auth.user?.role ?? ''),
)

interface UnitView { id: string, faction: string, designation: string }
/** 後端 FactionAI.objectives 是 `list[dict]`（core/app/api/autonomy.py），逐條原樣進 AI 提示詞。 */
type Objective = Record<string, unknown>
interface FactionAI { mission: string, objectives: Objective[] }
interface AutonomyView {
  factions: Record<string, FactionAI>
  heartbeat_s: number
  ai_ground_truth?: boolean
}

/** 一列任務目標的編輯狀態。`extra` 保住手工（curl/舊資料）設定的結構化欄位，存檔時原樣帶回。 */
interface ObjectiveRow { id: number, text: string, extra: Objective }

const loading = ref(true)
const err = ref('')
const saveMsg = ref('')
const saving = ref(false)

const factions = ref<string[]>([]) // 本 session 所有陣營
const enabled = ref<Record<string, boolean>>({}) // faction → 是否 AI 控制
const missions = ref<Record<string, string>>({}) // faction → 任務敘述（指揮官意圖，一句話）
const objectives = ref<Record<string, ObjectiveRow[]>>({}) // faction → 逐條任務目標
const heartbeat = ref(45)
// WP-A1 對照實驗開關。**每次 PUT 都要帶**：後端 pydantic 以 default false 補齊未帶的欄位，
// 漏帶就等於白軍每按一次儲存就把實驗設定靜默清掉。
const groundTruth = ref(false)

let rowSeq = 0
function newRow(text = '', extra: Objective = {}): ObjectiveRow {
  rowSeq += 1
  return { id: rowSeq, text, extra }
}

/**
 * 後端存的 objective dict → 可編輯列。
 * 認 `description`／`text` 兩個鍵當敘述；其餘鍵（例如手工設的 type/target）收進 extra 保留，
 * 否則白軍在這頁按一次儲存就會把 curl 設過的結構化目標吃掉。
 */
function toRows(raw: unknown): ObjectiveRow[] {
  if (!Array.isArray(raw)) return []
  return raw.map((o) => {
    if (typeof o === 'string') return newRow(o)
    if (o && typeof o === 'object') {
      const obj = o as Record<string, unknown>
      // 只把「真的拿來當敘述用」的那個鍵抽走，其餘（含另一個沒用到的鍵）留在 extra，
      // 否則存檔會把它吃掉——這頁是整份覆寫，吃掉就永久消失。
      const key = typeof obj.description === 'string' ? 'description' : typeof obj.text === 'string' ? 'text' : null
      const { [key ?? '']: used, ...extra } = obj
      return newRow(key ? String(used) : '', extra)
    }
    return newRow()
  })
}

/** 可編輯列 → 後端 objective dict。空白列（無敘述也無結構欄位）丟掉，不送雜訊給 AI。 */
function toPayload(rows: ObjectiveRow[] | undefined): Objective[] {
  const out: Objective[] = []
  for (const r of rows ?? []) {
    const text = r.text.trim()
    if (!text && Object.keys(r.extra).length === 0) continue
    out.push(text ? { ...r.extra, description: text } : { ...r.extra })
  }
  return out
}

function addObjective(f: string) {
  ;(objectives.value[f] ??= []).push(newRow())
}

function removeObjective(f: string, id: number) {
  const rows = objectives.value[f]
  if (rows) objectives.value[f] = rows.filter((r) => r.id !== id)
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    const units = await apiFetch<UnitView[]>(`/sessions/${sessionId}/units`)
    factions.value = [...new Set(units.map((u) => u.faction))].sort()
    const cfg = await apiFetch<AutonomyView>(`/sessions/${sessionId}/autonomy`)
    heartbeat.value = cfg.heartbeat_s || 45
    groundTruth.value = cfg.ai_ground_truth === true
    for (const f of factions.value) {
      enabled.value[f] = f in (cfg.factions ?? {})
      missions.value[f] = cfg.factions?.[f]?.mission ?? ''
      objectives.value[f] = toRows(cfg.factions?.[f]?.objectives)
    }
  } catch (e) {
    err.value = `載入失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    loading.value = false
  }
}

const anyEnabled = computed(() => Object.values(enabled.value).some(Boolean))

async function save() {
  saving.value = true
  saveMsg.value = ''
  err.value = ''
  try {
    const factionsPayload: Record<string, FactionAI> = {}
    for (const f of factions.value) {
      if (enabled.value[f]) {
        factionsPayload[f] = {
          mission: missions.value[f] ?? '',
          objectives: toPayload(objectives.value[f]),
        }
      }
    }
    if (Object.keys(factionsPayload).length === 0) {
      await apiFetch(`/sessions/${sessionId}/autonomy`, { method: 'DELETE' })
      saveMsg.value = '已清除自主指派——AI 將於數秒內停止'
    } else {
      await apiFetch(`/sessions/${sessionId}/autonomy`, {
        method: 'PUT',
        body: {
          factions: factionsPayload,
          heartbeat_s: heartbeat.value,
          ai_ground_truth: groundTruth.value,
        },
      })
      saveMsg.value = `已儲存並啟動——AI 將於數秒內接管 ${Object.keys(factionsPayload).join('、')}（回 COP 觀戰）`
    }
  } catch (e) {
    err.value = `儲存失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    saving.value = false
  }
}

// AI 決策狀態（#79）：主控台是白軍盯 AI 是否還活著的地方，故顯示比 COP 狀態列更完整的細節。
const { factions: aiRaw, start: startAiStatus } = useAiStatus(() => sessionId)
const aiRows = computed(() => aiRaw.value.map(describeAiStatus))

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  if (canManage.value) {
    await load()
    startAiStatus()
  } else {
    loading.value = false
  }
})
</script>

<template>
  <main class="autonomy">
    <header>
      <h1>自主推演主控台</h1>
      <a class="back" :href="`/session/${sessionId}/cop`">← 返回 COP</a>
    </header>
    <p class="sub">推演局 <code>{{ sessionId }}</code>：指派由 AI 控制的陣營，交由 AI 自動對抗。</p>

    <p v-if="!canManage" class="forbidden">僅統裁/白軍/管理可設定自主推演。</p>

    <template v-else>
      <p v-if="loading">載入中…</p>
      <p v-if="err" class="err" data-testid="autonomy-err">{{ err }}</p>

      <template v-if="!loading">
        <section class="card">
          <h2>陣營指派</h2>
          <p class="hint">
            勾選要交給 AI 控制的陣營，填入該陣營的任務敘述（指揮官意圖）與逐條任務目標——兩者都會逐字進入 AI 指揮官的提示詞。
          </p>
          <div v-for="f in factions" :key="f" class="fblock">
            <div class="frow">
              <label class="ftoggle">
                <input v-model="enabled[f]" type="checkbox" :data-testid="`ai-${f}`">
                <span class="fname">{{ f }}</span>
              </label>
              <input
                v-model="missions[f]"
                class="mission"
                :data-testid="`ai-mission-${f}`"
                :disabled="!enabled[f]"
                placeholder="任務敘述（例：於 06 時前肅清當面之敵並確保山脊）"
              >
            </div>

            <div v-if="enabled[f]" class="objs">
              <div class="objs-head">
                <span class="objs-title">
                  任務目標（{{ objectives[f]?.length ?? 0 }} 條，由上而下為優先順序）
                </span>
                <button class="mini" :data-testid="`obj-add-${f}`" @click="addObjective(f)">
                  ＋ 增列目標
                </button>
              </div>
              <p v-if="!objectives[f]?.length" class="objs-empty">
                尚未律定目標——AI 僅依上方任務敘述行動。
              </p>
              <div v-for="(o, i) in objectives[f]" :key="o.id" class="orow">
                <span class="onum">{{ i + 1 }}</span>
                <input
                  v-model="o.text"
                  class="otext"
                  :data-testid="`obj-${f}-${i}`"
                  placeholder="例：奪取並確保 218 高地，掩護主力左翼"
                >
                <span
                  v-if="Object.keys(o.extra).length"
                  class="oextra"
                  :title="JSON.stringify(o.extra)"
                >附結構欄位：{{ Object.keys(o.extra).join('、') }}</span>
                <button
                  class="mini danger"
                  :data-testid="`obj-del-${f}-${i}`"
                  @click="removeObjective(f, o.id)"
                >
                  移除
                </button>
              </div>
            </div>
          </div>

          <label class="field">
            <span class="lbl">決策心跳（秒）</span>
            <input
              v-model.number="heartbeat"
              type="number"
              min="10"
              max="600"
              class="hb"
              data-testid="ai-heartbeat"
            >
          </label>

          <div class="actions">
            <button class="primary" :disabled="saving" data-testid="save-autonomy" @click="save">
              {{ saving ? '儲存中…' : (anyEnabled ? '儲存指派' : '清除指派') }}
            </button>
            <span v-if="saveMsg" class="ok" data-testid="autonomy-save-msg">{{ saveMsg }}</span>
          </div>
        </section>

        <section v-if="aiRows.length" class="card">
          <h2>AI 決策狀態</h2>
          <p class="hint">
            每 8 秒回報一次。「已思考」持續攀升卻遲遲沒有下達，代表 LLM 後端過慢或該陣營決策卡住。
          </p>
          <div v-for="a in aiRows" :key="a.faction" class="arow" :data-testid="`ai-detail-${a.faction}`">
            <b class="afac">{{ a.faction }}</b>
            <span class="astate" :class="a.state">{{ a.stateLabel }}</span>
            <span v-if="a.state === 'thinking'" class="ameta" :class="{ warn: a.stalled }">
              已思考 <b :data-testid="`ai-thinking-${a.faction}`">{{ a.thinkingFor }}</b>
              <span v-if="a.stalled" class="badge" :title="`已超過一個決策心跳（${a.heartbeatS} 秒）`">逾時</span>
            </span>
            <span v-else-if="a.state === 'idle'" class="ameta">
              下一次決策 <b>{{ a.countdown }}</b>
              <template v-if="a.sinceLastDecision"> ・ 上次決策於 {{ a.sinceLastDecision }} 前</template>
            </span>
            <span v-else class="ameta off">決策程序未上線或已逾時</span>
            <span class="acount">
              累計決策 <b :data-testid="`ai-cycles-${a.faction}`">{{ a.cycles ?? '—' }}</b> 次
              ・ 上一次下達 <b :data-testid="`ai-last-${a.faction}`">{{ a.lastSubmitted ?? '—' }}</b> 道
            </span>
          </div>
        </section>

        <section class="card danger-card">
          <h2>對照實驗設定</h2>
          <label class="gt">
            <input v-model="groundTruth" type="checkbox" data-testid="ai-ground-truth">
            <span class="gt-body">
              <b>AI 使用 ground truth 敵情（關閉 AI 的戰場迷霧）</b>
              <small>
                開啟後，AI 指揮官直接讀取全場敵軍真實位置，不受偵測、情報時效與通聯限制——
                與人類指揮官的資訊條件<strong>不對等</strong>。僅供「有／無戰場迷霧」的對照實驗使用，
                正常推演一律保持關閉。
              </small>
            </span>
          </label>
        </section>

        <section class="card note">
          <h2>如何啟動 / 運作</h2>
          <ul>
            <!-- AI 模式的三個選項在系統設定頁是中文（見該頁的 AI_MODE_LABEL），此處照那三個中文說，
                 使用者才對得上要點哪一個；印後端代號等於要人自己去翻譯。 -->
            <li><strong>先決條件</strong>：於 <a href="/system-settings">系統設定</a> 把 AI 模式從「關閉」改為 <strong>「啟用・無 RAG」或「完整」</strong>並填 LLM 後端位址（Ollama/vLLM/雲端）。未設 → AI 不會啟動。</li>
            <li><strong>啟動</strong>：在此勾要交給 AI 的陣營、填任務敘述與目標，按「儲存指派」即可——推演引擎會於<strong>數秒內</strong>自動重啟並讓 AI 接管（戰局熱狀態保留、不中斷；不需另開新局）。</li>
            <li>每個 AI 陣營一條決策迴路（固定心跳）：讀該陣營視角 COP → LLM 產令 → 護欄審查 → 下達 → 引擎執行。首次產令約需一個心跳（預設 45s）＋ LLM 回應時間。</li>
            <li>勝負由確定性規則判定（預設「最後存活陣營」），底定即自動收場並記入戰況事件。</li>
            <li>回 <a :href="`/session/${sessionId}/cop`">COP</a> 觀戰：AI 下的令會出現在指令列，護欄干預與勝負底定會出現在戰況事件。</li>
          </ul>
        </section>
      </template>
    </template>
  </main>
</template>

<style scoped>
.autonomy { max-width: 46rem; margin: 0 auto; padding: 2rem 1rem; color: #e2e8f0; }
header { display: flex; align-items: baseline; justify-content: space-between; }
h1 { margin: 0; font-size: 1.5rem; }
.back { color: #60a5fa; text-decoration: none; font-size: 0.85rem; }
.sub { color: #94a3b8; font-size: 0.85rem; margin: 0.4rem 0 1.2rem; }
.sub code, code { background: #1e293b; padding: 0.05rem 0.3rem; border-radius: 0.2rem; font-size: 0.8rem; }
.forbidden { color: #f87171; }
.err { color: #f87171; }
.card { background: #0f172a; border: 1px solid #1e293b; border-radius: 0.5rem; padding: 1rem 1.1rem; margin-bottom: 1.1rem; }
.card h2 { margin: 0 0 0.6rem; font-size: 1.05rem; }
.hint { color: #94a3b8; font-size: 0.78rem; margin: 0 0 0.7rem; }
.fblock { border-bottom: 1px solid #1e293b; padding-bottom: 0.5rem; margin-bottom: 0.5rem; }
.fblock:last-of-type { border-bottom: none; }
.frow { display: flex; align-items: center; gap: 0.7rem; margin: 0.45rem 0; }
.ftoggle { display: flex; align-items: center; gap: 0.4rem; flex: 0 0 8rem; }
.fname { font-weight: 600; }
.mission { flex: 1 1 auto; background: #0b1220; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.25rem; padding: 0.35rem 0.5rem; font-size: 0.82rem; }
.mission:disabled { opacity: 0.4; }
.objs { margin: 0.2rem 0 0.6rem 8.7rem; }
.objs-head { display: flex; align-items: center; gap: 0.6rem; }
.objs-title { color: #94a3b8; font-size: 0.75rem; }
.objs-empty { color: #64748b; font-size: 0.75rem; margin: 0.3rem 0 0; }
.orow { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.3rem; }
.onum { color: #64748b; font-size: 0.75rem; width: 1.2rem; text-align: right; }
.otext { flex: 1 1 auto; background: #0b1220; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.25rem; padding: 0.3rem 0.5rem; font-size: 0.8rem; }
.oextra { color: #fbbf24; font-size: 0.7rem; white-space: nowrap; }
.mini { background: transparent; border: 1px solid #334155; color: #cbd5e1; border-radius: 0.25rem; padding: 0.15rem 0.5rem; font-size: 0.72rem; cursor: pointer; }
.mini.danger { border-color: #7f1d1d; color: #fca5a5; }
.field { display: flex; align-items: center; gap: 0.6rem; margin: 0.9rem 0 0.2rem; }
.field .lbl { color: #94a3b8; font-size: 0.85rem; }
.hb { width: 6rem; background: #0b1220; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.25rem; padding: 0.35rem 0.5rem; }
.actions { display: flex; align-items: center; gap: 0.6rem; margin-top: 1rem; }
.actions button { background: transparent; border: 1px solid #334155; color: #cbd5e1; border-radius: 0.3rem; padding: 0.4rem 0.9rem; cursor: pointer; }
.actions button.primary { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
.actions button:disabled { opacity: 0.5; cursor: default; }
.ok { color: #4ade80; font-size: 0.82rem; }
.arow { display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem; font-size: 0.8rem; padding: 0.35rem 0; border-top: 1px solid #1e293b; }
.arow:first-of-type { border-top: none; }
.afac { color: #f1f5f9; flex: 0 0 6rem; }
.astate { border: 1px solid #334155; border-radius: 999px; padding: 0.05rem 0.5rem; font-size: 0.72rem; }
.astate.thinking { border-color: #2563eb; color: #bfdbfe; }
.astate.idle { color: #cbd5e1; }
.astate.offline { color: #94a3b8; opacity: 0.7; }
.ameta { color: #cbd5e1; }
.ameta.off { color: #94a3b8; }
.ameta.warn { color: #fca5a5; }
.badge { margin-left: 0.35rem; background: #7f1d1d; color: #fecaca; border-radius: 0.2rem; padding: 0.02rem 0.35rem; font-size: 0.7rem; }
.acount { color: #94a3b8; margin-left: auto; font-size: 0.75rem; }
.danger-card { border-color: #7f1d1d; }
.gt { display: flex; align-items: flex-start; gap: 0.55rem; }
.gt-body { display: flex; flex-direction: column; gap: 0.25rem; }
.gt-body b { color: #fca5a5; font-size: 0.85rem; }
.gt-body small { color: #94a3b8; font-size: 0.76rem; line-height: 1.6; }
.gt-body strong { color: #e2e8f0; }
.note ul { margin: 0; padding-left: 1.1rem; color: #cbd5e1; font-size: 0.82rem; line-height: 1.7; }
.note a { color: #60a5fa; }
</style>
