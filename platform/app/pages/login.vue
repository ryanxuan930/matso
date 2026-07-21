<script setup lang="ts">
import type { ApiError } from '~/composables/useApi'

const auth = useAuthStore()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    await navigateTo('/lobby')
  } catch (e) {
    const err = e as ApiError
    error.value =
      err.code === 'AUTH_INVALID_CREDENTIALS' ? '帳號或密碼錯誤' : err.message || '登入失敗'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login">
    <form class="card" @submit.prevent="onSubmit">
      <h1>MATSO 登入</h1>
      <label>
        <span>帳號</span>
        <input v-model="username" data-testid="username" type="text" autocomplete="username" required>
      </label>
      <label>
        <span>密碼</span>
        <input v-model="password" data-testid="password" type="password" autocomplete="current-password" required>
      </label>
      <p v-if="error" data-testid="login-error" class="error">{{ error }}</p>
      <button data-testid="login-submit" type="submit" :disabled="loading">
        {{ loading ? '登入中…' : '登入' }}
      </button>
    </form>
  </main>
</template>

<style scoped>
.login {
  display: flex;
  min-height: 100vh;
  align-items: center;
  justify-content: center;
  background: #0f172a;
}
.card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: 20rem;
  padding: 2rem;
  border-radius: 0.5rem;
  background: #1e293b;
  color: #e2e8f0;
}
h1 {
  margin: 0 0 0.5rem;
  font-size: 1.25rem;
  text-align: center;
}
label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.875rem;
}
input {
  padding: 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0f172a;
  color: #e2e8f0;
}
button {
  margin-top: 0.5rem;
  padding: 0.5rem;
  border: 0;
  border-radius: 0.25rem;
  background: #2563eb;
  color: white;
  cursor: pointer;
}
button:disabled {
  opacity: 0.6;
  cursor: default;
}
.error {
  margin: 0;
  color: #f87171;
  font-size: 0.8125rem;
}
</style>
