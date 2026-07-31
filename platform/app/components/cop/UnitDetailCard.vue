<script setup lang="ts">
/**
 * 單位詳細資訊卡（#5 / #Fix C / #42）——懸浮於選取圖標旁，可拖曳。
 *
 * 定位與拖曳的狀態機是 `useUnitCardDrag()`，整包以 `card` prop 收下（父層須傳
 * `reactive(...)`，樣板不會 unwrap 巢狀 ref）。其餘是選取單位的衍生值，由頁面算好傳入
 * ——它們各自來源不同（DB 值、STATE_DIFF 活值、編裝權限），沒有共同的 composable 可收。
 */
import { POSTURE_LABELS, commsLabel, factionColor, postureLabel, unitLevelLabel } from '~/composables/useUnits'
import { starvationModifier, supplyClassLabel } from '~/composables/useLabels'
import type { UnitView, WeaponView } from '~/composables/useOrders'
import type { SupplyLevelView } from '~/composables/useLiveState'
import type { UnwrapNestedRefs } from 'vue'
import type { useUnitCardDrag } from '~/composables/useUnitCardDrag'

defineProps<{
  card: UnwrapNestedRefs<ReturnType<typeof useUnitCardDrag>>
  unit: UnitView
  unitId: string | null
  sessionId: string
  /** 作戰效能%（活值優先）與其色帶。 */
  hpPct: number
  hpColor: string
  /** 戰力/平台/人員（缺 authorized_strength 時為 null）。 */
  force: { cur: number; auth: number; platforms: number; personnel: number | null } | null
  weapons: WeaponView[]
  /** 是否可編裝（該局開放且為本軍，或全知）。 */
  editable: boolean
  currentTick: number
  liveComms: (u: UnitView) => string
  /** 活壓制度（0–1）與姿態（WP-C1）。敵方單位一律讀到中性值——後端不供應。 */
  liveSuppression: (u: UnitView) => number
  livePosture: (u: UnitView) => string
  liveStaleTick: (u: UnitView) => number | null
  liveFuel: (unitId: string | null) => number | null
  /**
   * WP-C7 補給水位與斷補天數（活值）。**空陣列＝這個單位沒有宣告補給類別**，
   * 不是「全部見底」——沒有宣告的既有想定不該在卡片上看起來像在挨餓。
   * 敵方單位一律讀到中性值（後端不供應，紅線 3）。
   */
  liveSupply: (u: UnitView) => SupplyLevelView[]
  liveStarvedDays: (u: UnitView) => number
  liveAmmo: (w: WeaponView) => number | null
}>()

defineEmits<{ (e: 'close'): void }>()

/** 戰備狀態的中文。DOWN 是「戰鬥不能」不是「已殲滅」——真被殲滅的單位不會在清單裡。 */
const READINESS_LABELS: Record<string, string> = {
  OK: '可戰',
  DEGRADED: '戰力受損',
  DOWN: '戰鬥不能（仍在戰場）',
}

/** 編裝編輯器展開狀態——由頁面持有（換單位時要收起），故走 model。 */
const showOrbat = defineModel<boolean>('showOrbat', { required: true })

/**
 * 單位屬性編輯器展開狀態。與編裝編輯**分開**：那一個管「帶什麼裝備」，
 * 這一個管「這是一支什麼樣的部隊」（番號/兵科/級別/人數/戰力）。
 * 本地 state 即可——換單位時 `unitId` 變了、編輯器自己會 reset。
 */
const showAttrs = ref(false)
</script>

<template>
<div
  v-if="unit"
  class="unit-card"
  :class="{ 'card-anchored': !!card.unitCardPos && !card.unitCardDrag, 'card-dragged': !!card.unitCardDrag }"
  :style="card.unitCardStyle"
  data-testid="unit-detail-card"
>
  <button class="card-close" data-testid="card-close" title="關閉（取消選取）" @click="$emit('close')">
    <i class="pi pi-times" />
  </button>
  <div
    class="card-hd"
    title="拖曳可移動資訊卡"
    @mousedown="card.beginCardDrag"
    @touchstart="card.beginCardDrag"
  >
    <span class="card-grip"><i class="pi pi-bars" /></span>
    <span class="fdot" :style="{ background: factionColor(unit.faction) }" />
    <strong class="cname">{{ unit.designation }}</strong>
    <span class="clevel">{{ unitLevelLabel(unit.unit_level) }} · {{ unit.faction }}</span>
  </div>
  <!-- WP-C1 姿態徽章：MOVING 不顯示（那是預設，每張卡都掛一個「行進」只是雜訊）。 -->
  <div
    v-if="livePosture(unit) !== 'MOVING'"
    class="posture"
    :class="`posture-${livePosture(unit).toLowerCase()}`"
    :title="POSTURE_LABELS[livePosture(unit)]?.hint ?? ''"
    data-testid="unit-posture"
  >
    <i class="pi pi-shield" /> {{ postureLabel(livePosture(unit)) }}
  </div>
  <!-- WP-C7 斷補徽章。**斷補是會贏會輸的事**（Class I 見底 3 個模擬日就只剩五成效能），
       但它不像戰損有血條可看——沒有這個徽章，指揮官要到部隊打不動了才會發現。
       用倍率而不是只講天數：「斷補 3.0 日」對操作員沒有意義，「效能 ×0.5」有。 -->
  <div
    v-if="liveStarvedDays(unit) > 0"
    class="starved"
    :title="`Class I（口糧／水）已見底 ${liveStarvedDays(unit).toFixed(1)} 個模擬日。效能隨天數階梯下降，補到一次即歸零。`"
    data-testid="unit-starved"
  >
    <i class="pi pi-exclamation-triangle" />
    斷補 {{ liveStarvedDays(unit).toFixed(1) }} 日 · 效能 ×{{ starvationModifier(liveStarvedDays(unit)).toFixed(2) }}
  </div>
  <div class="hpbar" :title="`作戰效能 ${hpPct}%`">
    <div class="hpfill" :style="{ width: `${hpPct}%`, background: hpColor }" />
    <span class="hptxt">效能 {{ hpPct }}%</span>
  </div>
  <!-- WP-C1 壓制條：只在真的被壓制時出現。壓制是可逆的——停火就開始恢復，
       所以它跟效能條是兩件事，不能塞進同一條裡混淆。 -->
  <div
    v-if="liveSuppression(unit) > 0"
    class="supbar"
    :title="`壓制 ${Math.round(liveSuppression(unit) * 100)}%：射擊效能 −${Math.round(liveSuppression(unit) * 60)}%、速度 −${Math.round(liveSuppression(unit) * 50)}%（停火後每分鐘衰減）`"
    data-testid="unit-suppression"
  >
    <div class="supfill" :style="{ width: `${Math.round(liveSuppression(unit) * 100)}%` }" />
    <span class="suptxt">壓制 {{ Math.round(liveSuppression(unit) * 100) }}%</span>
  </div>
  <dl class="card-meta">
    <div v-if="force">
      <dt>戰力</dt>
      <dd>
        {{ force.cur }}/{{ force.auth }}
        <span class="dim">· {{ force.platforms }} 平台</span>
        <span v-if="force.personnel != null" class="dim">· {{ force.personnel }} 人</span>
      </dd>
    </div>
    <!-- 戰備狀態。**存在的理由很具體**：上面那個「作戰效能%」在戰力比 ≤0.30 就顯示 0，
         而那個 0 會被讀成「已殲滅」——這一列說清楚它還在戰場上，只是戰鬥不能。 -->
    <div v-if="unit.readiness">
      <dt>戰備</dt>
      <dd :class="`rdy-${unit.readiness.toLowerCase()}`" data-testid="unit-readiness">
        {{ READINESS_LABELS[unit.readiness] }}
      </dd>
    </div>
    <div>
      <dt>通聯</dt>
      <dd :class="`comms-${liveComms(unit).toLowerCase()}`" data-testid="unit-comms">
        {{ commsLabel(liveComms(unit)) }}
      </dd>
    </div>
    <div>
      <dt>座標</dt>
      <dd data-testid="unit-coords">
        {{ (unit.lat ?? 0).toFixed(4) }}, {{ (unit.lng ?? 0).toFixed(4) }}
        <!-- WP-C5：斷聯單位的座標是最後一次回報，不是現在的位置。不標的話指揮官
             會拿一個過時的點下令，還以為是即時的。 -->
        <span v-if="liveStaleTick(unit) != null" class="stale">
          · T{{ liveStaleTick(unit) }} 最後回報（已失聯
          {{ Math.max(0, currentTick - (liveStaleTick(unit) ?? 0)) }}t）
        </span>
      </dd>
    </div>
    <!-- WP-C7 補給水位。**沒有宣告補給類別的單位整列不出現**——空清單是「沒有這本帳」，
         印一排 0% 會讓既有想定的每支部隊看起來都在斷補。
         低於再訂購水位（30%）標紅：那是 auto_resupply 會去拉貨的線，也是指揮官該有動作的線。 -->
    <div v-if="liveSupply(unit).length" data-testid="unit-supply">
      <dt>補給</dt>
      <dd class="supply-list">
        <span
          v-for="s in liveSupply(unit)"
          :key="s.supply_class"
          :class="{ lowsupply: s.fraction < 0.3 }"
          :title="`${s.supply_class}·${supplyClassLabel(s.supply_class)}：${s.on_hand.toFixed(0)} / ${s.capacity.toFixed(0)}`"
        >
          {{ supplyClassLabel(s.supply_class) }} {{ Math.round(s.fraction * 100) }}%
        </span>
      </dd>
    </div>
    <div v-if="liveFuel(unitId) != null" data-testid="unit-fuel">
      <dt>油料</dt>
      <dd :class="{ lowfuel: (liveFuel(unitId) ?? 0) <= 0 }">
        {{ (liveFuel(unitId) ?? 0).toFixed(0) }}
        <span v-if="(liveFuel(unitId) ?? 0) <= 0" class="dim">· 油料耗盡（待補給）</span>
      </dd>
    </div>
  </dl>
  <div v-if="weapons.length && !showOrbat" class="card-weapons">
    <div class="card-sub">武器裝載</div>
    <ul>
      <li v-for="w in weapons" :key="w.id">
        {{ w.name }}
        <span v-if="w.max_range_m" class="dim">· {{ (w.max_range_m / 1000).toFixed(1) }} km</span>
        <span v-if="liveAmmo(w) != null" class="dim">· 彈 {{ liveAmmo(w) }}</span>
      </li>
    </ul>
  </div>
  <div v-if="editable" class="card-orbat">
    <button class="orbat-toggle" data-testid="toggle-attrs" @click="showAttrs = !showAttrs">
      {{ showAttrs ? '▾ 單位屬性' : '▸ 單位屬性（番號/兵科/人數/戰力）' }}
    </button>
    <UnitAttributeEditor
      v-if="showAttrs && unitId"
      :session-id="sessionId"
      :unit-id="unitId"
      :designation="unit.designation"
      :branch="unit.branch ?? 'UNKNOWN'"
      :unit-level="unit.unit_level"
      :personnel="force?.personnel ?? null"
      :strength="force?.cur ?? unit.strength ?? 0"
      :authorized-strength="force?.auth ?? unit.authorized_strength ?? 100"
    />
    <button class="orbat-toggle" data-testid="toggle-orbat" @click="showOrbat = !showOrbat">
      {{ showOrbat ? '▾ 編裝編輯' : '▸ 編裝編輯（武器/彈藥）' }}
    </button>
    <UnitOrbatEditor
      v-if="showOrbat"
      :session-id="sessionId"
      :unit-id="unitId ?? ''"
      :can-edit="true"
    />
  </div>
</div>
</template>

<style scoped>
/* WP-C1 姿態徽章與壓制條。壓制用橘紅（與效能條的綠/黃/紅有別，避免誤讀成戰損）。 */
.unit-card .posture {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  margin-top: 0.35rem;
  padding: 0.05rem 0.4rem;
  border-radius: 0.75rem;
  font-size: 0.68rem;
  border: 1px solid currentcolor;
}
.unit-card .posture-hasty {
  color: #7dd3fc;
}
.unit-card .posture-defense {
  color: #4ade80;
}
.unit-card .posture-dug_in {
  color: #34d399;
}
/* WP-C7 斷補徽章：用紅色而非姿態徽章的藍綠——這是「必須有動作」而不是「狀態如此」。 */
.unit-card .starved {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  margin-top: 0.35rem;
  padding: 0.05rem 0.4rem;
  border-radius: 0.75rem;
  font-size: 0.68rem;
  color: #fca5a5;
  border: 1px solid currentcolor;
  background: rgba(127, 29, 29, 0.35);
}
.unit-card .supply-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.1rem 0.5rem;
}
.unit-card .supply-list .lowsupply {
  color: #f87171; /* 低於再訂購水位（30%）＝ auto_resupply 會去拉貨的線 */
}
.unit-card .supbar {
  position: relative;
  height: 0.85rem;
  margin: 0.35rem 0 0.1rem;
  border-radius: 0.25rem;
  background: #1e293b;
  overflow: hidden;
}
.unit-card .supfill {
  height: 100%;
  background: repeating-linear-gradient(45deg, #f97316, #f97316 4px, #c2410c 4px, #c2410c 8px);
  transition: width 0.3s ease;
}
.unit-card .suptxt {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.62rem;
  font-weight: 600;
  color: #fff7ed;
  text-shadow: 0 0 3px rgba(0, 0, 0, 0.8);
}
/* WP-C5 位置凍結註記：與 .dim 同層級，但用琥珀色點出「這不是即時值」。 */
.stale {
  color: #fbbf24;
  font-size: 0.8125rem;
}
.unit-card {
  position: fixed;
  z-index: 45;
  width: 19rem;
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  padding: 0.75rem 0.875rem;
  border-radius: 0.5rem;
  border: 1px solid #1e3a5f;
  background: rgba(15, 23, 42, 0.96);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  font-size: 0.78rem;
  color: #e2e8f0;
}
/* 錨定時附一條指向圖標的小尾巴（左側）。 */
.unit-card.card-anchored::before {
  content: '';
  position: absolute;
  left: -6px;
  top: 14px;
  width: 10px;
  height: 10px;
  background: rgba(15, 23, 42, 0.96);
  border-left: 1px solid #1e3a5f;
  border-bottom: 1px solid #1e3a5f;
  transform: rotate(45deg);
}
.unit-card .card-close {
  position: absolute;
  top: 0.375rem;
  right: 0.375rem;
  padding: 0 0.3rem;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 0.9rem;
}
.unit-card .card-close:hover {
  color: #e2e8f0;
}
.unit-card .card-hd {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding-right: 1rem;
  cursor: move;
  user-select: none;
  touch-action: none;
}
.unit-card .card-grip {
  color: #475569;
  font-size: 0.7rem;
  flex: none;
  margin-right: -0.1rem;
}
/* 拖曳後脫離錨定 → 不顯示指向圖標的小尾巴。 */
.unit-card.card-dragged::before {
  display: none;
}
.unit-card .fdot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
  flex: none;
}
.unit-card .cname {
  color: #f8fafc;
}
.unit-card .clevel {
  color: #94a3b8;
  font-size: 0.68rem;
}
.unit-card .hpbar {
  position: relative;
  height: 1.05rem;
  margin: 0.5rem 0 0.4rem;
  border-radius: 0.25rem;
  background: #1e293b;
  overflow: hidden;
}
.unit-card .hpfill {
  height: 100%;
  transition: width 0.3s ease;
}
.unit-card .hptxt {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.66rem;
  font-weight: 600;
  color: #0a1626;
  text-shadow: 0 0 2px rgba(255, 255, 255, 0.4);
}
.unit-card .card-meta {
  margin: 0.25rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.unit-card .card-meta > div {
  display: flex;
  gap: 0.5rem;
}
.unit-card .card-meta dt {
  color: #64748b;
  min-width: 2.5rem;
}
.unit-card .lowfuel {
  color: #f87171;
}
.unit-card .card-meta dd {
  margin: 0;
  color: #cbd5e1;
  font-family: monospace;
}
/* #33 通聯狀態色：上線綠 / 降級黃 / 離線紅 */
.unit-card .card-meta dd.comms-online {
  color: #4ade80;
}
.unit-card .card-meta dd.comms-degraded {
  color: #facc15;
}
.unit-card .card-meta dd.comms-offline {
  color: #f87171;
}
.unit-card .card-sub {
  margin: 0.5rem 0 0.2rem;
  color: #64748b;
  font-size: 0.68rem;
}
.unit-card .card-weapons ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.unit-card .card-weapons .dim {
  color: #94a3b8;
  font-size: 0.7rem;
}
.unit-card .card-orbat {
  margin-top: 0.5rem;
  border-top: 1px solid #1e293b;
  padding-top: 0.4rem;
}
.unit-card .orbat-toggle {
  border: none;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
  font-size: 0.72rem;
  padding: 0 0 0.3rem;
}
.unit-card .orbat-toggle:hover {
  color: #bae6fd;
}
.rdy-ok {
  color: var(--p-green-400);
}
.rdy-degraded {
  color: var(--p-amber-400);
}
.rdy-down {
  color: var(--p-red-400);
  font-weight: 600;
}
</style>
