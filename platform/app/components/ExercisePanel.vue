<script setup lang="ts">
/**
 * 演習專案面板（WP-B1c）——lobby 的「演習」分頁。
 *
 * **限白軍/統裁/管理**——演習層是導演工具。後端對非全知者回 404，前端只是把入口藏起來。
 *
 * 元件名必須全域唯一：`nuxt.config.ts` 設了 `pathPrefix: false`，
 * `components/`、`components/cop/`、`components/map/` 的 basename 共用同一個命名空間。
 */
import { computed, ref } from 'vue'
import type { components } from '~/types/api'
import { dataTableLabel, sessionStatusLabel } from '~/composables/useLabels'
import { fetchAllUsers } from '~/composables/useParticipants'
import {
  AUDIT_ACTION_LABELS,
  EXERCISE_PHASE_LABELS,
  SESSION_ROLE_LABELS,
  actorLabel,
  advancePhase,
  attachSession,
  createExercise,
  deleteExercise,
  destroyExerciseData,
  detachSession,
  downloadBundle,
  fetchAudit,
  fetchExercises,
  fetchSeal,
  fmtWallClock,
  nextPhase,
  phaseLabel,
  sealParams,
  tickChecklist,
  unsealParams,
  type DestroyResult,
  type ExerciseAuditEntry,
  type ExercisePhase,
  type ExerciseView,
  type SealView,
  type SessionRole,
} from '~/composables/useExercises'

const props = defineProps<{
  /** lobby 已經抓好的 session 清單——掛載下拉用，不再打一次 API。 */
  sessions: components['schemas']['SessionSummary'][]
}>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const exercises = ref<ExerciseView[]>([])
const loading = ref(true)
const newName = ref('')
const busy = ref(false)
const message = ref('')
const openId = ref<string | null>(null)
const audit = ref<ExerciseAuditEntry[]>([])
const seal = ref<SealView | null>(null)
const attachRole = ref<SessionRole>('REHEARSAL')
const attachSessionId = ref('')

/**
 * user id → 帳號名。稽核 `actor_id`、簽證 `sealed_by`、勾稽 `done_by` 全都只回 id，
 * 沒有這張表，「誰做的」就只能顯示一串 uuid——那等於沒回答稽核最主要的問題。
 * `/users` 的授權集（白軍/統裁/管理）與演習面板的可見集相同，故看得到面板就抓得到；
 * 真抓不到（403/離線）就退回 id 前 8 碼顯示，不讓整個面板陪葬。
 */
const actorNames = ref<Record<string, string>>({})

const auth = useAuthStore()
/**
 * 銷毀模式限 **ADMIN**（後端 `destroy_data` 的第一道閘門；白軍/統裁都不行）。
 * 前端據此隱藏入口——擺一顆按下去必定 403 的按鈕，比沒有按鈕更糟。
 */
const canDestroy = computed(() => auth.user?.role === 'ADMIN')
/** 展開中的銷毀確認框屬於哪一個演習（null＝沒展開）。 */
const destroyFor = ref<string | null>(null)
const destroyConfirm = ref('')
/** 銷毀結果連同演習 id 一起存——刪完清單會重抓，沒有 id 就不知道該貼在哪一張卡上。 */
const destroyResult = ref<{ exerciseId: string; result: DestroyResult } | null>(null)

/** 尚未掛在**任何**演習底下的局才可掛——一局只能屬於一個演習（後端 409）。 */
const attachable = computed(() => props.sessions.filter((s) => !s.exercise_id))

async function refresh() {
  loading.value = true
  try {
    exercises.value = await fetchExercises().catch(() => [])
  } finally {
    loading.value = false
  }
}

async function reopen(id: string) {
  // 換一張卡就關掉銷毀確認框：確認框綁的是「這一張卡的名稱」，
  // 帶著已輸入的名稱漂到別張卡上，就成了對錯的演習按下不可逆操作。
  if (openId.value !== id) closeDestroy()
  openId.value = id
  audit.value = await fetchAudit(id).catch(() => [])
  seal.value = await fetchSeal(id).catch(() => null)
}

function openDestroy(id: string) {
  destroyFor.value = id
  // 每次重開都清空確認輸入——留著上一次打到一半的名稱，等於幫使用者把閘門推開一半。
  destroyConfirm.value = ''
}

function closeDestroy() {
  destroyFor.value = null
  destroyConfirm.value = ''
}

/** 所有變更共用的收尾：重抓清單、重抓展開中的細節、通知 lobby 重抓 sessions。 */
async function run(fn: () => Promise<unknown>) {
  busy.value = true
  message.value = ''
  try {
    await fn()
    await refresh()
    if (openId.value) await reopen(openId.value)
    emit('changed')
  } catch (e) {
    const err = e as { code?: string; message?: string; details?: Record<string, unknown> }
    // 未完成的整備項要逐鍵說出來——只說「不能推進」的話，操作員得自己去猜是哪一項。
    const missing = (err.details?.missing as string[] | undefined) ?? []
    message.value = missing.length
      ? `尚有整備未完成：${missing.join('、')}`
      : (err.message ?? err.code ?? '操作失敗')
  } finally {
    busy.value = false
  }
}

function phaseHint(p: string): string {
  return EXERCISE_PHASE_LABELS[p]?.hint ?? ''
}

/** 稽核/簽證/勾稽欄位的「誰」。 */
function who(id?: string | null): string {
  return actorLabel(id, actorNames.value)
}

/**
 * 銷毀結果只列**真的刪到東西**的表。
 * `purge_session_rows` 會把每一個 session-scoped 表都回一筆（多半是 0），
 * 原樣攤開會是十幾個「XXX 0」把唯一有意義的那兩三個淹掉。
 */
function deletedRows(result: DestroyResult): [string, number][] {
  return Object.entries(result.rows_deleted ?? {}).filter(([, n]) => n > 0)
}

/**
 * 歸檔封包下載。**下載完要重抓稽核**——後端在這個端點寫 `BUNDLE_EXPORTED`，
 * 不重抓的話「誰把整場演習的完整資料帶走了」得收合再展開才看得到。
 */
function doDownloadBundle(id: string) {
  return run(() => downloadBundle(id))
}

/**
 * 執行銷毀。名稱不符時後端回 `EXERCISE_DESTROY_UNCONFIRMED`，由 `run` 統一顯示訊息；
 * 成功才關掉確認框，失敗留著讓操作員修正輸入。
 */
function doDestroy(ex: ExerciseView) {
  destroyResult.value = null
  return run(async () => {
    const result = await destroyExerciseData(ex.id, destroyConfirm.value)
    destroyResult.value = { exerciseId: ex.id, result }
    closeDestroy()
  })
}

await refresh()
// 帳號對映失敗不擋面板——沒有它只是顯示 id，有它才顯示得出「誰做的」。
actorNames.value = Object.fromEntries(
  (await fetchAllUsers().catch(() => [])).map((u) => [u.id, u.username]),
)
</script>

<template>
<div class="ex-panel" data-testid="exercise-panel">
  <section class="create">
    <input
      v-model="newName"
      data-testid="new-exercise-name"
      placeholder="新演習名稱"
      @keyup.enter="newName.trim() && run(() => createExercise(newName)).then(() => (newName = ''))"
    >
    <button
      data-testid="create-exercise"
      :disabled="busy || !newName.trim()"
      @click="run(() => createExercise(newName)).then(() => (newName = ''))"
    >建立演習</button>
  </section>

  <p v-if="message" class="ex-msg" data-testid="exercise-message">{{ message }}</p>
  <p v-if="loading" data-testid="exercise-loading">載入中…</p>
  <p v-else-if="!exercises.length" data-testid="exercise-empty">
    尚無演習專案。一場演習可以裝下多次預推、正式局與檢討。
  </p>

  <ul v-else class="ex-list" data-testid="exercise-list">
    <li v-for="ex in exercises" :key="ex.id" class="ex-card" data-testid="exercise-item">
      <div class="ex-hd" @click="openId === ex.id ? (openId = null) : reopen(ex.id)">
        <i class="pi" :class="openId === ex.id ? 'pi-chevron-down' : 'pi-chevron-right'" />
        <b class="ex-name">{{ ex.name }}</b>
        <span
          class="ex-phase"
          :class="`ph-${ex.phase.toLowerCase()}`"
          :title="phaseHint(ex.phase)"
          data-testid="exercise-phase"
        >{{ phaseLabel(ex.phase) }}</span>
        <span class="ex-count">{{ ex.sessions.length }} 局</span>
      </div>

      <div v-if="openId === ex.id" class="ex-body">
        <!-- 目前階段何時進入——`phase_changed_at` 後端一直有回，在此之前沒人讀。
             「這場演習卡在整備多久了」是統裁第一個會問的問題。 -->
        <div class="ex-meta" data-testid="exercise-meta">
          <span>建立：{{ fmtWallClock(ex.created_at) }}（{{ who(ex.created_by) }}）</span>
          <span data-testid="phase-changed-at">
            進入「{{ phaseLabel(ex.phase) }}」：{{ fmtWallClock(ex.phase_changed_at) }}
          </span>
        </div>

        <!-- 掛在底下的局 -->
        <div class="ex-sub">推演局</div>
        <ul class="ex-sessions" data-testid="exercise-sessions">
          <li v-for="s in ex.sessions" :key="s.id">
            <a :href="`/session/${s.id}/cop`">{{ s.name }}</a>
            <span class="tag">{{ SESSION_ROLE_LABELS[s.session_role ?? ''] ?? '未指定' }}</span>
            <span class="dim">{{ sessionStatusLabel(s.status) }}</span>
            <button
              class="edit-btn"
              data-testid="detach-session"
              title="卸下（該局變回獨立局）"
              :disabled="busy"
              @click="run(() => detachSession(ex.id, s.id))"
            ><i class="pi pi-times" /></button>
          </li>
          <li v-if="!ex.sessions.length" class="dim">（尚未掛入任何局）</li>
        </ul>
        <div class="ex-attach">
          <select v-model="attachSessionId" data-testid="attach-session-select">
            <option value="">掛入既有推演局…</option>
            <option v-for="s in attachable" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
          <select v-model="attachRole" data-testid="attach-session-role">
            <option v-for="(label, key) in SESSION_ROLE_LABELS" :key="key" :value="key">
              {{ label }}
            </option>
          </select>
          <button
            data-testid="attach-session"
            :disabled="busy || !attachSessionId"
            @click="run(() => attachSession(ex.id, attachSessionId, attachRole)).then(() => (attachSessionId = ''))"
          >掛入</button>
        </div>

        <!-- 整備勾稽：required 未勾就推不動階段 -->
        <div class="ex-sub">整備勾稽</div>
        <ul class="ex-checklist" data-testid="exercise-checklist">
          <li v-for="item in ex.checklist" :key="item.key">
            <label>
              <input
                type="checkbox"
                :checked="item.done"
                :disabled="busy"
                :data-testid="`checklist-${item.key}`"
                @change="run(() => tickChecklist(ex.id, item.key, !item.done))"
              >
              {{ item.label }}
              <span class="dim">· {{ phaseLabel(item.phase) }}</span>
              <span v-if="item.required" class="req">必要</span>
              <!-- 勾稽是要負責的動作：只顯示勾勾等於「有人勾過」，
                   問不出「誰、什麼時候勾的」——`done_at`/`done_by` 後端本來就有回。 -->
              <span v-if="item.done" class="dim" :data-testid="`checklist-done-${item.key}`">
                · {{ fmtWallClock(item.done_at) }} · {{ who(item.done_by) }}
              </span>
            </label>
          </li>
        </ul>

        <!-- WP-B4 參數簽證 -->
        <div class="ex-sub">參數簽證</div>
        <div class="ex-seal" data-testid="exercise-seal">
          <template v-if="seal">
            <div class="seal-row">
              <span :class="seal.matches ? 'ok' : 'bad'" data-testid="seal-status">
                {{ seal.matches ? '✓ 參數與簽證相符' : '⚠ 參數已被更動——此演習的局將拒絕啟動' }}
              </span>
              <button
                class="edit-btn"
                data-testid="unseal-params"
                title="解除簽證（會進稽核軌跡）"
                :disabled="busy"
                @click="run(() => unsealParams(ex.id))"
              >解除</button>
            </div>
            <!-- 簽證人/簽證時間/當前雜湊：不符時只給簽證當下的雜湊前 12 碼，
                 「誰簽的、何時簽的、現在變成什麼」全看不到，出事時無從判斷是誰改了什麼。 -->
            <div class="seal-detail" data-testid="seal-detail">
              <span>簽證：{{ fmtWallClock(seal.sealed_at) }} · {{ who(seal.sealed_by) }}</span>
              <span>
                簽證雜湊 <code>{{ seal.content_hash.slice(0, 12) }}…</code>
              </span>
              <span :class="seal.matches ? '' : 'bad'">
                當前雜湊 <code data-testid="seal-current-hash">{{ seal.current_hash.slice(0, 12) }}…</code>
              </span>
            </div>
          </template>
          <template v-else>
            <div class="seal-row">
              <span class="dim">未簽證。簽證後全域參數（武器庫／推演參數）唯讀。</span>
              <button data-testid="seal-params" :disabled="busy" @click="run(() => sealParams(ex.id))">
                簽證鎖定
              </button>
            </div>
          </template>
        </div>

        <!-- 階段推進 -->
        <div class="ex-actions">
          <button
            v-if="nextPhase(ex.phase)"
            data-testid="advance-phase"
            :disabled="busy"
            :title="phaseHint(nextPhase(ex.phase) as string)"
            @click="run(() => advancePhase(ex.id, nextPhase(ex.phase) as ExercisePhase))"
          >推進到「{{ phaseLabel(nextPhase(ex.phase) as string) }}」</button>
          <!-- 下載一律走 apiFetch＋Blob：`<a href>` 直連 API 端點會打到 Nuxt 自己、
               且瀏覽器導覽不帶 Bearer → 401（同 useAar.ts 已修過的坑）。 -->
          <button
            class="edit-btn bundle"
            data-testid="download-bundle"
            title="建檔：帳本原樣 + AAR 統計 + 想定包 + 稽核軌跡"
            :disabled="busy"
            @click="doDownloadBundle(ex.id)"
          ><i class="pi pi-download" /> 歸檔封包</button>
          <button
            class="edit-btn danger"
            data-testid="delete-exercise"
            title="刪除演習專案本身——掛在底下的局變回獨立局"
            :disabled="busy"
            @click="run(() => deleteExercise(ex.id)).then(() => (openId = null))"
          ><i class="pi pi-trash" /></button>
        </div>

        <!-- 銷毀模式（[JCATS-A p.16] 資安要求）——**只在已撤收（ARCHIVED）出現**。
             在此之前階段說明寫著「可執行銷毀模式」，面板卻沒有任何入口。 -->
        <template v-if="ex.phase === 'ARCHIVED'">
          <div class="ex-sub">銷毀模式</div>
          <p v-if="!canDestroy" class="dim" data-testid="destroy-forbidden">
            銷毀推演資料限系統管理員。
          </p>
          <div v-else class="ex-destroy" data-testid="exercise-destroy">
            <button
              v-if="destroyFor !== ex.id"
              class="danger-btn"
              data-testid="destroy-open"
              @click="openDestroy(ex.id)"
            >銷毀推演資料…</button>
            <template v-else>
              <p class="warn">
                將永久刪除本演習所有推演局的資料（推演局本身、事件帳本、單位、活狀態），
                <b>無法復原</b>。演習專案與稽核軌跡保留。執行前請先確認歸檔封包已下載。
              </p>
              <label class="confirm-row">
                <span>輸入演習名稱以確認：</span>
                <input
                  v-model="destroyConfirm"
                  data-testid="destroy-confirm-name"
                  :placeholder="ex.name"
                >
              </label>
              <div class="confirm-actions">
                <!-- 逐字相符才可按。**不 trim**——後端是直接比對，前端放寬只會讓按鈕
                     可按、送出後才被拒，把「確認」變成「猜密碼」。 -->
                <button
                  class="danger-btn"
                  data-testid="destroy-submit"
                  :disabled="busy || destroyConfirm !== ex.name"
                  @click="doDestroy(ex)"
                >確認銷毀</button>
                <button class="edit-btn" data-testid="destroy-cancel" :disabled="busy" @click="closeDestroy()">
                  取消
                </button>
              </div>
            </template>
            <!-- 後端回的 DestroyResult：刪了幾局、各表幾筆、清了幾個活狀態鍵。
                 銷毀是不可逆的，「到底刪掉了什麼」必須當場看得到。 -->
            <div
              v-if="destroyResult && destroyResult.exerciseId === ex.id"
              class="destroy-result"
              data-testid="destroy-result"
            >
              已銷毀 {{ destroyResult.result.sessions_destroyed }} 局；
              清除活狀態鍵 {{ destroyResult.result.redis_keys_deleted ?? 0 }} 個。
              <!-- 表名是後端 ORM 類別名（`purge.py` 自省導出）。統裁要讀的是「刪掉了哪一類資料」，
                   `IntelContact 305` 只有寫後端的人看得懂；查無對照仍原樣印英文表名。 -->
              <span
                v-for="[table, n] in deletedRows(destroyResult.result)"
                :key="table"
                class="tag"
                :title="table"
              >{{ dataTableLabel(table) }} {{ n }}</span>
            </div>
          </div>
        </template>

        <!-- 稽核軌跡 -->
        <div class="ex-sub">稽核軌跡</div>
        <ul class="ex-audit" data-testid="exercise-audit">
          <li v-for="a in audit" :key="a.id">
            <span class="dim">{{ fmtWallClock(a.at) }}</span>
            <!-- 稽核軌跡最主要的問題是「誰做的」——`actor_id` 後端一直有回，在此之前沒顯示。 -->
            <b class="actor" :data-testid="`audit-actor-${a.id}`">{{ who(a.actor_id) }}</b>
            {{ AUDIT_ACTION_LABELS[a.action] ?? a.action }}
            <span v-if="a.from_phase && a.to_phase" class="dim">
              {{ phaseLabel(a.from_phase) }} → {{ phaseLabel(a.to_phase) }}
            </span>
          </li>
          <li v-if="!audit.length" class="dim">（無）</li>
        </ul>
      </div>
    </li>
  </ul>
</div>
</template>

<style scoped>
.ex-panel .create {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
/* 表單控制項：整組宣告照抄 lobby.vue 的 `.create input` 與 `button`（就是隔壁分頁
   「新推演名稱／建立推演」那一對）。**必須重寫一份**——scoped CSS 只會把 scope 屬性
   加在子元件的根節點上，lobby 的規則穿不進 ExercisePanel 的內層元素。 */
.create input {
  flex: 1;
  padding: 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0f172a;
  color: #e2e8f0;
}
button {
  padding: 0.5rem 0.75rem;
  border: 0;
  border-radius: 0.25rem;
  background: #2563eb;
  color: white;
  cursor: pointer;
}
/* lobby 的主按鈕沒有 disabled 樣式（它只在送出的那一瞬間 disabled，看不出差別）；
   這裡的「建立演習」在名稱空白時就是 disabled，不變灰會像是壞掉的按鈕，
   故沿用 accounts.vue `.create button:disabled` 與 lobby `.edit-btn:disabled` 的同一組值。
   一條 `button:disabled` 同時蓋掉主按鈕與 .edit-btn（後者特異度較低）。 */
button:disabled {
  opacity: 0.4;
  cursor: default;
}
.ex-msg {
  color: #fca5a5;
  font-size: 0.85rem;
}
.ex-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.ex-card {
  border: 1px solid #1e293b;
  border-radius: 0.4rem;
  background: #0f172a;
}
.ex-hd {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.55rem 0.75rem;
  cursor: pointer;
}
.ex-name {
  color: #f8fafc;
}
/* 階段徽章。色帶照 C2Panel 的 .r-st 慣例（同一套視覺語彙）。 */
.ex-phase {
  padding: 0.05rem 0.4rem;
  border-radius: 0.25rem;
  background: #1e293b;
  font-size: 0.75rem;
}
.ph-prep {
  color: #fcd34d;
}
.ph-rehearsal {
  color: #7dd3fc;
}
.ph-execution {
  color: #86efac;
}
.ph-review {
  color: #c4b5fd;
}
.ph-archived {
  color: #94a3b8;
}
.ex-count {
  margin-left: auto;
  color: #94a3b8;
  font-size: 0.8rem;
}
.ex-body {
  border-top: 1px solid #1e293b;
  padding: 0.6rem 0.75rem 0.75rem;
  font-size: 0.85rem;
}
.ex-sub {
  margin: 0.6rem 0 0.25rem;
  color: #64748b;
  font-size: 0.72rem;
}
.ex-sessions,
.ex-checklist,
.ex-audit {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.ex-sessions a {
  color: #7dd3fc;
}
.tag {
  margin-left: 0.4rem;
  padding: 0 0.3rem;
  border-radius: 0.2rem;
  background: #1e293b;
  font-size: 0.72rem;
}
.dim {
  color: #94a3b8;
  font-size: 0.78rem;
}
.req {
  margin-left: 0.35rem;
  color: #fcd34d;
  font-size: 0.7rem;
}
.ex-attach {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.35rem;
}
/* 掛入列＝lobby 名冊的 `.roster-add`（下拉＋下拉＋按鈕）同款，故照抄 `.r-sel`
   與 `.roster-add .r-sel` / `.roster-add button`。底色取 #0a1626 而非 .sc-select 的
   #0f172a：lobby 的慣例是「控制項比所在面板再深一階」——.sc-select 貼在頁面底色
   (#0a1626) 上，而這裡貼在 .ex-card (#0f172a) 上，跟 .modal input / .r-sel 同情境。 */
.ex-attach select {
  flex: 1 1 auto;
  min-width: 6rem;
  max-width: 9.5rem;
  padding: 0.25rem 0.35rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.78rem;
}
.ex-attach button {
  flex: 0 0 auto;
}
/* 建立/階段時間戳列——與 .dim 同一階視覺（附屬資訊，不搶主體）。 */
.ex-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  color: #94a3b8;
  font-size: 0.78rem;
}
.ex-seal {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.seal-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
/* 簽證人/時間/兩個雜湊：換行擺，避免窄面板把當前雜湊擠出視野——
   那正是不符時最需要看到的一欄。 */
.seal-detail {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  color: #94a3b8;
  font-size: 0.75rem;
}
.ex-seal .ok {
  color: #4ade80;
}
.ex-seal .bad {
  color: #fca5a5;
}
.ex-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.75rem;
  border-top: 1px solid #1e293b;
  padding-top: 0.6rem;
}
/* 歸檔封包從 `<a>` 改成 `<button>`（見上方註解）後仍保留連結藍——它是這一列裡
   唯一的「取得資料」動作。特異度要壓過後面的 `.edit-btn`，故加上 .ex-actions。 */
.ex-actions .bundle {
  color: #7dd3fc;
  font-size: 0.8rem;
}
/* 稽核列的經手人。**左右外距不能省**：Vue 的 whitespace condense 會吃掉元素之間
   含換行的空白節點，不留外距就會渲染成「15:24:08commander 建立演習」。 */
.actor {
  margin: 0 0.35rem;
  color: #e2e8f0;
}
/* 銷毀模式：整塊以紅框圈起來，與其它區塊視覺上明確分離——
   這是面板上唯一不可逆的操作，不該長得像旁邊的「解除簽證」。 */
.ex-destroy {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  align-items: flex-start;
  border: 1px solid #7f1d1d;
  border-radius: 0.3rem;
  padding: 0.5rem;
}
.ex-destroy .warn {
  margin: 0;
  color: #fca5a5;
  font-size: 0.8rem;
  line-height: 1.5;
}
.confirm-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.8rem;
}
/* 名稱要整串看得見才確認得了——太窄的話使用者只看得到尾巴，
   等於在盲打一個「必須逐字相符」的欄位。 */
.confirm-row input {
  min-width: 16rem;
  padding: 0.25rem 0.35rem;
  border: 1px solid #7f1d1d;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.8rem;
}
.confirm-actions {
  display: flex;
  gap: 0.5rem;
}
/* 危險操作的紅——與 accounts.vue 的 `.modal-btns .danger-btn` 同一個值（#dc2626），
   全站「按下去救不回來」統一用它。 */
.danger-btn {
  background: #dc2626;
}
.destroy-result {
  color: #cbd5e1;
  font-size: 0.78rem;
}
/* 與 lobby.vue 的 `.edit-btn` 同一份，只少了它的 `margin-left: 0.5rem`——
   那是 lobby `.session` 列的排版補償，這裡的 .ex-seal / .ex-actions 已自帶 gap。 */
.edit-btn {
  padding: 0.15rem 0.4rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
}
.edit-btn:hover {
  border-color: #2563eb;
  color: #e2e8f0;
}
.edit-btn.danger:hover {
  border-color: #dc2626;
  color: #fca5a5;
}
</style>
