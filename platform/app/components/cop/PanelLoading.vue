<script setup lang="ts">
/**
 * 面板載入指示（旋轉圈 + 「載入中」）。
 *
 * COP 的 `onMounted` 不 await `refresh()`（要讓地圖先畫出來），所以首屏一定會有一段
 * 「資料還沒到」的空窗。過去那段時間各面板顯示的是**空狀態文字**
 * （「此 session 無可下令單位」「無指令」「尚無標註」），使用者會以為這局是空的。
 * 空狀態要留給「真的沒有」，載入中要有自己的樣子。
 */
defineProps<{ label?: string }>()
</script>

<template>
  <div class="ploading" data-testid="panel-loading">
    <span class="spin" aria-hidden="true" />
    <span>{{ label ?? '載入中…' }}</span>
  </div>
</template>

<style scoped>
.ploading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 8px;
  color: var(--p-text-muted-color);
  font-size: 12px;
}
.spin {
  width: 13px;
  height: 13px;
  flex: none;
  border: 2px solid color-mix(in srgb, currentColor 30%, transparent);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: ploading-spin 0.7s linear infinite;
}
@keyframes ploading-spin {
  to {
    transform: rotate(360deg);
  }
}
/* 使用者要求減少動態效果時不轉（無障礙）。 */
@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
</style>
