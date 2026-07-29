<script setup lang="ts">
/**
 * COP 裝備管理面板（WP-G1b 自 cop.vue 抽出）——白軍編任一單位編裝 + 設各軍自編權限；
 * 一般角色（該局開放自編時）僅能編本軍單位。
 *
 * 開關由頁面持有（`equipMgr`），故本元件只在被渲染時存在，關閉一律 emit。
 */
import { factionColor } from '~/composables/useUnits'
import type { UnitView } from '~/composables/useOrders'

defineProps<{
  sessionId: string
  /** 全知（統裁/白軍/管理）——自編權限那一區僅其可見。 */
  canControl: boolean
  /** 可編裝單位（依陣營分組）：白軍見全部，一般角色僅本軍。 */
  editableFactions: { faction: string; units: UnitView[] }[]
  /** 本局**全部**陣營分組——自編權限那一區列的是全部陣營，不只可編裝的。 */
  unitsByFaction: { faction: string; units: UnitView[] }[]
  /** 供編裝標題顯示番號。 */
  realUnits: UnitView[]
  /** 目前開放自編的陣營清單。 */
  orbatPerms: string[]
  togglePerm: (faction: string) => void
}>()

defineEmits<{ (e: 'close'): void }>()

/** 目前選取要編裝的單位（面板內狀態，不需外洩）。 */
const equipUnitId = defineModel<string>('equipUnitId', { required: true })
</script>

<template>
<div class="equip-overlay" data-testid="equip-mgr" @click.self="$emit('close')">
  <div class="equip-modal">
    <div class="eq-hd">
      <h3><i class="pi pi-box" /> 裝備管理</h3>
      <button class="eq-x" data-testid="equip-close" @click="$emit('close')"><i class="pi pi-times" /></button>
    </div>

    <div v-if="canControl" class="eq-perms" data-testid="equip-perms">
      <div class="eq-perms-hd">各軍自編權限（開放後該陣營指揮官可自行編裝本軍單位）</div>
      <div class="eq-perms-row">
        <label
          v-for="g in unitsByFaction"
          :key="g.faction"
          class="eq-perm"
          :data-testid="`equip-perm-${g.faction}`"
        >
          <input
            type="checkbox"
            :checked="orbatPerms.includes(g.faction)"
            @change="togglePerm(g.faction)"
          >
          <span class="u-dot" :style="{ background: factionColor(g.faction) }" />{{ g.faction }}
        </label>
        <span v-if="!unitsByFaction.length" class="eq-hint">（本局尚無單位）</span>
      </div>
    </div>

    <div class="eq-body">
      <div class="eq-units" data-testid="equip-unit-list">
        <div v-for="g in editableFactions" :key="g.faction" class="eq-fac">
          <div class="eq-fac-hd">
            <span class="u-dot" :style="{ background: factionColor(g.faction) }" /><b>{{ g.faction }}</b>
            <span class="dim">· {{ g.units.length }}</span>
          </div>
          <button
            v-for="u in g.units"
            :key="u.id"
            class="eq-unit"
            :class="{ sel: u.id === equipUnitId }"
            data-testid="equip-unit"
            @click="equipUnitId = u.id"
          >
            {{ u.designation }}
          </button>
        </div>
        <p v-if="!editableFactions.length" class="eq-hint">（無可編裝的單位）</p>
      </div>
      <div class="eq-editor">
        <div v-if="equipUnitId" class="eq-editor-in">
          <div class="eq-editor-hd">編裝 · {{ realUnits.find((u) => u.id === equipUnitId)?.designation ?? equipUnitId }}</div>
          <UnitOrbatEditor :session-id="sessionId" :unit-id="equipUnitId" :can-edit="true" />
        </div>
        <p v-else class="eq-hint">← 選一個單位以編輯其武器/裝備配發</p>
      </div>
    </div>
  </div>
</div>
</template>

<style scoped>
.equip-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
}
.equip-modal {
  width: min(760px, 94vw);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 0.5rem;
  color: #e2e8f0;
  overflow: hidden;
}
.equip-modal .eq-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.7rem 0.9rem;
  border-bottom: 1px solid #1e293b;
}
.equip-modal .eq-hd h3 {
  margin: 0;
  font-size: 1rem;
}
.equip-modal .eq-x {
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  font-size: 1rem;
}
.eq-perms {
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid #1e293b;
  background: #0a1626;
}
.eq-perms-hd {
  font-size: 0.75rem;
  color: #94a3b8;
  margin-bottom: 0.35rem;
}
.eq-perms-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.9rem;
}
.eq-perm {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.82rem;
  cursor: pointer;
}
.eq-perm .u-dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  display: inline-block;
}
.eq-body {
  display: flex;
  min-height: 0;
  flex: 1 1 auto;
}
.eq-units {
  flex: 0 0 40%;
  max-width: 16rem;
  overflow-y: auto;
  padding: 0.6rem;
  border-right: 1px solid #1e293b;
}
.eq-fac-hd {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.8rem;
  color: #cbd5e1;
  margin: 0.4rem 0 0.2rem;
}
.eq-fac-hd .u-dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  display: inline-block;
}
.eq-unit {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.3rem 0.5rem;
  margin: 0.15rem 0;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
  font-size: 0.82rem;
}
.eq-unit.sel {
  border-color: #2563eb;
  background: #172554;
}
.eq-editor {
  flex: 1 1 auto;
  min-width: 0;
  overflow-y: auto;
  padding: 0.7rem 0.9rem;
}
.eq-editor-hd {
  font-size: 0.85rem;
  color: #cbd5e1;
  margin-bottom: 0.5rem;
}
.eq-hint {
  color: #64748b;
  font-size: 0.82rem;
}
</style>
