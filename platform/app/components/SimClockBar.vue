<script setup lang="ts">
// 系統牆鐘列（#4）：sim tick + 開局以來執行時間 + 目前真實時間。now 初值 0 避免 SSR 水合不一致。
const props = defineProps<{ tick?: number | null; startTime?: string | null }>()

const now = ref(0)
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  now.value = Date.now()
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

function fmtHMS(ms: number): string {
  if (!isFinite(ms) || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

const tickText = computed(() => (props.tick != null ? `T${props.tick}` : 'T—'))
const elapsed = computed(() => {
  if (!now.value || !props.startTime) return '—'
  const t = Date.parse(props.startTime)
  return isNaN(t) ? '—' : fmtHMS(now.value - t)
})
const realClock = computed(() =>
  now.value ? new Date(now.value).toLocaleTimeString('zh-TW', { hour12: false }) : '—',
)
</script>

<template>
  <div class="clockbar" data-testid="sim-clock">
    <span class="seg" title="系統牆鐘（sim tick）"><i>牆鐘</i>{{ tickText }}</span>
    <span class="seg" title="開局以來執行時間"><i>執行</i>{{ elapsed }}</span>
    <span class="seg" title="目前真實時間"><i>現在</i>{{ realClock }}</span>
  </div>
</template>

<style scoped>
.clockbar {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  font-size: 0.78rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #cbd5e1;
}
.seg {
  display: inline-flex;
  gap: 0.3rem;
  align-items: baseline;
}
.seg i {
  font-style: normal;
  color: #64748b;
  font-size: 0.68rem;
}
</style>
