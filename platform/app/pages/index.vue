<script setup lang="ts">
// 首頁：交由全域守衛導向（已登入→/lobby，未登入→/login）
const { access } = useAuthTokens()
await navigateTo(access.value ? '/lobby' : '/login')
</script>

<template>
  <div />
</template>
