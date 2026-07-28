<script setup lang="ts">
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'
import {
  ASSIGNABLE_ROLES,
  PARTICIPANT_ROLE_LABELS,
  assignParticipant,
  fetchAllUsers,
  fetchRoster,
  removeParticipant,
  type ParticipantRoster,
  type UserView,
} from '~/composables/useParticipants'

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

// #79 複製推演為新局——沿用部署/編裝/名冊/AI 指派，新 RNG 種子。限統裁/管理。
const cloning = ref<SessionSummary | null>(null)
const cloneName = ref('')
const cloneBusy = ref(false)
function openClone(s: SessionSummary) {
  cloning.value = s
  cloneName.value = `${s.name}（副本）`
}
async function doClone() {
  const s = cloning.value
  if (!s) return
  cloneBusy.value = true
  try {
    const created = await apiFetch<SessionSummary>(`/sessions/${s.id}/clone`, {
      method: 'POST',
      body: { name: cloneName.value.trim() || null },
    })
    cloning.value = null
    await refresh()
    await navigateTo(`/session/${created.id}/cop`) // 直接進新局 COP
  } finally {
    cloneBusy.value = false
  }
}

// 參與者名冊——指派帳號↔陣營↔角色（決定誰能操控/查看哪個陣營）。限統裁/管理。
const rosterFor = ref<SessionSummary | null>(null)
const roster = ref<ParticipantRoster | null>(null)
const allUsers = ref<UserView[]>([])
const rosterErr = ref('')
const rosterBusy = ref(false)
const addUserId = ref('')
const addFaction = ref('')
const addRole = ref('COMMANDER')
const ROLE_LABELS = PARTICIPANT_ROLE_LABELS
const ROLE_OPTIONS = ASSIGNABLE_ROLES
// 已在名冊中的帳號不重複列於「新增」下拉。
const assignableUsers = computed(() => {
  const inRoster = new Set((roster.value?.participants ?? []).map((p) => p.user_id))
  return allUsers.value.filter((u) => !inRoster.has(u.id))
})
async function openRoster(s: SessionSummary) {
  rosterFor.value = s
  roster.value = null
  rosterErr.value = ''
  addUserId.value = ''
  addRole.value = 'COMMANDER'
  try {
    const [r, us] = await Promise.all([fetchRoster(s.id), fetchAllUsers()])
    roster.value = r
    allUsers.value = us
    addFaction.value = r.factions[0] ?? ''
  } catch (e) {
    rosterErr.value = `載入名冊失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  }
}
async function doAssign() {
  if (!rosterFor.value || !addUserId.value || !addFaction.value) return
  rosterBusy.value = true
  rosterErr.value = ''
  try {
    await assignParticipant(rosterFor.value.id, addUserId.value, addFaction.value, addRole.value)
    roster.value = await fetchRoster(rosterFor.value.id)
    addUserId.value = ''
  } catch (e) {
    rosterErr.value = `指派失敗：${(e as { code?: string; message?: string }).message ?? (e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    rosterBusy.value = false
  }
}
async function doReassign(userId: string, faction: string, role: string, unitScope: string[] = []) {
  if (!rosterFor.value) return
  rosterBusy.value = true
  rosterErr.value = ''
  try {
    await assignParticipant(rosterFor.value.id, userId, faction, role, unitScope)
    roster.value = await fetchRoster(rosterFor.value.id)
  } catch (e) {
    rosterErr.value = `更新失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  } finally {
    rosterBusy.value = false
  }
}
// unit_scope（限指揮特定單位子集）——展開/收合的列 + 該陣營單位清單 + 切換。
const scopeEditFor = ref('')
function unitsOfFaction(faction: string) {
  return (roster.value?.units ?? []).filter((u) => u.faction === faction)
}
function toggleScopeUnit(p: { user_id: string; faction: string; role: string; unit_scope?: string[] }, unitId: string) {
  const cur = new Set(p.unit_scope ?? [])
  if (cur.has(unitId)) cur.delete(unitId)
  else cur.add(unitId)
  doReassign(p.user_id, p.faction, p.role, [...cur])
}
async function doRemoveParticipant(userId: string) {
  if (!rosterFor.value) return
  rosterBusy.value = true
  rosterErr.value = ''
  try {
    await removeParticipant(rosterFor.value.id, userId)
    roster.value = await fetchRoster(rosterFor.value.id)
  } catch (e) {
    rosterErr.value = `移除失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  } finally {
    rosterBusy.value = false
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
        <a
          v-if="canEditScenario"
          class="help"
          href="/system-settings"
          data-testid="nav-system-settings"
        >系統設定</a>
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
            data-testid="roster-session"
            title="參與者（指派帳號↔陣營↔角色）"
            @click.stop="openRoster(s)"
          ><i class="pi pi-users" /></button>
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
            data-testid="clone-session"
            title="複製為新局（沿用部署/編裝/AI 指派，建議開打前複製）"
            :disabled="busyId === s.id"
            @click.stop="openClone(s)"
          ><i class="pi pi-copy" /></button>
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

    <!-- 參與者名冊——指派帳號↔陣營↔角色（決定操控/查看範圍） -->
    <div v-if="rosterFor" class="modal-overlay" data-testid="roster-modal" @click.self="rosterFor = null">
      <div class="modal roster-modal">
        <h3>參與者 · {{ rosterFor.name }}</h3>
        <p class="modal-hint">指派帳號到陣營與角色：指揮官/參謀＝可操控該陣營；觀察員＝只查看；白軍/統裁＝全知。</p>
        <p v-if="rosterErr" class="modal-err" data-testid="roster-err">{{ rosterErr }}</p>

        <ul v-if="roster" class="roster-list" data-testid="roster-list">
          <li v-for="p in roster.participants" :key="p.user_id" class="roster-row-wrap" data-testid="roster-item">
            <div class="roster-row">
              <span class="r-user">{{ p.username }}</span>
              <select
                :value="p.faction"
                class="r-sel"
                data-testid="roster-faction"
                :disabled="rosterBusy"
                @change="doReassign(p.user_id, ($event.target as HTMLSelectElement).value, p.role, [])"
              >
                <option v-for="f in roster.factions" :key="f" :value="f">{{ f }}</option>
              </select>
              <select
                :value="p.role"
                class="r-sel"
                data-testid="roster-role"
                :disabled="rosterBusy"
                @change="doReassign(p.user_id, p.faction, ($event.target as HTMLSelectElement).value, p.unit_scope ?? [])"
              >
                <option v-for="rr in ROLE_OPTIONS" :key="rr" :value="rr">{{ ROLE_LABELS[rr] ?? rr }}</option>
              </select>
              <button
                v-if="unitsOfFaction(p.faction).length"
                class="edit-btn"
                data-testid="roster-scope-toggle"
                :title="`限指揮單位（${(p.unit_scope ?? []).length ? (p.unit_scope ?? []).length + ' 個' : '全部'}）`"
                @click="scopeEditFor = scopeEditFor === p.user_id ? '' : p.user_id"
              >
                <i class="pi pi-crosshairs" />
                <span class="scope-badge">{{ (p.unit_scope ?? []).length || '全' }}</span>
              </button>
              <button
                class="edit-btn danger"
                data-testid="roster-remove"
                title="移除參與資格"
                :disabled="rosterBusy"
                @click="doRemoveParticipant(p.user_id)"
              ><i class="pi pi-times" /></button>
            </div>
            <div v-if="scopeEditFor === p.user_id" class="scope-panel" data-testid="roster-scope-panel">
              <span class="scope-hint">限指揮單位（不勾＝整個 {{ p.faction }} 陣營）：</span>
              <label v-for="u in unitsOfFaction(p.faction)" :key="u.id" class="scope-unit">
                <input
                  type="checkbox"
                  :checked="(p.unit_scope ?? []).includes(u.id)"
                  :disabled="rosterBusy"
                  @change="toggleScopeUnit(p, u.id)"
                >{{ u.designation }}
              </label>
            </div>
          </li>
          <li v-if="!roster.participants.length" class="roster-empty">（尚無參與者）</li>
        </ul>
        <p v-else class="modal-hint">載入中…</p>

        <div v-if="roster" class="roster-add" data-testid="roster-add">
          <select v-model="addUserId" class="r-sel" data-testid="roster-add-user">
            <option value="">＋ 選帳號…</option>
            <option v-for="u in assignableUsers" :key="u.id" :value="u.id">{{ u.username }}（{{ u.role }}）</option>
          </select>
          <select v-model="addFaction" class="r-sel" data-testid="roster-add-faction">
            <option v-for="f in roster.factions" :key="f" :value="f">{{ f }}</option>
          </select>
          <select v-model="addRole" class="r-sel" data-testid="roster-add-role">
            <option v-for="rr in ROLE_OPTIONS" :key="rr" :value="rr">{{ ROLE_LABELS[rr] ?? rr }}</option>
          </select>
          <button data-testid="roster-assign" :disabled="!addUserId || rosterBusy" @click="doAssign">指派</button>
        </div>

        <div class="modal-btns">
          <button class="ghost" data-testid="roster-close" @click="rosterFor = null">關閉</button>
        </div>
      </div>
    </div>

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

    <!-- #79 複製為新局 -->
    <div v-if="cloning" class="modal-overlay" data-testid="clone-modal" @click.self="cloning = null">
      <div class="modal">
        <h3>複製推演為新局</h3>
        <p class="modal-hint">
          沿用「<b>{{ cloning.name }}</b>」目前的單位部署、編裝、地圖標註、參與者名冊與 AI 指派，
          另開一局並給新的隨機種子。<br>
          提示：於<b>開打前</b>複製即為純淨初始局；若已交戰，將沿用當下的座標與戰力。
        </p>
        <label>新局名稱 <input v-model="cloneName" data-testid="clone-name" @keyup.enter="doClone"></label>
        <div class="modal-btns">
          <button class="ghost" @click="cloning = null">取消</button>
          <button data-testid="clone-confirm" :disabled="cloneBusy" @click="doClone">建立副本</button>
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
/* 參與者名冊 */
.roster-modal {
  width: 30rem;
}
.roster-list {
  gap: 0.35rem;
  max-height: 40vh;
  overflow-y: auto;
}
.roster-row-wrap {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.roster-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.scope-badge {
  font-size: 0.7rem;
  margin-left: 0.15rem;
}
.scope-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem 0.7rem;
  padding: 0.35rem 0.5rem;
  margin-left: 0.5rem;
  border-left: 2px solid #334155;
  background: #0a1626;
  border-radius: 0.25rem;
}
.scope-hint {
  flex-basis: 100%;
  font-size: 0.72rem;
  color: #64748b;
}
.scope-unit {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.78rem;
  color: #cbd5e1;
  cursor: pointer;
}
.roster-row .r-user {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.85rem;
}
.r-sel {
  flex: 0 0 auto;
  max-width: 9.5rem;
  padding: 0.25rem 0.35rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.78rem;
}
.roster-empty {
  color: #64748b;
  font-size: 0.82rem;
}
.roster-add {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  flex-wrap: wrap;
  border-top: 1px solid #1e293b;
  padding-top: 0.6rem;
}
.roster-add .r-sel {
  flex: 1 1 auto;
  min-width: 6rem;
}
.roster-add button {
  flex: 0 0 auto;
}
</style>
