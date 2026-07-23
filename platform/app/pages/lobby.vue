<script setup lang="ts">
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

type SessionSummary = components['schemas']['SessionSummary']

const auth = useAuthStore()
// 想定編輯限統裁/管理角色（SPEC §11.2 / §12）。
const canEditScenario = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF', 'ADMIN'].includes(auth.user?.role ?? ''),
)
const sessions = ref<SessionSummary[]>([])
const newName = ref('')
const loading = ref(true)
const creating = ref(false)

async function refresh() {
  loading.value = true
  try {
    sessions.value = await apiFetch<SessionSummary[]>('/sessions')
  } finally {
    loading.value = false
  }
}

async function createSession() {
  if (!newName.value.trim()) return
  creating.value = true
  try {
    await apiFetch<SessionSummary>('/sessions', { method: 'POST', body: { name: newName.value } })
    newName.value = ''
    await refresh()
  } finally {
    creating.value = false
  }
}

// 從已存想定開局（#7）——限統裁/管理。
type ScenarioItem = { id: string; name: string; version: string }
const scenarios = ref<ScenarioItem[]>([])
const selectedScenarioId = ref('')

async function loadScenarios() {
  if (!canEditScenario.value) return
  scenarios.value = await apiFetch<ScenarioItem[]>('/scenarios').catch(() => [])
}

async function createFromScenario() {
  if (!selectedScenarioId.value) return
  creating.value = true
  try {
    await apiFetch<SessionSummary>('/sessions', {
      method: 'POST',
      body: { name: '劇本局', scenario_id: selectedScenarioId.value },
    })
    selectedScenarioId.value = ''
    await refresh()
  } finally {
    creating.value = false
  }
}

// 編輯已開推演設定（#16）——名稱 + 想定世界初始日期時間。限統裁/管理。
const editing = ref<SessionSummary | null>(null)
const editName = ref('')
const editWorldTime = ref('') // datetime-local 值（YYYY-MM-DDTHH:mm）
const editErr = ref('')
function openEdit(s: SessionSummary) {
  editing.value = s
  editName.value = s.name
  // ISO8601 → datetime-local（去尾秒/時區）
  editWorldTime.value = s.world_start_time ? String(s.world_start_time).slice(0, 16) : ''
  editErr.value = ''
}
async function saveEdit() {
  if (!editing.value) return
  editErr.value = ''
  try {
    await apiFetch<SessionSummary>(`/sessions/${editing.value.id}`, {
      method: 'PATCH',
      body: { name: editName.value.trim(), world_start_time: editWorldTime.value || '' },
    })
    editing.value = null
    await refresh()
  } catch (e) {
    editErr.value = `儲存失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  }
}

// #31 封存 / 歷史 / 刪除——限統裁/管理。
const activeSessions = computed(() => sessions.value.filter((s) => !s.archived_at))
const historySessions = computed(() => sessions.value.filter((s) => s.archived_at))
const showHistory = ref(false)
const confirmDelete = ref<SessionSummary | null>(null)
const busyId = ref<string | null>(null)

async function archiveSession(s: SessionSummary, archive: boolean) {
  busyId.value = s.id
  try {
    const verb = archive ? 'archive' : 'unarchive'
    await apiFetch<SessionSummary>(`/sessions/${s.id}/${verb}`, { method: 'POST' })
    await refresh()
  } finally {
    busyId.value = null
  }
}

async function doDelete() {
  const s = confirmDelete.value
  if (!s) return
  busyId.value = s.id
  try {
    await apiFetch<unknown>(`/sessions/${s.id}`, { method: 'DELETE' })
    confirmDelete.value = null
    await refresh()
  } finally {
    busyId.value = null
  }
}

async function onLogout() {
  auth.logout()
  await navigateTo('/login')
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  await Promise.all([refresh(), loadScenarios()])
})
</script>

<template>
  <main class="lobby">
    <header>
      <h1>系統首頁</h1>
      <div class="who">
        <!-- 劇本編輯器入口移至「劇本管理」頁（新劇本／編輯按鈕）；首頁不再重複。 -->
        <a
          v-if="canEditScenario"
          class="help"
          href="/scenarios"
          data-testid="nav-scenarios"
        >劇本管理</a>
        <a
          v-if="canEditScenario"
          class="help"
          href="/armory"
          data-testid="nav-armory"
        >武器庫</a>
        <a
          v-if="canEditScenario"
          class="help"
          href="/accounts"
          data-testid="nav-accounts"
        >帳號管理</a>
        <span v-if="auth.user" data-testid="current-user">{{ auth.user.username }}（{{ auth.user.role }}）</span>
        <button data-testid="logout" @click="onLogout">登出</button>
      </div>
    </header>

    <section class="create">
      <input v-model="newName" data-testid="new-session-name" placeholder="新推演名稱" @keyup.enter="createSession">
      <button data-testid="create-session" :disabled="creating" @click="createSession">建立推演</button>
    </section>

    <section v-if="canEditScenario && scenarios.length" class="create" data-testid="scenario-create">
      <select v-model="selectedScenarioId" data-testid="scenario-select" class="sc-select">
        <option value="">選劇本開局…</option>
        <option v-for="s in scenarios" :key="s.id" :value="s.id">{{ s.name }} · v{{ s.version }}</option>
      </select>
      <button
        data-testid="create-from-scenario"
        :disabled="creating || !selectedScenarioId"
        @click="createFromScenario"
      >
        從劇本建立
      </button>
    </section>

    <section>
      <p v-if="loading" data-testid="lobby-loading">載入中…</p>
      <p v-else-if="activeSessions.length === 0" data-testid="lobby-empty">目前沒有進行中的推演，建立一個開始。</p>
      <ul v-else data-testid="session-list">
        <li
          v-for="s in activeSessions"
          :key="s.id"
          class="session"
          data-testid="session-item"
          @click="navigateTo(`/session/${s.id}/cop`)"
        >
          <span class="name">{{ s.name }}</span>
          <span class="meta">{{ s.mode }} · {{ s.status }}</span>
          <span v-if="s.my_faction" class="faction">{{ s.my_faction }}</span>
          <button
            v-if="canEditScenario"
            class="edit-btn"
            data-testid="edit-session"
            title="編輯設定"
            @click.stop="openEdit(s)"
          ><i class="pi pi-cog" /></button>
          <button
            v-if="canEditScenario"
            class="edit-btn"
            data-testid="archive-session"
            title="封存（移入歷史）"
            :disabled="busyId === s.id"
            @click.stop="archiveSession(s, true)"
          ><i class="pi pi-inbox" /></button>
        </li>
      </ul>
    </section>

    <!-- #31 歷史（已封存）——限統裁/管理 -->
    <section v-if="canEditScenario" class="history">
      <button class="hist-toggle" data-testid="toggle-history" @click="showHistory = !showHistory">
        {{ showHistory ? '▾' : '▸' }} 歷史推演（{{ historySessions.length }}）
      </button>
      <ul v-if="showHistory && historySessions.length" data-testid="history-list">
        <li
          v-for="s in historySessions"
          :key="s.id"
          class="session archived"
          data-testid="history-item"
        >
          <span class="name" @click="navigateTo(`/session/${s.id}/cop`)">{{ s.name }}</span>
          <span class="meta">{{ s.mode }} · 已封存</span>
          <button
            class="edit-btn"
            data-testid="unarchive-session"
            title="還原（移回進行中）"
            :disabled="busyId === s.id"
            @click.stop="archiveSession(s, false)"
          ><i class="pi pi-replay" /></button>
          <button
            class="edit-btn danger"
            data-testid="delete-session"
            title="永久刪除"
            :disabled="busyId === s.id"
            @click.stop="confirmDelete = s"
          ><i class="pi pi-trash" /></button>
        </li>
      </ul>
      <p v-else-if="showHistory" class="hist-empty" data-testid="history-empty">（無封存推演）</p>
    </section>

    <!-- 編輯已開推演設定（#16） -->
    <div v-if="editing" class="modal-overlay" data-testid="edit-session-modal" @click.self="editing = null">
      <div class="modal">
        <h3>編輯推演設定</h3>
        <label>名稱 <input v-model="editName" data-testid="edit-session-name"></label>
        <label>想定初始日期時間
          <input v-model="editWorldTime" type="datetime-local" data-testid="edit-world-time">
        </label>
        <p class="modal-hint">想定世界的 t=0 日期時間（供日照/晨昏推算）。留空＝未設定。</p>
        <p v-if="editErr" class="modal-err" data-testid="edit-session-err">{{ editErr }}</p>
        <div class="modal-btns">
          <button class="ghost" @click="editing = null">取消</button>
          <button data-testid="save-session-edit" @click="saveEdit">儲存</button>
        </div>
      </div>
    </div>

    <!-- #31 刪除二次確認 -->
    <div
      v-if="confirmDelete"
      class="modal-overlay"
      data-testid="delete-confirm-modal"
      @click.self="confirmDelete = null"
    >
      <div class="modal">
        <h3>永久刪除推演？</h3>
        <p class="modal-hint">
          將永久刪除「<b>{{ confirmDelete.name }}</b>」及其所有單位、事件、標註。此動作無法復原。
        </p>
        <div class="modal-btns">
          <button class="ghost" @click="confirmDelete = null">取消</button>
          <button class="danger-btn" data-testid="confirm-delete" @click="doDelete">確認刪除</button>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.lobby {
  max-width: 48rem;
  margin: 0 auto;
  padding: 2rem 1rem;
  color: #e2e8f0;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}
h1 {
  margin: 0;
  font-size: 1.5rem;
}
.who {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  font-size: 0.875rem;
}
.who .help {
  color: #60a5fa;
  text-decoration: none;
}
.who .help:hover {
  text-decoration: underline;
}
.who span {
  color: #94a3b8;
}
.create {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
}
.create input,
.sc-select {
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
ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.session {
  display: flex;
  gap: 1rem;
  align-items: center;
  padding: 0.75rem 1rem;
  border: 1px solid #334155;
  border-radius: 0.375rem;
  background: #1e293b;
  cursor: pointer;
}
.session:hover {
  border-color: #2563eb;
}
.name {
  font-weight: 600;
}
.meta {
  color: #94a3b8;
  font-size: 0.8125rem;
}
.faction {
  margin-left: auto;
  padding: 0.125rem 0.5rem;
  border-radius: 0.25rem;
  background: #334155;
  font-size: 0.75rem;
}
.edit-btn {
  margin-left: 0.5rem;
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
.edit-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.edit-btn.danger:hover {
  border-color: #dc2626;
  color: #fca5a5;
}
/* #31 歷史區 */
.history {
  margin-top: 1.25rem;
  border-top: 1px solid #1e293b;
  padding-top: 0.75rem;
}
.hist-toggle {
  background: transparent;
  border: none;
  color: #94a3b8;
  cursor: pointer;
  font-size: 0.85rem;
  padding: 0.25rem 0;
}
.hist-toggle:hover {
  color: #e2e8f0;
}
.session.archived {
  opacity: 0.72;
  cursor: default;
}
.session.archived .name {
  cursor: pointer;
}
.hist-empty {
  color: #64748b;
  font-size: 0.8rem;
  padding-left: 0.5rem;
}
.danger-btn {
  background: #dc2626;
  color: #fff;
  border: none;
  border-radius: 0.25rem;
  padding: 0.4rem 0.75rem;
  cursor: pointer;
}
.danger-btn:hover {
  background: #b91c1c;
}
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
}
.modal {
  width: 22rem;
  max-width: 90vw;
  padding: 1.25rem;
  border-radius: 0.5rem;
  border: 1px solid #334155;
  background: #0f172a;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.modal h3 {
  margin: 0;
  font-size: 1rem;
}
.modal label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.8rem;
  color: #94a3b8;
}
.modal input {
  padding: 0.4rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
}
.modal-hint {
  margin: 0;
  font-size: 0.72rem;
  color: #64748b;
}
.modal-err {
  margin: 0;
  color: #f87171;
  font-size: 0.78rem;
}
.modal-btns {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.25rem;
}
.modal-btns .ghost {
  background: transparent;
  border: 1px solid #334155;
  color: #e2e8f0;
}
</style>
