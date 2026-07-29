<script setup lang="ts">
/**
 * 地圖右鍵選單（#3，ATAK 式移動/攻擊；WP-G1b 自 cop.vue 抽出）。
 *
 * **純呈現**：選單狀態（座標、命中的單位/圖形/控制點）與每個動作都在頁面，
 * 這裡只把它們畫成清單並把點擊轉發回去。選單的可見性由父層的 `v-if` 決定。
 */
defineProps<{
  /** 命中資訊：螢幕座標 + 地圖座標 + 可選的單位/圖形/控制點。 */
  menu: {
    x: number
    y: number
    lng: number
    lat: number
    unitId?: string
    faction?: string
    kind?: string
    featureId?: string
    vertexIndex?: number
  }
  /** 右鍵到的是本方單位（可下令移動）。 */
  isMine: boolean
  /** 右鍵到的是他軍單位（可鎖為交戰目標）。 */
  isEnemy: boolean
  unitName: string
  /** 目前有選取的我方單位——沒有就只能顯示「先選取我方單位」。 */
  hasSelection: boolean
  /** 選取單位的番號（缺番號時退回 id，與拆分前同）。 */
  selectedName: string
  /** 有繪製權（決定圖形相關項目是否出現）。 */
  canDraw: boolean
  /** 該圖形**已解鎖整形**（`canEditSelectedFeature`）——控制點刪除項只在解鎖時出現。
   * 注意這不是「有編修權」（`mayEditSelectedFeature`）：有權 ≠ 現在可拖，見 useMapEditor。 */
  canEditFeature: boolean
}>()

defineEmits<{
  (
    e:
      | 'closeCtx'
      | 'ctxArmMove'
      | 'ctxArmAttack'
      | 'ctxMoveHere'
      | 'ctxLockTarget'
      | 'ctxEditFeature'
      | 'ctxDeleteFeature'
      | 'ctxDeleteVertex',
  ): void
  (e: 'ctxRotateFeature', deg: number): void
}>()
</script>

<template>
  <div class="ctx-backdrop" @click="$emit('closeCtx')" @contextmenu.prevent="$emit('closeCtx')" />
  <div
    class="ctx-menu"
    data-testid="ctx-menu"
    :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
  >
    <!-- #99 右鍵控制點：刪點優先於一般物件選單（游標下同時有頂點與圖形本體）。 -->
    <template v-if="menu?.vertexIndex != null && canEditFeature">
      <div class="ctx-title">控制點 #{{ menu.vertexIndex + 1 }}</div>
      <button class="ctx-danger" data-testid="ctx-vertex-del" @click="$emit('ctxDeleteVertex')">
        <i class="pi pi-trash" /> 刪除控制點
      </button>
    </template>
    <template v-else-if="menu?.featureId && canDraw">
      <div class="ctx-title">地圖物件</div>
      <button data-testid="ctx-feat-edit" @click="$emit('ctxEditFeature')">
        <i class="pi pi-pencil" /> 編輯形狀 / 屬性
      </button>
      <button data-testid="ctx-feat-rot-ccw" @click="$emit('ctxRotateFeature', -15)"><i class="pi pi-undo" /> 旋轉 15°</button>
      <button data-testid="ctx-feat-rot-cw" @click="$emit('ctxRotateFeature', 15)"><i class="pi pi-refresh" /> 旋轉 15°</button>
      <button class="ctx-danger" data-testid="ctx-feat-del" @click="$emit('ctxDeleteFeature')"><i class="pi pi-trash" /> 刪除</button>
    </template>
    <template v-else-if="isMine">
      <div class="ctx-title">{{ unitName }}</div>
      <button data-testid="ctx-move" @click="$emit('ctxArmMove')"><i class="pi pi-arrow-right" /> 移動</button>
      <button data-testid="ctx-attack" @click="$emit('ctxArmAttack')"><i class="pi pi-bullseye" /> 攻擊</button>
    </template>
    <template v-else-if="isEnemy && hasSelection">
      <div class="ctx-title">目標：{{ unitName }}</div>
      <button data-testid="ctx-lock-target" @click="$emit('ctxLockTarget')">
        <i class="pi pi-bullseye" /> 以「{{ selectedName }}」攻擊
      </button>
    </template>
    <template v-else-if="hasSelection">
      <div class="ctx-title">{{ selectedName }}</div>
      <button data-testid="ctx-move-here" @click="$emit('ctxMoveHere')"><i class="pi pi-arrow-right" /> 移動至此</button>
      <button data-testid="ctx-attack" @click="$emit('ctxArmAttack')"><i class="pi pi-bullseye" /> 攻擊…</button>
    </template>
    <template v-else>
      <div class="ctx-empty">先選取我方單位</div>
    </template>
  </div>
</template>

<style scoped>
/* 右鍵選單（#3）——ATAK 式移動/攻擊。 */
.ctx-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1090; /* 壓過停靠側欄(40)/Unit 卡(45)/浮動視窗，避免右鍵選單被遮住（#3） */
}
.ctx-menu {
  position: absolute;
  z-index: 1091;
  min-width: 8rem;
  transform: translate(2px, 2px);
  display: flex;
  flex-direction: column;
  padding: 0.25rem;
  border: 1px solid #334155;
  border-radius: 0.4rem;
  background: rgba(15, 23, 42, 0.97);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
}
.ctx-title {
  padding: 0.25rem 0.5rem 0.35rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: #7dd3fc;
  border-bottom: 1px solid #1e293b;
  margin-bottom: 0.2rem;
}
.ctx-menu button {
  text-align: left;
  padding: 0.4rem 0.55rem;
  border: 0;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  font-size: 0.8rem;
  cursor: pointer;
}
.ctx-menu button.ctx-danger {
  color: #fca5a5;
}
.ctx-menu button:hover {
  background: #1d4ed8;
}
.ctx-empty {
  padding: 0.4rem 0.55rem;
  font-size: 0.75rem;
  color: #94a3b8;
}
</style>
