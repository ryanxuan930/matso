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

<!-- 全域深色基底（非 scoped，SPEC §13 COP）。統一所有頁面 dark mode，並消除 body 預設邊距的白框。
     注意：專案未把 assets/css/main.css 掛進 nuxt.config 的 css[]，故全域樣式置此以確保載入。 -->
<style>
:root {
  color-scheme: dark;
}
html,
body,
#__nuxt {
  margin: 0;
  padding: 0;
  min-height: 100vh;
  background-color: #0a1626;
  color: #e2e8f0;
  font-family: system-ui, -apple-system, "Segoe UI", "Noto Sans TC", "PingFang TC", sans-serif;
  -webkit-font-smoothing: antialiased;
}
input,
select,
textarea,
button {
  color-scheme: dark;
}
</style>
