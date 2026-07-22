<script setup lang="ts">
// 帳號管理（#32）——白軍/統裁/管理建立帳號、設定角色（權限）、重設密碼、刪除。
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

type UserView = components['schemas']['UserView']
type UserRole = components['schemas']['UserRole']

const ROLES: UserRole[] = [
  'EXERCISE_DIRECTOR',
  'WHITE_CELL_STAFF',
  'COMMANDER',
  'STAFF',
  'OBSERVER',
  'ANALYST',
  'ADMIN',
]
const ROLE_LABEL: Record<string, string> = {
  EXERCISE_DIRECTOR: '演習統裁',
  WHITE_CELL_STAFF: '白軍幕僚',
  COMMANDER: '指揮官',
  STAFF: '參謀',
  OBSERVER: '觀察員',
  ANALYST: '分析官',
  ADMIN: '系統管理',
}

const auth = useAuthStore()
const canManage = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF', 'ADMIN'].includes(auth.user?.role ?? ''),
)

const users = ref<UserView[]>([])
const loading = ref(true)
const err = ref('')

// 建立帳號
const newUsername = ref('')
const newPassword = ref('')
const newRole = ref<UserRole>('OBSERVER')
const creating = ref(false)

// 重設密碼
const resetting = ref<UserView | null>(null)
const resetPw = ref('')
const confirmDelete = ref<UserView | null>(null)
const busyId = ref<string | null>(null)

async function refresh() {
  loading.value = true
  err.value = ''
  try {
    users.value = await apiFetch<UserView[]>('/users')
  } catch (e) {
    err.value = `載入失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    loading.value = false
  }
}

async function createUser() {
  if (!newUsername.value.trim() || newPassword.value.length < 8) return
  creating.value = true
  err.value = ''
  try {
    await apiFetch<UserView>('/users', {
      method: 'POST',
      body: { username: newUsername.value.trim(), password: newPassword.value, role: newRole.value },
    })
    newUsername.value = ''
    newPassword.value = ''
    newRole.value = 'OBSERVER'
    await refresh()
  } catch (e) {
    err.value = `建立失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    creating.value = false
  }
}

async function changeRole(u: UserView, role: UserRole) {
  busyId.value = u.id
  err.value = ''
  try {
    await apiFetch<UserView>(`/users/${u.id}`, { method: 'PATCH', body: { role } })
    await refresh()
  } catch (e) {
    err.value = `更新失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
    await refresh()
  } finally {
    busyId.value = null
  }
}

async function doResetPw() {
  const u = resetting.value
  if (!u || resetPw.value.length < 8) return
  busyId.value = u.id
  err.value = ''
  try {
    await apiFetch<UserView>(`/users/${u.id}`, { method: 'PATCH', body: { password: resetPw.value } })
    resetting.value = null
    resetPw.value = ''
  } catch (e) {
    err.value = `重設失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    busyId.value = null
  }
}

async function doDelete() {
  const u = confirmDelete.value
  if (!u) return
  busyId.value = u.id
  err.value = ''
  try {
    await apiFetch<unknown>(`/users/${u.id}`, { method: 'DELETE' })
    confirmDelete.value = null
    await refresh()
  } catch (e) {
    err.value = `刪除失敗：${(e as { code?: string }).code ?? 'UNKNOWN'}`
  } finally {
    busyId.value = null
  }
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  if (canManage.value) await refresh()
  else loading.value = false
})
</script>

<template>
  <main class="accounts">
    <header>
      <h1>帳號管理</h1>
      <a class="back" href="/lobby" data-testid="nav-lobby">← 返回首頁</a>
    </header>

    <p v-if="!canManage" class="forbidden" data-testid="accounts-forbidden">
      僅白軍/統裁/管理可管理帳號。
    </p>

    <template v-else>
      <section class="create" data-testid="create-user">
        <input
          v-model="newUsername"
          data-testid="new-username"
          placeholder="帳號名"
          autocomplete="off"
        >
        <input
          v-model="newPassword"
          type="password"
          data-testid="new-password"
          placeholder="初始密碼（≥8）"
          autocomplete="new-password"
        >
        <select v-model="newRole" data-testid="new-role">
          <option v-for="r in ROLES" :key="r" :value="r">{{ ROLE_LABEL[r] }}（{{ r }}）</option>
        </select>
        <button
          data-testid="create-user-btn"
          :disabled="creating || !newUsername.trim() || newPassword.length < 8"
          @click="createUser"
        >
          建立帳號
        </button>
      </section>

      <p v-if="err" class="err" data-testid="accounts-err">{{ err }}</p>

      <section>
        <p v-if="loading" data-testid="accounts-loading">載入中…</p>
        <table v-else class="user-table" data-testid="user-table">
          <thead>
            <tr><th>帳號</th><th>角色（權限）</th><th>建立時間</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id" data-testid="user-row">
              <td class="uname">{{ u.username }}</td>
              <td>
                <select
                  :value="u.role"
                  :disabled="busyId === u.id"
                  data-testid="role-select"
                  @change="changeRole(u, ($event.target as HTMLSelectElement).value as UserRole)"
                >
                  <option v-for="r in ROLES" :key="r" :value="r">{{ ROLE_LABEL[r] }}</option>
                </select>
              </td>
              <td class="created">{{ u.created_at ? String(u.created_at).slice(0, 16).replace('T', ' ') : '—' }}</td>
              <td class="ops">
                <button data-testid="reset-pw" title="重設密碼" @click="resetting = u; resetPw = ''">🔑</button>
                <button
                  class="danger"
                  data-testid="delete-user"
                  title="刪除帳號"
                  :disabled="u.id === auth.user?.id"
                  @click="confirmDelete = u"
                >🗑</button>
              </td>
            </tr>
            <tr v-if="!users.length"><td colspan="4" class="empty">（無帳號）</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 重設密碼 -->
    <div v-if="resetting" class="modal-overlay" data-testid="reset-modal" @click.self="resetting = null">
      <div class="modal">
        <h3>重設「{{ resetting.username }}」密碼</h3>
        <input
          v-model="resetPw"
          type="password"
          data-testid="reset-pw-input"
          placeholder="新密碼（≥8）"
          autocomplete="new-password"
        >
        <div class="modal-btns">
          <button class="ghost" @click="resetting = null">取消</button>
          <button data-testid="reset-pw-confirm" :disabled="resetPw.length < 8" @click="doResetPw">
            重設
          </button>
        </div>
      </div>
    </div>

    <!-- 刪除確認 -->
    <div v-if="confirmDelete" class="modal-overlay" data-testid="delete-modal" @click.self="confirmDelete = null">
      <div class="modal">
        <h3>刪除帳號？</h3>
        <p class="hint">將永久刪除帳號「<b>{{ confirmDelete.username }}</b>」，無法復原。</p>
        <div class="modal-btns">
          <button class="ghost" @click="confirmDelete = null">取消</button>
          <button class="danger-btn" data-testid="confirm-delete-user" @click="doDelete">確認刪除</button>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.accounts {
  max-width: 52rem;
  margin: 0 auto;
  padding: 2rem 1rem;
  color: #e2e8f0;
}
header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 1.5rem;
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
.create {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}
.create input,
.create select,
.modal input {
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.4rem 0.5rem;
}
.create button,
.modal-btns button {
  background: #1e40af;
  color: #fff;
  border: none;
  border-radius: 0.25rem;
  padding: 0.4rem 0.75rem;
  cursor: pointer;
}
.create button:disabled {
  opacity: 0.4;
  cursor: default;
}
.err {
  color: #f87171;
  font-size: 0.85rem;
}
.user-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
.user-table th,
.user-table td {
  text-align: left;
  padding: 0.45rem 0.5rem;
  border-bottom: 1px solid #1e293b;
}
.user-table th {
  color: #94a3b8;
  font-weight: 600;
}
.uname {
  font-weight: 600;
}
.created {
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}
.user-table select {
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.25rem 0.4rem;
}
.ops button {
  background: transparent;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  color: #cbd5e1;
  cursor: pointer;
  padding: 0.15rem 0.4rem;
  margin-right: 0.3rem;
}
.ops button.danger:hover {
  border-color: #dc2626;
  color: #fca5a5;
}
.ops button:disabled {
  opacity: 0.35;
  cursor: default;
}
.empty {
  color: #64748b;
  text-align: center;
}
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(2, 6, 23, 0.7);
}
.modal {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 0.5rem;
  padding: 1.25rem;
  min-width: 20rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.modal h3 {
  margin: 0;
}
.modal .hint {
  color: #94a3b8;
  font-size: 0.82rem;
}
.modal-btns {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.modal-btns .ghost {
  background: transparent;
  border: 1px solid #334155;
  color: #cbd5e1;
}
.modal-btns .danger-btn {
  background: #dc2626;
}
.modal-btns button:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
