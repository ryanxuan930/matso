<script setup lang="ts">
/**
 * COP 頂列（WP-G1b 自 cop.vue 抽出）——session 識別、單位數、通聯姿態告示、模擬時鐘、
 * 視角切換、導覽鈕與工具視窗開關選單。
 *
 * 只做呈現與轉發：所有動作（回大廳、進地圖狀態編輯、開裝備管理）都以 emit 交回頁面，
 * 視角與工具視窗開關則是雙向（`v-model:viewpoint` / `widgets` 狀態束）。
 */
import { commsLabel } from '~/composables/useUnits'
import { WIDGET_DEFS, type WidgetId, type WStat } from '~/composables/useCopWidgets'

defineProps<{
  sessionId: string
  unitCount: number
  /** 觀測陣營的整體通聯姿態（WP-C5）；null 或 ONLINE 時不顯示告示。 */
  commsPosture: string | null
  /** 目前 sim tick（給時鐘列）。 */
  tick: number | null
  /**
   * WS 串流狀態（idle/connecting/live/resyncing/closed）。
   *
   * **這個指示器在 WP-G1 拆分時被弄丟了**——store 一直有 `status`，但沒有任何地方顯示它，
   * 於是 smoke e2e 那條 `ws-status` 斷言從此不可能成立（找不到元素、textContent 永遠 null）。
   * 它本身也是操作員真正需要的資訊：畫面沒動，是戰場安靜還是我斷線了？
   */
  streamStatus: string
  startTime: string | null
  /** 全知（統裁/白軍/管理）——視角切換與部分導覽鈕僅其可見。 */
  canControl: boolean
  canDraw: boolean
  canManageEquip: boolean
  sessionFactions: string[]
  mapEditMode: boolean
  /** 浮動視窗狀態（開關選單直接切它）。 */
  widgets: Record<WidgetId, WStat>
  toggleWidget: (id: WidgetId) => void
}>()

defineEmits<{
  (e: 'back' | 'enter-map-edit' | 'open-equip-mgr'): void
}>()

const viewpoint = defineModel<string>('viewpoint', { required: true })
const widgetMenuOpen = defineModel<boolean>('widgetMenuOpen', { required: true })
</script>

<template>
<header class="cop-bar">
  <button data-testid="back-lobby" @click="$emit('back')">← 系統首頁</button>
  <span class="sid" data-testid="cop-session">Session {{ sessionId }}</span>
  <span class="count" data-testid="unit-count">單位 {{ unitCount }}</span>
  <!-- WP-C5：本軍通聯不良 → 敵情圖已在**後端**粗化（位置跳到 3km 格心、只剩 DETECTED）。
       不標的話操作者只會覺得「敵情圖怎麼突然變爛了」而不知道原因。 -->
  <span
    v-if="commsPosture && commsPosture !== 'ONLINE'"
    class="posture"
    data-testid="comms-posture"
    :title="
      commsPosture === 'OFFLINE'
        ? '本軍通聯全斷：敵情圖停止更新並已粗化到約 3km 格'
        : '本軍通聯不良：敵情位置已粗化到約 3km 格、情報等級降為 DETECTED'
    "
  >
    <i class="pi pi-wifi" /> 敵情粗化（{{ commsLabel(commsPosture) }}）
  </span>
  <span
    class="ws"
    :class="`ws-${streamStatus}`"
    data-testid="ws-status"
    :title="
      streamStatus === 'live'
        ? '即時串流連線中：單位位置與戰況事件即時更新'
        : '即時串流未連線：畫面可能停在最後一次收到的狀態'
    "
  >
    <i class="pi" :class="streamStatus === 'live' ? 'pi-circle-fill' : 'pi-circle'" />
    {{ streamStatus }}
  </span>
  <ClientOnly><SimClockBar :tick="tick" :start-time="startTime" /></ClientOnly>
  <nav class="cop-nav">
    <!-- 視角切換（#90）：僅全知角色。選陣營＝以該陣營之眼觀戰（後端套其戰場迷霧）。 -->
    <label v-if="canControl" class="vp" :class="{ 'vp-on': !!viewpoint }">
      <i class="pi pi-eye" />
      <select
        v-model="viewpoint"
        data-testid="viewpoint"
        :title="
          viewpoint
            ? `目前以 ${viewpoint} 視角觀戰：只看得到該陣營看得到的（含其偵測到的敵情）`
            : '全局視角（全知）：看得到所有陣營的單位'
        "
      >
        <option value="">全局視角（全知）</option>
        <option v-for="f in sessionFactions" :key="f" :value="f">{{ f }} 視角</option>
      </select>
    </label>
    <!-- 導覽鈕一律只留 icon（頂列空間留給地圖）；名稱與說明走 data-tip 的 hover 提示。
         不用原生 title：延遲約 1 秒且樣式不受控，icon-only 之下等於沒有提示。 -->
    <button
      v-if="canControl && !mapEditMode"
      class="icon-btn"
      data-testid="nav-map-edit"
      data-tip="地圖狀態編輯"
      data-tip2="暫停推演、拖放單位、繪障礙，完成再開始"
      aria-label="地圖狀態編輯"
      @click="$emit('enter-map-edit')"
    >
      <i class="pi pi-pencil" />
    </button>
    <button
      v-if="canControl"
      class="icon-btn"
      data-testid="nav-white-cell"
      data-tip="白軍控制台"
      data-tip2="時間控制、注入事件、視角"
      aria-label="白軍控制台"
      @click="navigateTo(`/session/${sessionId}/white-cell`)"
    >
      <i class="pi pi-cog" />
    </button>
    <button
      v-if="canManageEquip"
      class="icon-btn"
      data-testid="nav-equip-mgr"
      data-tip="裝備管理"
      data-tip2="編輯各單位配發的武器/裝備（白軍編任一；本軍需該局開放自編）"
      aria-label="裝備管理"
      @click="$emit('open-equip-mgr')"
    >
      <i class="pi pi-box" />
    </button>
    <div class="widget-menu">
      <button
        class="icon-btn"
        data-testid="nav-widgets"
        :class="{ on: widgetMenuOpen }"
        data-tip="工具"
        data-tip2="開啟/關閉小工具視窗"
        aria-label="工具視窗"
        @click="widgetMenuOpen = !widgetMenuOpen"
      >
        <i class="pi pi-th-large" />
      </button>
      <template v-if="widgetMenuOpen">
        <div class="wm-backdrop" @click="widgetMenuOpen = false" />
        <div class="wm-pop" data-testid="widget-menu">
          <div class="wm-hd">工具視窗</div>
          <label
            v-for="d in WIDGET_DEFS"
            v-show="d.id !== 'mapedit' || canDraw"
            :key="d.id"
            :class="{ off: !widgets[d.id].open }"
            :data-testid="`widget-toggle-${d.id}`"
          >
            <input type="checkbox" :checked="widgets[d.id].open" @change="toggleWidget(d.id)">
            {{ d.label }}
          </label>
        </div>
      </template>
    </div>
    <button
      v-if="canControl"
      class="icon-btn"
      data-testid="nav-autonomy"
      data-tip="自主推演"
      data-tip2="指派 AI 控制陣營"
      aria-label="自主推演"
      @click="navigateTo(`/session/${sessionId}/autonomy`)"
    >
      <i class="pi pi-bolt" />
    </button>
    <button
      class="icon-btn"
      data-testid="nav-aar"
      data-tip="AAR"
      data-tip2="戰後檢討報告"
      aria-label="AAR 戰後檢討"
      @click="navigateTo(`/session/${sessionId}/aar`)"
    >
      <i class="pi pi-chart-bar" />
    </button>
  </nav>
</header>
</template>

<style scoped>
.cop-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: #0f172a;
  border-bottom: 1px solid #1e293b;
  position: relative;
  z-index: 1000; /* 頂列 + 工具選單永遠壓過浮動視窗 */
}
/* #12 工具視窗開關選單 */
.widget-menu {
  position: relative;
}
.wm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
}
.wm-pop {
  position: absolute;
  top: 110%;
  left: 0;
  z-index: 1001;
  min-width: 9rem;
  padding: 0.35rem;
  background: #0f1b2e;
  border: 1px solid #24344a;
  border-radius: 0.4rem;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.wm-hd {
  font-size: 0.68rem;
  color: #64748b;
  padding: 0.1rem 0.3rem 0.25rem;
}
.wm-pop label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.25rem 0.35rem;
  border-radius: 0.25rem;
  font-size: 0.78rem;
  color: #e2e8f0;
  cursor: pointer;
}
.wm-pop label:hover {
  background: #1e293b;
}
.wm-pop label.off {
  color: #64748b;
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
/* WP-C5 敵情粗化告示：琥珀色（警示但非錯誤——是戰場現實，不是系統故障）。 */
.ws {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.68rem;
  color: #64748b;
  font-variant-numeric: tabular-nums;
}
.ws.ws-live {
  color: #4ade80;
}
.ws.ws-closed {
  color: #f87171;
}
.ws i {
  font-size: 0.5rem;
}
.posture {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.1rem 0.5rem;
  border: 1px solid #b45309;
  border-radius: 0.25rem;
  font-size: 0.8125rem;
  color: #fbbf24;
  cursor: help;
}
.cop-nav {
  margin-left: auto;
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.cop-nav button {
  padding: 0.25rem 0.75rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  cursor: pointer;
}
/* 視角切換（#90）：套了陣營視角時整顆變琥珀，提醒「你現在不是全知」。 */
.vp {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.15rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  color: #94a3b8;
}
.vp select {
  background: transparent;
  border: 0;
  color: #e2e8f0;
  font-size: 0.85rem;
  cursor: pointer;
}
.vp select option {
  background: #0f172a;
}
.vp-on {
  border-color: #f59e0b;
  color: #f59e0b;
}
.vp-on select {
  color: #f59e0b;
  font-weight: 600;
}
.cop-nav button:hover {
  border-color: #2563eb;
}
/* 只留 icon 的導覽鈕：正方形、字級放大到讀得出圖形 */
.cop-nav .icon-btn {
  position: relative; /* hover 提示以此為定位基準 */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.1rem;
  height: 2.1rem;
  padding: 0;
  font-size: 1rem;
  line-height: 1;
}
/* hover 提示：名稱（data-tip）+ 選填的補充說明（data-tip2）。
   自繪而非原生 title——原生的約 1 秒才出現，icon-only 之下等於沒有提示。 */
.cop-nav .icon-btn::after {
  content: attr(data-tip);
  position: absolute;
  top: calc(100% + 0.45rem);
  /* 靠按鈕右緣、往左長：導覽列本身靠右，置中對齊會讓最右邊幾顆的提示被視窗切掉。 */
  right: 0;
  z-index: 1002; /* 壓過工具選單彈出層（1001） */
  max-width: 15rem;
  width: max-content;
  padding: 0.3rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.3rem;
  background: #0b1324;
  color: #e2e8f0;
  font-size: 0.75rem;
  line-height: 1.35;
  text-align: left;
  white-space: pre-line; /* 名稱與補充說明以 \A 分行（見下方 [data-tip2] 規則） */
  box-shadow: 0 6px 16px rgb(0 0 0 / 45%);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s ease;
}
/* 有補充說明者：名稱一行、說明一行 */
.cop-nav .icon-btn[data-tip2]::after {
  content: attr(data-tip) '\A' attr(data-tip2);
}
.cop-nav .icon-btn:hover::after,
.cop-nav .icon-btn:focus-visible::after {
  opacity: 1;
}
.cop-nav button.on {
  border-color: #eab308;
  color: #fde68a;
}
.cop-nav .help {
  font-size: 0.8125rem;
  color: #60a5fa;
  text-decoration: none;
}
.cop-nav .help:hover {
  text-decoration: underline;
}
</style>
