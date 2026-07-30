<script setup lang="ts">
/**
 * 地圖狀態編輯提示條——推演暫停、可拖曳單位調位置時顯示，並提供「開始兵推」出口。
 */
defineProps<{ selectedUnitCount: number }>()
defineEmits<{ (e: 'start'): void }>()
</script>

<template>
<div class="mapedit-bar" data-testid="mapedit-bar">
  <i class="pi pi-pencil" />
  <span v-if="selectedUnitCount" class="meb-badge" data-testid="selected-count">已選 {{ selectedUnitCount }} 個</span>
  <span class="meb-txt">
    <strong>地圖狀態編輯（推演已暫停）</strong>——拖曳單位調整位置；<b>Shift＋點單位</b>可多選、<b>Shift＋空白處拖曳</b>可框選，再拖曳任一選取單位即整組移動；用「地圖編輯」工具繪障礙/建築。
  </span>
  <button class="meb-start" data-testid="start-wargame" @click="$emit('start')">
    ▶ 開始推演
  </button>
</div>
</template>

<style scoped>
.mapedit-bar {
  /* 置中浮動藥丸：避免被左右浮動工具視窗（z 15+）遮住兩端與「開始兵推」鈕。 */
  position: fixed;
  top: 64px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 50;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  max-width: min(760px, 94vw);
  padding: 0.45rem 0.55rem 0.45rem 0.9rem;
  background: rgba(69, 51, 8, 0.97);
  border: 1px solid rgba(251, 191, 36, 0.6);
  border-radius: 0.55rem;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
  color: #fde68a;
  font-size: 0.85rem;
}
.mapedit-bar .meb-txt {
  flex: 1 1 auto;
}
.mapedit-bar .meb-badge {
  flex: 0 0 auto;
  background: #0e7490;
  color: #cffafe;
  border: 1px solid #22d3ee;
  border-radius: 999px;
  padding: 0.1rem 0.55rem;
  font-size: 0.78rem;
  font-weight: 700;
  white-space: nowrap;
}
.mapedit-bar .meb-start {
  flex: 0 0 auto;
  background: #16a34a;
  border: none;
  color: #fff;
  border-radius: 0.35rem;
  padding: 0.4rem 0.95rem;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 600;
  white-space: nowrap;
}
.mapedit-bar .meb-start:hover {
  background: #15803d;
}
</style>
