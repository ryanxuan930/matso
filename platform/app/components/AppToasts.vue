<script setup lang="ts">
// 全域通知堆疊（#7）——由 useToasts() 驅動；掛在 app.vue 供全站使用。
import { useToasts } from '~/composables/useToasts'

const { toasts, dismiss } = useToasts()
</script>

<template>
  <div class="toast-host" data-testid="toast-host">
    <div
      v-for="t in toasts"
      :key="t.id"
      class="toast"
      :class="t.severity"
      data-testid="toast"
      role="alert"
    >
      <button class="x" data-testid="toast-dismiss" @click="dismiss(t.id)">✕</button>
      <div class="t-title">{{ t.title }}</div>
      <div v-if="t.detail" class="t-detail">{{ t.detail }}</div>
      <ul v-if="t.lines?.length" class="t-lines">
        <li v-for="(l, i) in t.lines" :key="i">{{ l }}</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-width: 24rem;
  pointer-events: none;
}
.toast {
  position: relative;
  pointer-events: auto;
  padding: 0.7rem 1.75rem 0.7rem 0.85rem;
  border-radius: 0.5rem;
  border-left: 4px solid #64748b;
  background: rgba(15, 23, 42, 0.97);
  color: #e2e8f0;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
  font-size: 0.8rem;
  animation: toast-in 0.18s ease-out;
}
.toast.error {
  border-left-color: #ef4444;
}
.toast.warn {
  border-left-color: #f59e0b;
}
.toast.success {
  border-left-color: #22c55e;
}
.toast.info {
  border-left-color: #38bdf8;
}
.t-title {
  font-weight: 600;
  color: #f8fafc;
}
.t-detail {
  margin-top: 0.25rem;
  color: #cbd5e1;
  line-height: 1.4;
}
.t-lines {
  margin: 0.35rem 0 0;
  padding-left: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.t-lines li {
  color: #fca5a5;
  line-height: 1.4;
}
.toast .x {
  position: absolute;
  top: 0.35rem;
  right: 0.4rem;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 0.85rem;
}
.toast .x:hover {
  color: #e2e8f0;
}
@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateX(12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}
</style>
