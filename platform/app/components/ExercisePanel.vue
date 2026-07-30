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
import {
  AUDIT_ACTION_LABELS,
  EXERCISE_PHASE_LABELS,
  SESSION_ROLE_LABELS,
  advancePhase,
  attachSession,
  createExercise,
  deleteExercise,
  detachSession,
  fetchAudit,
  fetchExercises,
  fetchSeal,
  nextPhase,
  phaseLabel,
  sealParams,
  tickChecklist,
  unsealParams,
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
  openId.value = id
  audit.value = await fetchAudit(id).catch(() => [])
  seal.value = await fetchSeal(id).catch(() => null)
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

await refresh()
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
        <!-- 掛在底下的局 -->
        <div class="ex-sub">推演局</div>
        <ul class="ex-sessions" data-testid="exercise-sessions">
          <li v-for="s in ex.sessions" :key="s.id">
            <a :href="`/session/${s.id}/cop`">{{ s.name }}</a>
            <span class="tag">{{ SESSION_ROLE_LABELS[s.session_role ?? ''] ?? '未指定' }}</span>
            <span class="dim">{{ s.status }}</span>
            <button
              class="edit-btn"
              data-testid="detach-session"
              title="卸下（該局變回獨立局，不刪任何資料）"
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
            </label>
          </li>
        </ul>

        <!-- WP-B4 參數簽證 -->
        <div class="ex-sub">參數簽證（WP-B4）</div>
        <div class="ex-seal" data-testid="exercise-seal">
          <template v-if="seal">
            <span :class="seal.matches ? 'ok' : 'bad'" data-testid="seal-status">
              {{ seal.matches ? '✓ 參數與簽證相符' : '⚠ 參數已被更動——此演習的局將拒絕啟動' }}
            </span>
            <code class="dim">{{ seal.content_hash.slice(0, 12) }}…</code>
            <button
              class="edit-btn"
              data-testid="unseal-params"
              title="解除簽證（會進稽核軌跡）"
              :disabled="busy"
              @click="run(() => unsealParams(ex.id))"
            >解除</button>
          </template>
          <template v-else>
            <span class="dim">未簽證。簽證後全域參數（武器庫／推演參數）唯讀。</span>
            <button data-testid="seal-params" :disabled="busy" @click="run(() => sealParams(ex.id))">
              簽證鎖定
            </button>
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
          <a
            class="bundle"
            data-testid="download-bundle"
            :href="`/api/v1/exercises/${ex.id}/bundle`"
            target="_blank"
            title="撤收建檔：帳本原樣 + AAR 統計 + 想定包 + 稽核軌跡"
          >歸檔封包</a>
          <button
            class="edit-btn danger"
            data-testid="delete-exercise"
            title="刪除演習專案本身——掛在底下的局變回獨立局，不刪任何推演資料"
            :disabled="busy"
            @click="run(() => deleteExercise(ex.id)).then(() => (openId = null))"
          ><i class="pi pi-trash" /></button>
        </div>

        <!-- 稽核軌跡 -->
        <div class="ex-sub">稽核軌跡</div>
        <ul class="ex-audit" data-testid="exercise-audit">
          <li v-for="a in audit" :key="a.id">
            <span class="dim">{{ a.at.slice(0, 19).replace('T', ' ') }}</span>
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
.ex-seal {
  display: flex;
  align-items: center;
  gap: 0.5rem;
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
.bundle {
  color: #7dd3fc;
  font-size: 0.85rem;
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
