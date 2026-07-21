<script setup lang="ts">
// 水合完成標記：E2E（Playwright）在互動前等待 [data-hydrated=true]，避免在事件處理器
// 尚未附掛時點擊而觸發原生表單送出（SSR 水合前的競態）。
const hydrated = ref(false)
onMounted(() => {
  hydrated.value = true
})
</script>

<template>
  <div :data-hydrated="hydrated">
    <NuxtRouteAnnouncer />
    <NuxtPage />
  </div>
</template>
