<script setup lang="ts">
// 劇本管理列表（#5）——列出已存想定，可進編輯器載入或刪除。限統裁/管理（後端 RBAC 亦把關）。
import { apiFetch } from '~/composables/useApi'
import type { ApiError } from '~/composables/useApi'

type ScenarioItem = { id: string; name: string; version: string }

const scenarios = ref<ScenarioItem[]>([])
const loading = ref(true)
const errorMsg = ref('')
const busyId = ref('') // 刪除中的列 id

async function refresh() {
  loading.value = true
  errorMsg.value = ''
  try {
    scenarios.value = await apiFetch<ScenarioItem[]>('/scenarios')
  } catch (e) {
    errorMsg.value = `載入失敗：${(e as ApiError).code ?? 'UNKNOWN'}`
  } finally {
    loading.value = false
  }
}

function edit(id: string) {
  navigateTo(`/scenario-editor?load=${encodeURIComponent(id)}`)
}

async function remove(s: ScenarioItem) {
  if (!window.confirm(`確定刪除想定「${s.name}」（v${s.version}）？此動作無法復原。`)) return
  busyId.value = s.id
  errorMsg.value = ''
  try {
    await apiFetch(`/scenarios/${encodeURIComponent(s.id)}`, { method: 'DELETE' })
    await refresh()
  } catch (e) {
    errorMsg.value = `刪除失敗：${(e as ApiError).code ?? 'UNKNOWN'}`
  } finally {
    busyId.value = ''
  }
}

onMounted(refresh)
</script>

<template>
  <main class="scenarios" data-testid="scenarios-page">
    <header class="sc-bar">
      <Button data-testid="sc-back-lobby" size="small" text @click="navigateTo('/lobby')">← 系統首頁</Button>
      <h1>劇本管理</h1>
      <Button data-testid="sc-new" class="sc-new-btn" size="small" @click="navigateTo('/scenario-editor')">
        ＋ 新增劇本
      </Button>
    </header>

    <Message v-if="errorMsg" severity="error" size="small" class="sc-error" data-testid="sc-error">
      {{ errorMsg }}
    </Message>

    <p v-if="loading" data-testid="sc-loading" class="hint">載入中…</p>
    <p v-else-if="scenarios.length === 0" data-testid="sc-empty" class="hint">
      目前沒有已存想定。到編輯器建立並存到伺服器。
    </p>
    <ul v-else data-testid="scenario-list">
      <li v-for="s in scenarios" :key="s.id" class="scenario" data-testid="scenario-item">
        <span class="name">{{ s.name }}</span>
        <span class="ver">v{{ s.version }}</span>
        <span class="spacer" />
        <Button data-testid="sc-edit" size="small" text @click="edit(s.id)">編輯</Button>
        <Button
          data-testid="sc-delete"
          size="small"
          text
          severity="danger"
          :disabled="busyId === s.id"
          @click="remove(s)"
        >
          {{ busyId === s.id ? '刪除中…' : '刪除' }}
        </Button>
      </li>
    </ul>
  </main>
</template>

<style scoped>
.scenarios {
  max-width: 48rem;
  margin: 0 auto;
  padding: 2rem 1rem;
  color: #e2e8f0;
}
.sc-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}
.sc-bar h1 {
  margin: 0;
  font-size: 1.5rem;
}
.sc-new-btn {
  margin-left: auto;
}
.sc-error {
  margin: 0 0 0.75rem;
}
.hint {
  color: #94a3b8;
  font-size: 0.9rem;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.scenario {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  padding: 0.6rem 1rem;
  border: 1px solid #334155;
  border-radius: 0.375rem;
  background: #1e293b;
}
.name {
  font-weight: 600;
}
.ver {
  color: #94a3b8;
  font-size: 0.8125rem;
}
.spacer {
  flex: 1;
}
</style>
