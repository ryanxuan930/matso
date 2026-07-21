<script setup lang="ts">
import type { Contact, OwnUnit } from '~/composables/useUnits'

// COP 主作戰圖像（SPEC §13.1 /session/:id/cop）——O4.2 地圖基座 + O4.4 單位/fog of war。
// ?units=N 生成 N 個合成己方單位（FPS 量測 + demo）；固定附三級 contact + 一個 OFFLINE 虛影。
const route = useRoute()
const sessionId = computed(() => String(route.params.id))

const hex = ref(false)
const hillshade = ref(false)
const currentTick = ref(100)

const TYPES = ['INFANTRY', 'ARMOR', 'ARTILLERY', 'RECON', 'HQ']

const ownUnits = computed<OwnUnit[]>(() => {
  const n = Math.min(Math.max(Number(route.query.units) || 0, 0), 2000)
  const units: OwnUnit[] = []
  // 網格散佈於台灣周邊（確定性，不用 random）
  const cols = Math.ceil(Math.sqrt(n)) || 1
  for (let i = 0; i < n; i++) {
    const r = Math.floor(i / cols)
    const c = i % cols
    units.push({
      id: `own-${i}`,
      faction: 'BLUE',
      lng: 120.0 + (c / cols) * 2.0,
      lat: 22.8 + (r / cols) * 1.8,
      unitType: TYPES[i % TYPES.length],
      comms: i % 17 === 0 ? 'OFFLINE' : i % 7 === 0 ? 'DEGRADED' : 'ONLINE',
      lastReportedTick: 100 - (i % 40),
    })
  }
  // 固定示範：一個明確 OFFLINE 虛影
  units.push({
    id: 'own-ghost',
    faction: 'BLUE',
    lng: 121.2,
    lat: 24.2,
    unitType: 'HQ',
    comms: 'OFFLINE',
    lastReportedTick: 60,
  })
  return units
})

// 三級 contact（fog of war）：DETECTED / CLASSIFIED / IDENTIFIED，時效遞減
const contacts = computed<Contact[]>(() => [
  { contactId: 'c-det', fidelity: 'DETECTED', lng: 121.4, lat: 23.5, errorRadiusM: 2000, lastSeenTick: 40 },
  { contactId: 'c-cls', fidelity: 'CLASSIFIED', lng: 121.5, lat: 23.6, errorRadiusM: 800, unitType: 'ARMOR', lastSeenTick: 80 },
  { contactId: 'c-id', fidelity: 'IDENTIFIED', lng: 121.6, lat: 23.7, errorRadiusM: 200, unitType: 'ARTILLERY', designation: '3-BN', lastSeenTick: 98 },
])

async function back() {
  await navigateTo('/lobby')
}
</script>

<template>
  <div class="cop">
    <header class="cop-bar">
      <button data-testid="back-lobby" @click="back">← 大廳</button>
      <span class="sid" data-testid="cop-session">Session {{ sessionId }}</span>
      <span class="count" data-testid="unit-count">單位 {{ ownUnits.length }} · contact {{ contacts.length }}</span>
    </header>
    <div class="map-wrap">
      <ClientOnly>
        <MapCanvas
          :hex-visible="hex"
          :hillshade-visible="hillshade"
          :own-units="ownUnits"
          :contacts="contacts"
          :current-tick="currentTick"
        />
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
.sid,
.count {
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
