<script setup lang="ts">
import type { components } from '~/types/api'
// 系統設定（#54）——全系統通用參數。AI 模式 + LLM 後端可編輯（存 DB）；ENV/容器層參數唯讀檢視。
// 限統裁/白軍/管理（後端 RBAC 亦把關）。
import { apiFetch } from '~/composables/useApi'

interface SysConfig {
  ai: {
    ai_mode: string
    llm_base_url: string
    llm_model: string
    llm_api_key_set: boolean
    ai_modes: string[]
  }
  readonly: {
    env: string
    ai_mode_env_default: string
    terrain_grpc_target: string
    weather_grpc_target: string
    redis_url: string
    stub_gateway: boolean
    ai_loop_wired: boolean
  }
  sim: SimParams
}
// #93 推演參數（契約 SimParamsView）——改了會改變推演物理，故獨立一區並標明生效時機。
type SimParams = components['schemas']['SimParamsView']

const AI_MODE_LABEL: Record<string, string> = {
  AI_OFF: '關閉（傳統兵推，各陣營由人操作）',
  AI_BARE: '啟用・無 RAG（模型自身判斷，引用必空）',
  AI_FULL: '完整（RAG + 引用查核）',
}

const auth = useAuthStore()
const canManage = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF', 'ADMIN'].includes(auth.user?.role ?? ''),
)

const cfg = ref<SysConfig | null>(null)
const loading = ref(true)
const err = ref('')

// 可編輯欄位（本地草稿）
const aiMode = ref('AI_OFF')
const llmBaseUrl = ref('')
const llmModel = ref('')
const llmApiKey = ref('') // 空＝不變（若已設定）；填了才送
const apiKeyAlreadySet = ref(false)

const saving = ref(false)
const saveMsg = ref('')
const testing = ref(false)
const testMsg = ref('')
const testOk = ref<boolean | null>(null)

// #93 推演參數編輯狀態（載入時由後端帶入預設）。
const sim = ref<SimParams | null>(null)

function applyConfig(c: SysConfig) {
  cfg.value = c
  aiMode.value = c.ai.ai_mode
  llmBaseUrl.value = c.ai.llm_base_url
  llmModel.value = c.ai.llm_model
  apiKeyAlreadySet.value = c.ai.llm_api_key_set
  llmApiKey.value = ''
  sim.value = c.sim ? { ...c.sim, march_attrition: { ...c.sim.march_attrition } } : null
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    applyConfig(await apiFetch<SysConfig>('/system/config'))
  } catch (e) {
    err.value = `載入失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  saveMsg.value = ''
  err.value = ''
  try {
    const body: Record<string, unknown> = {
      ai_mode: aiMode.value,
      llm_base_url: llmBaseUrl.value.trim(),
      llm_model: llmModel.value.trim(),
    }
    // 只有使用者填了才送 api_key（空＝保留原值）。
    if (llmApiKey.value) body.llm_api_key = llmApiKey.value
    if (sim.value) body.sim = sim.value
    applyConfig(await apiFetch<SysConfig>('/system/config', { method: 'PUT', body }))
    saveMsg.value = '已儲存'
  } catch (e) {
    err.value = `儲存失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testMsg.value = ''
  testOk.value = null
  try {
    const r = await apiFetch<{ ok: boolean; detail: string; latency_ms: number | null }>(
      '/system/config/test-llm',
      { method: 'POST', body: { base_url: llmBaseUrl.value.trim(), model: llmModel.value.trim(), api_key: llmApiKey.value || undefined } },
    )
    testOk.value = r.ok
    testMsg.value = `${r.detail}${r.latency_ms != null ? `（${r.latency_ms} ms）` : ''}`
  } catch (e) {
    testOk.value = false
    testMsg.value = `測試失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  } finally {
    testing.value = false
  }
}

// LLM 後端快速預設。Google 走 Gemini API 的 OpenAI 相容端點（Gemma 也在此 API）。
function applyPreset(kind: 'ollama' | 'google') {
  if (kind === 'ollama') {
    llmBaseUrl.value = 'http://host.docker.internal:11434'
  } else {
    llmBaseUrl.value = 'https://generativelanguage.googleapis.com/v1beta/openai'
    llmModel.value = 'gemma-4-31b-it'
  }
}

// 是否為雲端後端（非本機）——用於資料外送警示。
const isCloudBackend = computed(() => {
  const u = llmBaseUrl.value.trim().toLowerCase()
  if (!u) return false
  return !/(localhost|127\.0\.0\.1|host\.docker\.internal|0\.0\.0\.0|::1)/.test(u)
})

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  if (canManage.value) await load()
  else loading.value = false
})
</script>

<template>
  <main class="settings">
    <header>
      <h1>系統設定</h1>
      <a class="back" href="/lobby" data-testid="nav-lobby">← 返回首頁</a>
    </header>

    <p v-if="!canManage" class="forbidden" data-testid="settings-forbidden">
      僅統裁/白軍/管理可設定系統參數。
    </p>

    <template v-else>
      <p v-if="loading" data-testid="settings-loading">載入中…</p>
      <p v-if="err" class="err" data-testid="settings-err">{{ err }}</p>

      <template v-if="cfg">
        <!-- AI 設定（可編輯） -->
        <section class="card" data-testid="ai-settings">
          <h2>AI 設定</h2>

          <label class="field">
            <span class="lbl">AI 模式（目前生效）</span>
            <select v-model="aiMode" data-testid="ai-mode">
              <option v-for="m in cfg.ai.ai_modes" :key="m" :value="m">
                {{ AI_MODE_LABEL[m] ?? m }}（{{ m }}）
              </option>
            </select>
          </label>
          <p class="hint">
            這是<strong>目前生效</strong>的 AI 模式（存於資料庫、全系統適用）。下方「系統資訊」的
            <em>AI 模式環境預設</em> 只是環境變數 fallback——<strong>未在此設定時</strong>才會採用，兩者不同屬正常。
          </p>

          <div class="subhd">LLM 後端（OpenAI 相容，如 Ollama / vLLM / Google AI Studio）</div>
          <div class="presets">
            <span class="plabel">快速預設：</span>
            <button type="button" data-testid="preset-ollama" @click="applyPreset('ollama')">
              Ollama（本機）
            </button>
            <button type="button" data-testid="preset-google" @click="applyPreset('google')">
              Google AI Studio（雲端 Gemma）
            </button>
          </div>
          <p v-if="isCloudBackend" class="egress-warn" data-testid="egress-warn">
            ⚠ 雲端後端：AI 決策會把戰場 COP（單位位置、陣營、任務、敵情）送到該服務。
            機敏/機密兵推請改用本機模型；非機密演練再用雲端。
          </p>
          <label class="field">
            <span class="lbl">Base URL</span>
            <input
              v-model="llmBaseUrl"
              data-testid="llm-base-url"
              placeholder="http://host.docker.internal:11434"
              autocomplete="off"
            >
          </label>
          <p class="hint">
            Ollama 在本機時，core 於容器內需用 <code>host.docker.internal</code> 連回主機；系統會呼叫
            <code>{Base URL}/v1/chat/completions</code>。
          </p>
          <label class="field">
            <span class="lbl">Model</span>
            <input
              v-model="llmModel"
              data-testid="llm-model"
              placeholder="gemma4:12b-mlx"
              autocomplete="off"
            >
          </label>
          <label class="field">
            <span class="lbl">API Key</span>
            <input
              v-model="llmApiKey"
              type="password"
              data-testid="llm-api-key"
              :placeholder="apiKeyAlreadySet ? '（已設定，留空＝不變）' : (isCloudBackend ? '（雲端後端必填，如 Google AI Studio 金鑰）' : '（Ollama 免填）')"
              autocomplete="new-password"
            >
          </label>

          <div class="actions">
            <button data-testid="test-llm" :disabled="testing || !llmBaseUrl || !llmModel" @click="testConnection">
              {{ testing ? '測試中…' : '測試連線' }}
            </button>
            <button class="primary" data-testid="save-settings" :disabled="saving" @click="save">
              {{ saving ? '儲存中…' : '儲存' }}
            </button>
            <span v-if="saveMsg" class="ok" data-testid="save-msg">{{ saveMsg }}</span>
          </div>
          <p
            v-if="testMsg"
            class="test-result"
            :class="testOk ? 'ok' : 'bad'"
            data-testid="test-result"
          >{{ testMsg }}</p>

          <p v-if="!cfg.readonly.ai_loop_wired" class="note" data-testid="ai-loop-note">
            ⓘ AI 決策迴路尚未接入活執行期 Kernel——此設定目前供「連線測試」與未來 AI 自動推演使用；
            現階段活模擬不會據此自動下令。
          </p>
        </section>

        <!-- #93 推演參數（可編輯；改的是兵推物理，故獨立一區並標明生效時機） -->
        <section v-if="sim" class="card" data-testid="sim-params">
          <h2>推演參數</h2>
          <p class="hint">
            這些會<strong>改變推演的物理規則</strong>（速度、耗損、補給距離、偵測）。
            <strong>進行中的推演局不受影響</strong>——執行端於該局啟動時讀取一次，
            要套用新值需該局重跑（封存/複製為新局）。移動<strong>預覽</strong>則立即反映。
          </p>
          <div class="grid2">
            <label>徒步越野速度（km/h）
              <input v-model.number="sim.foot_xc_kmh" data-testid="sim-foot-xc" type="number" min="0.1" step="0.5">
            </label>
            <label>徒步沿道路速度（km/h）
              <input v-model.number="sim.foot_road_kmh" data-testid="sim-foot-road" type="number" min="0.1" step="0.5">
            </label>
            <label>後備車輛速度（km/h）
              <input v-model.number="sim.vehicle_fallback_kmh" type="number" min="0.1" step="1">
              <small>無法由編裝導出機動時採用</small>
            </label>
            <label>補給撥交距離（km）
              <input v-model.number="sim.resupply_range_km" data-testid="sim-resupply" type="number" min="0.1" step="0.5">
            </label>
            <label>內建目視距離（m）
              <input v-model.number="sim.intrinsic_optical_range_m" type="number" min="1" step="100">
              <small>單位未配感測裝備時的基本偵察能力</small>
            </label>
            <label>偵測掃描間隔（tick）
              <input v-model.number="sim.sensor_interval_ticks" type="number" min="1" step="1">
              <small>1 tick ＝ 1 分模擬時間；愈密愈吃 DB</small>
            </label>
          </div>
          <h3 class="sim-h3">行軍耗損（戰力點 / 公里）</h3>
          <div class="grid2">
            <label v-for="(_v, profile) in sim.march_attrition" :key="profile">
              {{ profile }}
              <input v-model.number="sim.march_attrition[profile]" type="number" min="0" step="0.01">
            </label>
          </div>
        </section>

        <!-- 系統資訊（唯讀） -->
        <section class="card" data-testid="system-info">
          <h2>系統資訊（唯讀）</h2>
          <p class="hint">下列由容器啟動 ENV / 掛載決定，於此僅檢視；變更需改部署設定並重啟對應服務。</p>
          <dl class="ro">
            <div><dt>部署環境</dt><dd>{{ cfg.readonly.env }}</dd></div>
            <div>
              <dt>AI 模式環境預設（fallback）</dt>
              <dd>{{ cfg.readonly.ai_mode_env_default }}<span class="ro-note">← 僅未於上方設定時採用</span></dd>
            </div>
            <div><dt>Terrain gRPC</dt><dd>{{ cfg.readonly.terrain_grpc_target }}</dd></div>
            <div><dt>Weather gRPC</dt><dd>{{ cfg.readonly.weather_grpc_target }}</dd></div>
            <div><dt>Redis</dt><dd>{{ cfg.readonly.redis_url }}</dd></div>
            <div><dt>物理 Stub Gateway</dt><dd>{{ cfg.readonly.stub_gateway ? '啟用（E2E）' : '停用' }}</dd></div>
          </dl>
          <p class="hint">
            地形 DTED 資料路徑（<code>MATSO_DTED_PATH</code>）與地圖瓦片由 terrain / tileserver 容器的
            掛載設定，不在 core 可視範圍；如需更改請調 <code>ops/compose</code> 掛載並重啟該服務。
          </p>
        </section>
      </template>
    </template>
  </main>
</template>

<style scoped>
.settings {
  max-width: 46rem;
  margin: 0 auto;
  padding: 2rem 1rem;
  color: #e2e8f0;
}
header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 1.25rem;
}
h1 {
  margin: 0;
  font-size: 1.5rem;
}
.back {
  color: #60a5fa;
  text-decoration: none;
  font-size: 0.85rem;
}
.forbidden {
  color: #f87171;
}
.card {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 0.5rem;
  padding: 1rem 1.1rem;
  margin-bottom: 1.1rem;
}
.card h2 {
  margin: 0 0 0.75rem;
  font-size: 1.05rem;
}
.subhd {
  margin: 0.9rem 0 0.4rem;
  color: #94a3b8;
  font-size: 0.82rem;
  border-top: 1px solid #1e293b;
  padding-top: 0.7rem;
}
.presets {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin: 0.2rem 0 0.5rem;
}
.presets .plabel {
  color: #94a3b8;
  font-size: 0.78rem;
}
.presets button {
  background: #0b1220;
  border: 1px solid #334155;
  color: #cbd5e1;
  border-radius: 0.3rem;
  padding: 0.25rem 0.6rem;
  cursor: pointer;
  font-size: 0.78rem;
}
.presets button:hover {
  border-color: #60a5fa;
}
.egress-warn {
  margin: 0.2rem 0 0.6rem;
  color: #fbbf24;
  font-size: 0.76rem;
  line-height: 1.5;
  background: rgba(251, 191, 36, 0.08);
  border: 1px solid rgba(251, 191, 36, 0.25);
  border-radius: 0.35rem;
  padding: 0.45rem 0.6rem;
}
.field {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin: 0.4rem 0;
}
.field .lbl {
  flex: 0 0 6.5rem;
  color: #94a3b8;
  font-size: 0.85rem;
}
.field input,
.field select {
  flex: 1 1 auto;
  background: #0b1220;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.4rem 0.55rem;
  font-size: 0.85rem;
}
.hint {
  color: #94a3b8;
  font-size: 0.75rem;
  line-height: 1.5;
  margin: 0.2rem 0 0.4rem;
}
.hint code,
.note code {
  background: #1e293b;
  padding: 0.05rem 0.3rem;
  border-radius: 0.2rem;
  font-size: 0.72rem;
}
.actions {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-top: 0.9rem;
}
.actions button {
  background: transparent;
  border: 1px solid #334155;
  color: #cbd5e1;
  border-radius: 0.3rem;
  padding: 0.4rem 0.85rem;
  cursor: pointer;
  font-size: 0.85rem;
}
.actions button.primary {
  background: #1d4ed8;
  border-color: #1d4ed8;
  color: #fff;
}
.actions button:disabled {
  opacity: 0.5;
  cursor: default;
}
.ok {
  color: #4ade80;
  font-size: 0.82rem;
}
.bad {
  color: #f87171;
  font-size: 0.82rem;
}
.err {
  color: #f87171;
}
.test-result {
  margin: 0.6rem 0 0;
  font-size: 0.82rem;
  word-break: break-all;
}
.note {
  margin: 0.9rem 0 0;
  color: #fbbf24;
  font-size: 0.78rem;
  line-height: 1.55;
  background: rgba(251, 191, 36, 0.08);
  border: 1px solid rgba(251, 191, 36, 0.25);
  border-radius: 0.35rem;
  padding: 0.5rem 0.6rem;
}
.ro {
  margin: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.35rem 1rem;
}
.ro > div {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.ro dt {
  color: #64748b;
  font-size: 0.72rem;
}
.ro dd {
  margin: 0;
  color: #e2e8f0;
  font-size: 0.85rem;
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}
.ro-note {
  color: #64748b;
  font-size: 0.7rem;
  margin-left: 0.4rem;
}
/* #93 推演參數：兩欄格線，與既有 card 表單風格一致。 */
.grid2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
  gap: 0.75rem 1rem;
}
.grid2 label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
  color: #94a3b8;
}
.grid2 input {
  padding: 0.35rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
}
.grid2 small {
  color: #64748b;
  font-size: 0.72rem;
}
.sim-h3 {
  margin: 1rem 0 0.5rem;
  font-size: 0.9rem;
  color: #cbd5e1;
}
</style>
