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

async function onLogout() {
  auth.logout()
  await navigateTo('/login')
}

onMounted(async () => {
  if (!auth.user) await auth.fetchMe()
  await refresh()
})
</script>

<template>
  <main class="lobby">
    <header>
      <h1>推演大廳</h1>
      <div class="who">
        <a
          v-if="canEditScenario"
          class="help"
          href="/scenario-editor"
          data-testid="nav-scenario-editor"
        >想定編輯器</a>
        <a class="help" href="/help">操作教學</a>
        <span v-if="auth.user" data-testid="current-user">{{ auth.user.username }}（{{ auth.user.role }}）</span>
        <button data-testid="logout" @click="onLogout">登出</button>
      </div>
    </header>

    <section class="create">
      <input v-model="newName" data-testid="new-session-name" placeholder="新推演名稱" @keyup.enter="createSession">
      <button data-testid="create-session" :disabled="creating" @click="createSession">建立推演</button>
    </section>

    <section>
      <p v-if="loading" data-testid="lobby-loading">載入中…</p>
      <p v-else-if="sessions.length === 0" data-testid="lobby-empty">目前沒有推演，建立一個開始。</p>
      <ul v-else data-testid="session-list">
        <li
          v-for="s in sessions"
          :key="s.id"
          class="session"
          data-testid="session-item"
          @click="navigateTo(`/session/${s.id}/cop`)"
        >
          <span class="name">{{ s.name }}</span>
          <span class="meta">{{ s.mode }} · {{ s.status }}</span>
          <span v-if="s.my_faction" class="faction">{{ s.my_faction }}</span>
        </li>
      </ul>
    </section>
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
</style>
