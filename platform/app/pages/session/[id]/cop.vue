<script setup lang="ts">
// COP 主作戰圖像（SPEC §13.1 /session/:id/cop）——O4.2 地圖基座（單位/WS 於 O4.3/O4.4）。
const route = useRoute()
const sessionId = computed(() => String(route.params.id))

const hex = ref(false)
const hillshade = ref(false)

async function back() {
  await navigateTo('/lobby')
}
</script>

<template>
  <div class="cop">
    <header class="cop-bar">
      <button data-testid="back-lobby" @click="back">← 大廳</button>
      <span class="sid" data-testid="cop-session">Session {{ sessionId }}</span>
    </header>
    <div class="map-wrap">
      <ClientOnly>
        <MapCanvas :hex-visible="hex" :hillshade-visible="hillshade" />
        <template #fallback>
          <div class="map-loading" data-testid="map-loading">地圖載入中…</div>
        </template>
      </ClientOnly>
      <LayerToggles v-model:hex="hex" v-model:hillshade="hillshade" />
    </div>
  </div>
</template>

<style scoped>
.cop {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0a1626;
  color: #e2e8f0;
}
.cop-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: #0f172a;
  border-bottom: 1px solid #1e293b;
}
.cop-bar button {
  padding: 0.25rem 0.75rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
}
.sid {
  font-size: 0.875rem;
  color: #94a3b8;
}
.map-wrap {
  position: relative;
  flex: 1;
}
.map-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
}
</style>
