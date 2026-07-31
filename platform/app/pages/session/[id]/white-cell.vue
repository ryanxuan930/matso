<script setup lang="ts">
// 白軍控制台（O7.4，SPEC §12）——視角切換 / 時間控制 / 事件注入 / 事件流。限統裁角色。
import { UNKNOWN_REASON, streamStatusLabel } from '~/composables/useLabels'
import { useSessionStreamStore } from '~/stores/sessionStream'
import type { UnitView } from '~/composables/useOrders'
import { apiFetch } from '~/composables/useApi'
import {
  checkpointLabel,
  eventAudience,
  eventTick,
  fetchCheckpoints,
  injectEvent,
  sessionControl,
  simTimeIso,
  unitsAsFaction,
  type CheckpointPoint,
  type ControlAction,
  type StreamEnvelope,
} from '~/composables/useWhiteCell'
import {
  hasBlockingIssue,
  injectActionIssues,
  type InjectAction,
} from '~/composables/useConditionDsl'

const route = useRoute()
const sessionId = route.params.id as string
const stream = useSessionStreamStore()
// 事件敘述器與 COP 戰況面板同源（見樣板註解）。
const { formatEvent } = useCopFeed(() => units.value)

const viewpoint = ref<string>('') // '' = 全知 god view
const units = ref<UnitView[]>([])
const factions = computed(() => [...new Set(units.value.map((u) => u.faction))].sort())

/**
 * 狀態列。**成功與失敗要分得出來**：過去兩者共用一個綠色的 `.status`，
 * 於是「注入失敗：…」是以綠字顯示的——在統裁台上，那個顏色會被讀成「成功了」。
 */
const status = ref('')
const statusBad = ref(false)
function say(msg: string, bad = false) {
  status.value = msg
  statusBad.value = bad
}

/**
 * 牆鐘（快照點的「多久以前」要用）。初值 0 避免 SSR 水合不一致。
 *
 * **10 秒一跳而不是 1 秒**：每一跳都會重算整份快照點下拉的文字（實測一局有上百個點），
 * 而下拉正被拉開時重寫選項文字有機會把它收起來。分鐘級的「多久以前」不需要秒級更新。
 */
const nowMs = ref(0)
const CLOCK_TICK_MS = 10_000
let clockTimer: ReturnType<typeof setInterval> | null = null
let cpTimer: ReturnType<typeof setInterval> | null = null

const sessionStart = ref<string | null>(null)

async function loadUnits() {
  try {
    units.value = await unitsAsFaction(sessionId, viewpoint.value || null)
    // 切視角後被編輯的單位可能已不在清單裡（換成他軍視角）。留著的話會出現
    // 「屬性面板消失、編裝面板還開著」——兩個面板指向不同的事實。
    if (editUnitId.value && !units.value.some((u) => u.id === editUnitId.value)) {
      editUnitId.value = ''
    }
  } catch (e) {
    say(`讀取失敗：${(e as { message?: string }).message ?? e}`, true)
  }
}

/**
 * 開局時間（供時間列算執行時間）——與 COP 同一個來源。
 *
 * **補上時區再交給時間列**：後端回的是不帶時區的 UTC，而 `SimClockBar` 直接 `Date.parse`。
 * 在 UTC+8 的機器上那會把開局時間算成 8 小時後的未來，執行時間變負數 → 顯示「—」。
 */
async function loadSessionMeta() {
  const list = await apiFetch<Array<{ id: string; start_time?: string | null }>>('/sessions').catch(
    () => [],
  )
  sessionStart.value = simTimeIso(list.find((s) => s.id === sessionId)?.start_time)
}

// ---------------------------------------------------------------------------
// 時間控制與快照點
// ---------------------------------------------------------------------------

/**
 * 回滾目標**必須剛好是既有的快照 tick**（後端 `_request_rollback` 的硬性條件），
 * 所以這裡列出實際可選的點。過去這裡用 `prompt()`：使用者只能瞎猜一個數字，
 * 十之八九拿到 ROLLBACK_TARGET_NOT_FOUND；而按「取消」時 `null ?? 0` 會變成 0，
 * 直接送出「回滾到 tick 0」＝整局重來。
 *
 * 清單**要持續更新**：快照每隔一段 tick 產生一個，只在開頁時抓一次的話，開著控制台跑了
 * 一小時的統裁能選的仍是一小時前那幾個點——而他最想回到的，正是剛剛那一場交戰之前。
 */
const checkpoints = ref<CheckpointPoint[]>([])
const rollbackTick = ref<number | null>(null)
const confirmRollback = ref(false)

async function loadCheckpoints() {
  try {
    checkpoints.value = await fetchCheckpoints(sessionId)
    // 後端依帳本序由新到舊回傳 → [0] 是最新的存錄點。預設選最新＝作廢範圍最小的那一個。
    // 選過的點若還在清單裡就不要動它（每 30 秒重抓一次，不能把使用者選的蓋掉）。
    const known = checkpoints.value.some((c) => c.tick === rollbackTick.value)
    if (!known) rollbackTick.value = checkpoints.value.length ? checkpoints.value[0]!.tick : null
  } catch {
    checkpoints.value = []
  }
}

/**
 * 該 tick 上「發生了什麼」——**只用本頁真的收到過的事件**推導，查不到就不寫。
 *
 * 快照點端點只回 tick／帳本序／校驗碼／存錄時間，沒有任何「當時的戰況」。與其編一句，
 * 不如用手上真有的東西：事件流裡同一個 tick 的最後一則敘述。涵蓋範圍受限於補送與
 * 本頁連線後累積的量，所以**查不到是常態**，不是解析失敗。
 */
const eventTextByTick = computed(() => {
  const map = new Map<number, string>()
  for (const raw of stream.events as unknown as StreamEnvelope[]) {
    const t = eventTick(raw)
    if (t === null) continue
    map.set(t, formatEvent((raw.payload ?? {}) as Record<string, unknown>))
  }
  return map
})
function labelOf(cp: CheckpointPoint): string {
  return checkpointLabel(cp, nowMs.value, eventTextByTick.value.get(cp.tick))
}
const selectedCheckpoint = computed(
  () => checkpoints.value.find((c) => c.tick === rollbackTick.value) ?? null,
)

async function control(action: ControlAction) {
  const target = action === 'ROLLBACK' ? rollbackTick.value! : undefined
  try {
    const res = await sessionControl(sessionId, action, target)
    say(
      action === 'ROLLBACK'
        ? `已排入回滾至 tick ${res.rollback_requested_tick ?? target}；該局將停在暫停狀態，請確認後按「續行」。`
        : `已送出 ${action}`,
    )
    if (action === 'ROLLBACK') await loadCheckpoints()
  } catch (e) {
    say(`控制失敗：${(e as { message?: string }).message ?? e}`, true)
  }
}

/**
 * 回溯要**兩段確認**。它把選定 tick 之後的整段推演作廢且無法復原，而現在的它只是一顆
 * 和「暫停」長得一模一樣的按鈕——手滑一次就毀掉半場演習。
 * （不用 `window.confirm`：那與被換掉的 `window.prompt` 是同一類東西。）
 */
function askRollback() {
  if (rollbackTick.value === null) {
    say('請先選一個快照點——回滾目標必須是既有的快照 tick。', true)
    return
  }
  confirmRollback.value = true
}
async function doRollback() {
  confirmRollback.value = false
  await control('ROLLBACK')
}

// ---------------------------------------------------------------------------
// 即時注入
// ---------------------------------------------------------------------------

/**
 * 即時注入（trigger-free）：event_type + payload + 目標陣營（空＝廣播全體）。
 *
 * ⚠ **這條路只會發一則事件**：`core/app/api/inject.py` 直接 `publish_event` 進 Redis
 * ring／WS，不經過 `msel_actions.make_applier`，也不寫 Ledger。所以表單用 `live` 型態
 * ——不給「會改變世界」的那些動作選項（理由見 `InjectActionForm` 檔頭）。
 */
const injectAction = ref<InjectAction>({ event_type: 'BRIDGE_DESTROYED', payload: {}, faction: undefined })
const injectIssues = computed(() => injectActionIssues(injectAction.value))
const injectBlocked = computed(() => hasBlockingIssue(injectIssues.value))
/** 送出前先看它在戰況流長什麼樣——用的是與 COP 同一個敘述器、同一份單位清單。 */
const injectPreview = computed(() =>
  formatEvent({
    event_type: injectAction.value.event_type,
    ...(injectAction.value.payload ?? {}),
  } as Record<string, unknown>),
)
const injectAudience = computed(() => injectAction.value.faction || '全體（廣播）')

async function doInject() {
  if (injectBlocked.value) {
    say('注入未送出：請先修掉表單上的紅字。', true)
    return
  }
  try {
    await injectEvent(
      sessionId,
      injectAction.value.event_type,
      injectAction.value.payload ?? {},
      injectAction.value.faction ?? null,
    )
    say(`已注入 ${injectAction.value.event_type}（受眾：${injectAudience.value}）`)
  } catch (e) {
    say(`注入失敗：${(e as { message?: string }).message ?? e}`, true)
  }
}

// ---------------------------------------------------------------------------
// 單位編輯
// ---------------------------------------------------------------------------

/**
 * 單位編輯（#6）＋各軍自編權限。
 *
 * ⚠ **這裡曾經是一組手寫欄位**：番號 + 「作戰效能%」+ 一個要打 JSON 的 attributes 輸入框。
 * 兩個問題：
 *   1. 作戰效能是**導出量**——由戰力比算出、裁決層每次命中都會覆寫。改它只改了顯示，
 *      下一次交戰就打回去；統裁卻以為自己幫某個單位補了血。後端現在對它回 422。
 *   2. 裸 JSON 輸入框把資料格式的正確性丟給使用者，打錯一個引號就整包存不進去。
 *
 * 改用與 COP 單位卡同一個 `UnitAttributeEditor`（結構化欄位 + 會寫進活模擬熱狀態）。
 * 兩處共用一個元件，日後加欄位不會只有一邊拿到。
 */
const editUnitId = ref('')
const orbatFactions = ref<string[]>([])
const editUnit = computed(() => units.value.find((u) => u.id === editUnitId.value) ?? null)

function pickUnit(u: UnitView) {
  editUnitId.value = u.id
}
async function loadPerms() {
  const r = await apiFetch<{ factions: string[] }>(
    `/sessions/${sessionId}/orbat-permissions`,
  ).catch(() => ({ factions: [] as string[] }))
  orbatFactions.value = r.factions
}
async function togglePerm(f: string) {
  const set = new Set(orbatFactions.value)
  if (set.has(f)) set.delete(f)
  else set.add(f)
  orbatFactions.value = [...set]
  try {
    await apiFetch(`/sessions/${sessionId}/orbat-permissions`, {
      method: 'PUT',
      body: { factions: orbatFactions.value },
    })
    say(`自編權限：${orbatFactions.value.join('、') || '（僅白軍）'}`)
  } catch (e) {
    say(`設定失敗：${(e as { code?: string }).code ?? e}`, true)
  }
}

// ---------------------------------------------------------------------------
// MSEL 待命注入
// ---------------------------------------------------------------------------

/**
 * WP-B2c 待命注入：MSEL 腳本裡還沒發、也沒被跳過的狀況。
 *
 * **扣板機是排隊不是立即生效**：`MselRuntime` 活在 sim runner 行程，
 * API 只能把命令排進佇列，由 runner 於下一 tick 套用（故端點回 202）。
 * 清單本身也是 runner 每 tick 發布的，所以按完會慢一拍才更新——這裡照實顯示，
 * 不做樂觀更新假裝已經發了。
 */
const mselPending = ref<string[]>([])
const mselBusy = ref('')

async function loadMsel() {
  try {
    const r = await apiFetch<{ pending: string[] }>(`/sessions/${sessionId}/msel`)
    mselPending.value = r.pending
  } catch {
    mselPending.value = []
  }
}

async function mselAct(entryId: string, action: 'fire' | 'skip') {
  mselBusy.value = entryId
  try {
    await apiFetch(`/sessions/${sessionId}/msel/${entryId}/${action}`, { method: 'POST' })
    say(action === 'fire' ? `已排入扣發：${entryId}` : `已排入跳過：${entryId}`)
    setTimeout(loadMsel, 1500) // runner 下一 tick 才會更新清單
  } catch (e) {
    say(`MSEL 操作失敗：${(e as { message?: string }).message ?? UNKNOWN_REASON}`, true)
  } finally {
    mselBusy.value = ''
  }
}

// ---------------------------------------------------------------------------
// 事件流（統裁版：帳本序 / tick / 受眾）
// ---------------------------------------------------------------------------

const FEED_LIMIT = 200
const feedFilter = ref('')

/**
 * 事件流列。與 COP 共用敘述器，但**多三欄**：帳本序（定位）、tick（排時序）、
 * 受眾（哪個陣營看得到）。統裁要追的是事件鏈與各方的資訊落差，一句中文敘述不夠——
 * 「藍軍知不知道這件事」正是講評時最容易講錯的東西。
 *
 * 新到舊排序（與指令列同一個決定）：演習中要看的是「剛剛發生什麼」。
 * 舊寫法把最新一則放最下面，而且只留 20 則。
 */
const feedRows = computed(() => {
  const rows = (stream.events as unknown as StreamEnvelope[]).map((e, i) => ({
    key: `${e.seq ?? 'x'}-${i}`,
    seq: e.seq ?? null,
    tick: eventTick(e),
    audience: eventAudience(e),
    text: formatEvent((e.payload ?? {}) as Record<string, unknown>),
  }))
  const q = feedFilter.value.trim().toLowerCase()
  const hit = q
    ? rows.filter(
        (r) =>
          r.text.toLowerCase().includes(q) ||
          r.audience.toLowerCase().includes(q) ||
          String(r.seq ?? '').includes(q),
      )
    : rows
  return hit.slice(-FEED_LIMIT).reverse()
})

onMounted(() => {
  nowMs.value = Date.now()
  clockTimer = setInterval(() => {
    nowMs.value = Date.now()
  }, CLOCK_TICK_MS)
  loadUnits()
  loadPerms()
  loadMsel()
  loadCheckpoints()
  loadSessionMeta()
  cpTimer = setInterval(loadCheckpoints, 30_000)
  /**
   * **要求補送**：連線握手不帶 last_seq 時，後端把你當全新 client，一則歷史都不補
   * （`stream/backfill.plan_resume`：`last_seq is None` → 從當下起接 live）。
   * 於是「演習到一半打開控制台」看到的是一片空白的事件流——而事件流正是統裁追事件鏈的工具。
   * 帶 0 等於說「我什麼都還沒收到」，後端會把環形緩衝裡還留著的全部補上；
   * 已被截斷則回 RESYNC_REQUIRED，store 自己會退回抓快照，不會壞掉。
   *
   * 只在**緩衝是空的**時候這麼做：從圖台走過來時 store 已有內容，再補一次會拿到重複的一份。
   */
  if (!stream.events.length) stream.lastSeq = 0
  stream.connect(sessionId)
})
onUnmounted(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (cpTimer) clearInterval(cpTimer)
  stream.disconnect()
})
watch(viewpoint, loadUnits)
</script>

<template>
  <div class="wc" data-testid="white-cell-console">
    <header class="wc-bar">
      <button data-testid="wc-back-cop" @click="navigateTo(`/session/${sessionId}/cop`)">← 返回圖台</button>
      <h1>白軍控制台 · {{ sessionId }}</h1>
      <!-- 沒有任何端點讀得回暫停旗標（見回報）。推演時間停不停，是統裁唯一看得出
           「暫停到底生效了沒」的證據，所以這一列必須在。 -->
      <ClientOnly>
        <SimClockBar :tick="stream.lastTick" :start-time="sessionStart" />
      </ClientOnly>
    </header>
    <p v-if="status" class="status" :class="{ bad: statusBad }" data-testid="wc-status">{{ status }}</p>

    <section class="controls">
      <div>
        <h2>視角</h2>
        <select v-model="viewpoint" data-testid="viewpoint">
          <option value="">全局視角（全知）</option>
          <option v-for="f in factions" :key="f" :value="f">{{ f }} 視角</option>
        </select>
        <span data-testid="unit-count">{{ units.length }} 單位</span>
      </div>
      <div>
        <h2>時間控制</h2>
        <button data-testid="pause" @click="control('PAUSE')">⏸ 暫停</button>
        <button data-testid="resume" @click="control('RESUME')">▶ 續行</button>
        <label class="rollback-pick">
          回溯至
          <select v-model.number="rollbackTick" data-testid="rollback-tick">
            <option v-if="!checkpoints.length" :value="null">（尚無快照點）</option>
            <option
              v-for="c in checkpoints"
              :key="c.tick"
              :value="c.tick"
              :title="`完整校驗碼 ${c.state_hash}`"
            >
              {{ labelOf(c) }}
            </option>
          </select>
        </label>
        <button data-testid="wc-checkpoints-refresh" title="重新抓取快照點" @click="loadCheckpoints">⟳</button>
        <button
          data-testid="rollback"
          :disabled="!checkpoints.length"
          @click="askRollback"
        >回溯至存錄點</button>
        <p class="wc-hint">
          校驗碼是給人<b>核對</b>兩份紀錄是不是同一個狀態用的，不是選擇依據；要選哪一點請看時間。
        </p>
        <div v-if="confirmRollback" class="wc-confirm" data-testid="wc-rollback-confirm">
          <p>
            確定回溯至 <b>{{ selectedCheckpoint ? labelOf(selectedCheckpoint) : `tick ${rollbackTick}` }}</b>？<br>
            <b>該點之後的推演全部作廢且無法復原</b>；該局會停在暫停狀態，需自行按「續行」。
          </p>
          <button data-testid="wc-rollback-yes" @click="doRollback">確定回溯</button>
          <button data-testid="wc-rollback-no" @click="confirmRollback = false">取消</button>
        </div>
      </div>
      <div class="inject-box">
        <h2>注入事件</h2>
        <InjectActionForm
          v-model="injectAction"
          variant="live"
          :factions="factions"
          :units="units"
          event-testid="inject-type"
        />
        <!-- 送出前先看戰況流會長什麼樣。過去只能送出去、切到圖台、再切回來看——
             而看到的常常是一行原始英文代號（敘述器沒有那個型別的中文）。 -->
        <p class="wc-preview" data-testid="wc-inject-preview">
          <span class="wc-preview-tag">戰況流預覽</span>
          <span class="wc-preview-text">{{ injectPreview }}</span>
          <span class="wc-preview-aud">受眾：{{ injectAudience }}</span>
        </p>
        <button data-testid="do-inject" :disabled="injectBlocked" @click="doInject">注入</button>
      </div>
    </section>

    <section>
      <h2>編裝編輯 · 各軍自編權限</h2>
      <div class="perms">
        <label v-for="f in factions" :key="f">
          <input
            type="checkbox"
            :checked="orbatFactions.includes(f)"
            :data-testid="`perm-${f}`"
            @change="togglePerm(f)"
          >
          {{ f }} 可自編本軍
        </label>
        <span v-if="!factions.length" class="hint">（無單位）</span>
      </div>
    </section>

    <section>
      <h2>單位（{{ viewpoint || '全局視角' }}）— 點選編輯</h2>
      <ul data-testid="wc-unit-list" class="units">
        <li
          v-for="u in units"
          :key="u.id"
          :class="{ sel: u.id === editUnitId }"
          data-testid="wc-unit-item"
          @click="pickUnit(u)"
        >
          {{ u.designation }} · {{ u.faction }} · 效能 {{ Math.round(u.health) }}%
        </li>
      </ul>
      <div v-if="editUnit" class="edit" data-testid="unit-edit">
        <UnitAttributeEditor
          :session-id="sessionId"
          :unit-id="editUnit.id"
          :designation="editUnit.designation"
          :branch="editUnit.branch ?? 'UNKNOWN'"
          :unit-level="editUnit.unit_level"
          :personnel="editUnit.personnel_current ?? null"
          :strength="editUnit.strength ?? 0"
          :authorized-strength="editUnit.authorized_strength ?? 100"
          @saved="loadUnits"
        />
      </div>
      <!-- 與屬性編輯器**同一個條件**：切視角後單位不在清單裡時兩個面板要一起收起來，
           否則會出現「屬性面板不見了、編裝面板還開著」這種指向不同事實的畫面。 -->
      <div v-if="editUnit" class="orbat-box" data-testid="wc-orbat">
        <h3>編裝（武器/彈藥）</h3>
        <UnitOrbatEditor :session-id="sessionId" :unit-id="editUnitId" :can-edit="true" />
      </div>
    </section>

    <!-- WP-B2c 白軍動態取捨：教官看現場狀況決定要不要發下一個狀況。 -->
    <section>
      <h2>待命注入（MSEL）</h2>
      <p class="wc-hint">
        <code>manual</code> 型狀況須於此扣發才會發生。跳過將<b>記入事件帳本</b>——
        行動後檢討要看得出「原定」與「實際」的差異。
        清單只給得出腳本條目編號：後端不供應內容（會注入什麼、觸發條件為何），須查該局想定。
      </p>
      <ul data-testid="wc-msel-pending">
        <li v-for="id in mselPending" :key="id" class="wc-msel">
          <code>{{ id }}</code>
          <button :disabled="mselBusy === id" data-testid="wc-msel-fire" @click="mselAct(id, 'fire')">
            扣發
          </button>
          <button :disabled="mselBusy === id" data-testid="wc-msel-skip" @click="mselAct(id, 'skip')">
            跳過
          </button>
        </li>
        <li v-if="!mselPending.length" class="dim">（無待命注入；該局可能沒有 MSEL 或尚未開跑）</li>
      </ul>
    </section>

    <section>
      <h2 data-testid="wc-stream-status">戰況事件流（{{ streamStatusLabel(stream.status) }}）</h2>
      <!-- 與 COP 戰況面板共用同一個敘述器：統裁與參謀看到的是同一句話，
           對起帳來才不會各說各話。過去這裡直接印 JSON.stringify(payload)。
           但統裁要的比參謀多——帳本序定位、tick 排時序、受眾判斷誰知道這件事。 -->
      <div class="wc-feed-bar">
        <input
          v-model="feedFilter"
          data-testid="wc-event-filter"
          placeholder="篩選：番號 / 陣營 / 序號 / 關鍵字"
        >
        <span class="hint" data-testid="wc-event-count">
          顯示 {{ feedRows.length }} / 已收 {{ stream.events.length }} 則（新→舊）
        </span>
      </div>
      <ul data-testid="wc-event-list">
        <li v-for="row in feedRows" :key="row.key" class="wc-event">
          <span class="wc-seq">#{{ row.seq ?? '—' }}</span>
          <span class="wc-tick">{{ row.tick === null ? 'tick —' : `tick ${row.tick}` }}</span>
          <span class="wc-aud" data-testid="wc-event-audience">{{ row.audience }}</span>
          <span class="wc-text">{{ row.text }}</span>
        </li>
        <li v-if="!feedRows.length" class="dim">
          （無事件。事件流自本頁連線起累積，更早的紀錄請看行動後檢討。）
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.wc { max-width: 1000px; margin: 0 auto; padding: 1rem; color: #e2e8f0; }
.wc-bar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.wc-bar h1 { font-size: 1.25rem; margin: 0; }
.wc-bar .help { margin-left: auto; font-size: 0.8125rem; color: #60a5fa; text-decoration: none; }
.wc-bar .help:hover { text-decoration: underline; }
h2 { font-size: 0.9375rem; color: #94a3b8; margin: 0 0 0.5rem; }
.controls { display: flex; gap: 2rem; flex-wrap: wrap; }
.status { color: #4ade80; }
.status.bad { color: #f87171; }
section { border-top: 1px solid #1e293b; padding-top: 0.75rem; margin-top: 1rem; }
ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.8125rem; }
input, select {
  padding: 0.375rem 0.5rem; border: 1px solid #334155; border-radius: 0.25rem;
  background: #0f172a; color: #e2e8f0;
}
button {
  margin-right: 0.4rem; padding: 0.375rem 0.75rem; border: 1px solid #334155;
  border-radius: 0.25rem; background: #1e293b; color: #e2e8f0; cursor: pointer;
}
button:hover { border-color: #2563eb; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
.units li { cursor: pointer; padding: 0.25rem 0.5rem; border-radius: 0.25rem; }
.units li:hover { background: #1e293b; }
.units li.sel { background: #172554; outline: 1px solid #2563eb; }
.perms { display: flex; gap: 1rem; flex-wrap: wrap; }
.perms label { display: flex; gap: 0.375rem; align-items: center; }
.edit { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; margin-top: 0.5rem; }
.edit label { display: flex; gap: 0.375rem; align-items: center; font-size: 0.8125rem; }
.hint { color: #64748b; }
.dim { color: #64748b; }
.wc-hint { font-size: 0.75rem; color: #64748b; margin: 0.35rem 0 0; max-width: 34rem; }
.wc-seq { color: #64748b; margin-right: 0.35rem; }
.wc-tick {
  color: #94a3b8; margin-right: 0.35rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.wc-aud {
  color: #38bdf8; margin-right: 0.4rem; font-size: 0.72rem;
  border: 1px solid #0e7490; border-radius: 0.2rem; padding: 0 0.25rem;
}
.wc-event { display: flex; align-items: baseline; gap: 0.1rem; }
.wc-text { flex: 1; }
.wc-feed-bar { display: flex; gap: 0.6rem; align-items: center; margin-bottom: 0.4rem; }
.wc-feed-bar input { min-width: 16rem; font-size: 0.8125rem; }
.wc-confirm {
  margin-top: 0.5rem; padding: 0.5rem 0.6rem; border: 1px solid #b91c1c;
  border-radius: 0.25rem; background: #1f0d0d; font-size: 0.8125rem;
}
.wc-confirm p { margin: 0 0 0.5rem; }
.wc-preview {
  margin: 0.4rem 0; font-size: 0.78rem; display: flex; flex-wrap: wrap;
  gap: 0.4rem; align-items: baseline;
}
.wc-preview-tag { color: #64748b; }
.wc-preview-text { color: #e2e8f0; }
.wc-preview-aud { color: #38bdf8; }
.rollback-pick {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: 8px;
  font-size: 12px;
}
</style>
