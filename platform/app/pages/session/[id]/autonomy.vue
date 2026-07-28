<script setup lang="ts">
// 自主推演主控台（O11.7）——為本 session 指派哪些陣營由 AI 控制 + 任務目標 + 決策心跳。
// 存 PUT /sessions/{id}/autonomy（Redis）；sim runner 起跑時讀取並為每個 AI 陣營起決策 worker。
// 限統裁/白軍/管理（後端 RBAC 亦把關）。
import { apiFetch } from '~/composables/useApi'

const route = useRoute()
const sessionId = String(route.params.id)

const auth = useAuthStore()
const canManage = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF', 'ADMIN'].includes(auth.user?.role ?? ''),
)

interface UnitView { id: string, faction: string, designation: string }
interface FactionAI { mission: string, objectives: unknown[] }
interface AutonomyView { factions: Record<string, FactionAI>, heartbeat_s: number }

const loading = ref(true)
const err = ref('')
const saveMsg = ref('')
const saving = ref(false)

const factions = ref<string[]>([]) // 本 session 所有陣營
const enabled = ref<Record<string, boolean>>({}) // faction → 是否 AI 控制
const missions = ref<Record<string, string>>({}) // faction → 任務目標
const heartbeat = ref(45)

async function load() {
  loading.value = true
  err.value = ''
  try {
    const units = await apiFetch<UnitView[]>(`/sessions/${sessionId}/units`)
    factions.value = [...new Set(units.map((u) => u.faction))].sort()
    const cfg = await apiFetch<AutonomyView>(`/sessions/${sessionId}/autonomy`)
    heartbeat.value = cfg.heartbeat_s || 45
    for (const f of factions.value) {
      enabled.value[f] = f in (cfg.factions ?? {})
      missions.value[f] = cfg.factions?.[f]?.mission ?? ''
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
      if (enabled.value[f]) factionsPayload[f] = { mission: missions.value[f] ?? '', objectives: [] }
    }
    if (Object.keys(factionsPayload).length === 0) {
      await apiFetch(`/sessions/${sessionId}/autonomy`, { method: 'DELETE' })
      saveMsg.value = '已清除自主指派——AI 將於數秒內停止'
    } else {
      await apiFetch(`/sessions/${sessionId}/autonomy`, {
        method: 'PUT',
        body: { factions: factionsPayload, heartbeat_s: heartbeat.value },
      })
      saveMsg.value = `已儲存並啟動——AI 將於數秒內接管 ${Object.keys(factionsPayload).join('、')}（回 COP 觀戰）`
    }
  } catch (e) {
    err.value = `儲存失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  if (canManage.value) await load()
  else loading.value = false
})
</script>

<template>
  <main class="autonomy">
    <header>
      <h1>自主推演主控台</h1>
      <a class="back" :href="`/session/${sessionId}/cop`">← 返回 COP</a>
    </header>
    <p class="sub">Session <code>{{ sessionId }}</code>：指派由 AI 控制的陣營，交由 AI 自動對抗。</p>

    <p v-if="!canManage" class="forbidden">僅統裁/白軍/管理可設定自主推演。</p>

    <template v-else>
      <p v-if="loading">載入中…</p>
      <p v-if="err" class="err">{{ err }}</p>

      <template v-if="!loading">
        <section class="card">
          <h2>陣營指派</h2>
          <p class="hint">勾選要交給 AI 控制的陣營，填入該陣營的任務目標（供 AI 指揮官對齊意圖）。</p>
          <div v-for="f in factions" :key="f" class="frow">
            <label class="ftoggle">
              <input v-model="enabled[f]" type="checkbox" :data-testid="`ai-${f}`">
              <span class="fname">{{ f }}</span>
            </label>
            <input
              v-model="missions[f]"
              class="mission"
              :disabled="!enabled[f]"
              placeholder="任務目標（例：殲滅當面之敵、奪取山脊）"
            >
          </div>

          <label class="field">
            <span class="lbl">決策心跳（秒）</span>
            <input v-model.number="heartbeat" type="number" min="10" max="600" class="hb">
          </label>

          <div class="actions">
            <button class="primary" :disabled="saving" data-testid="save-autonomy" @click="save">
              {{ saving ? '儲存中…' : (anyEnabled ? '儲存指派' : '清除指派') }}
            </button>
            <span v-if="saveMsg" class="ok">{{ saveMsg }}</span>
          </div>
        </section>

        <section class="card note">
          <h2>如何啟動 / 運作</h2>
          <ul>
            <li><strong>先決條件</strong>：於 <a href="/system-settings">系統設定</a> 把 AI 模式設為 <strong>AI_BARE 或 AI_FULL</strong>（非 AI_OFF）並填 LLM 後端位址（Ollama/vLLM/雲端）。未設 → AI 不會啟動。</li>
            <li><strong>啟動</strong>：在此勾要交給 AI 的陣營、填任務目標，按「儲存指派」即可——runner 會於<strong>數秒內</strong>自動重啟並讓 AI 接管（戰局熱狀態保留、不中斷；不需新建 session）。</li>
            <li>每個 AI 陣營一條決策迴路（固定心跳）：讀該陣營視角 COP → LLM 產令 → 護欄 → 落單 → 引擎執行。首次產令約需一個心跳（預設 45s）＋ LLM 回應時間。</li>
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
.frow { display: flex; align-items: center; gap: 0.7rem; margin: 0.45rem 0; }
.ftoggle { display: flex; align-items: center; gap: 0.4rem; flex: 0 0 8rem; }
.fname { font-weight: 600; }
.mission { flex: 1 1 auto; background: #0b1220; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.25rem; padding: 0.35rem 0.5rem; font-size: 0.82rem; }
.mission:disabled { opacity: 0.4; }
.field { display: flex; align-items: center; gap: 0.6rem; margin: 0.9rem 0 0.2rem; }
.field .lbl { color: #94a3b8; font-size: 0.85rem; }
.hb { width: 6rem; background: #0b1220; color: #e2e8f0; border: 1px solid #334155; border-radius: 0.25rem; padding: 0.35rem 0.5rem; }
.actions { display: flex; align-items: center; gap: 0.6rem; margin-top: 1rem; }
.actions button { background: transparent; border: 1px solid #334155; color: #cbd5e1; border-radius: 0.3rem; padding: 0.4rem 0.9rem; cursor: pointer; }
.actions button.primary { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
.actions button:disabled { opacity: 0.5; cursor: default; }
.ok { color: #4ade80; font-size: 0.82rem; }
.note ul { margin: 0; padding-left: 1.1rem; color: #cbd5e1; font-size: 0.82rem; line-height: 1.7; }
.note a { color: #60a5fa; }
</style>
