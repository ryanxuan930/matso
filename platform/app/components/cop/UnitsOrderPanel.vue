<script setup lang="ts">
/**
 * 「單位 / 下令」小工具的內容（WP-G1 自 cop.vue 抽出）——陣營分組的單位清單 + 下令面板。
 *
 * 下令狀態同 `MapEditorPanel` 的作法：把 `reactive(useCopOrdering(...))` **整包**收成一個
 * prop。樣板不會 unwrap 巢狀 ref，故父層務必傳 `reactive(...)` 而非 composable 的原始回傳值。
 */
import { computed } from 'vue'
import { POSTURE_LABELS, factionColor, healthColor } from '~/composables/useUnits'
import type { UnitView } from '~/composables/useOrders'
import type { UnwrapNestedRefs } from 'vue'
import type { useCopOrdering } from '~/composables/useCopOrdering'

const props = defineProps<{
  ordering: UnwrapNestedRefs<ReturnType<typeof useCopOrdering>>
  /** 依陣營分組的單位（含戰力彙總）。 */
  unitsByFaction: { faction: string; units: UnitView[]; power: { pct: number; mass: number; ko: number } }[]
  unitCount: number
  selectedId: string | null
  selectedUnit: UnitView | null
  selectedUnitFixed: boolean
  collapsedFactions: Set<string>
  engageTargets: UnitView[]
  targetUnit: UnitView | null
  /** WP-C9：本局是否允許誤傷裁決（後端 `allow_fratricide`）。 */
  allowFratricide: boolean
  /** 觀測者對某陣營是否為友軍/盟軍——決定要不要要求誤傷確認。 */
  isFriendly: (faction?: string | null) => boolean
  /** 該單位是否在本帳號的指揮範圍內（roster 的 unit_scope）。 */
  inScope: (u: UnitView) => boolean
  /** 首次載入尚未完成——顯示載入中而不是空狀態（空狀態要留給「真的沒有」）。 */
  loading?: boolean
  /** 活血量（STATE_DIFF 優先）。 */
  liveHealth: (u: UnitView) => number | undefined
}>()

defineEmits<{ (e: 'select' | 'toggle-group', value: string): void }>()

/** 精確移動是持久化偏好（`useCopPrefs`），故以 model 雙向綁回頁面。 */
const preciseMove = defineModel<boolean>('preciseMove', { required: true })

const SUBMIT_LABELS: Record<string, string> = {
  MOVE: '送出移動',
  ENGAGE: '送出交戰',
  FIRE_MISSION: '送出火力任務',
  POSTURE: '送出姿態令',
  MISSION: '下達任務',
  FORMATION: '送出隊形/乘駐車令',
  ENGINEER: '送出障礙作業令',
}

/**
 * WP-A2 任務型。**下的是任務不是動作**——系統的分解器會把它持續展開成移動、接敵、
 * 佔領、構工並執行到完成，指揮官不必每回合重下。
 */
const MISSION_OPTS = [
  { value: 'SEIZE', label: '奪佔', hint: '沿軸線機動 → 對目標區內敵接戰 → 佔領後轉守' },
  { value: 'DEFEND', label: '防守', hint: '進入防區 → 構工 → 對進入之敵接戰' },
  { value: 'SCREEN', label: '掩護幕', hint: '沿線佔位 → 偵測回報，不接戰' },
  { value: 'MOVE_MARCH', label: '行軍', hint: '依序通過航路點' },
] as const

/** WP-C1 姿態選項。順序＝防護由弱到強，也剛好是耗時由短到長。 */
const POSTURE_OPTS = ['MOVING', 'HASTY', 'DEFENSE', 'DUG_IN'] as const

/** 每種令型各自的最低必要條件。前端只是 UX 早退——後端 validator 才是權威閘門。 */
const canSubmit = computed(() => {
  const o = props.ordering
  if (o.orderType === 'MOVE') return !!o.destH3
  if (o.orderType === 'FIRE_MISSION') return !!o.firePoint && o.fireRounds >= 1
  if (o.orderType === 'POSTURE') return !!o.posture
  if (o.orderType === 'MISSION') {
    // 幾何收齊了才送得出去。SEIZE/DEFEND 要主目標；SCREEN/MOVE_MARCH 要至少一個點。
    return o.missionNeedsPoint ? !!o.missionPoint : o.missionPath.length > 0
  }
  // WP-C3：**兩者至少指定一項**（後端 payload 的 model_validator 也擋，這裡先擋住送出）。
  if (o.orderType === 'FORMATION') return !!o.formation || !!o.mounted
  // WP-C2：BREACH 要標的、EMPLACE 要落點。
  if (o.orderType === 'ENGINEER') {
    return o.engineerAction === 'BREACH' ? !!o.engineerFeatureId : !!o.engineerPoint
  }
  if (!o.targetUnitId) return false
  // WP-C9：目標是友軍 → **一定要勾確認**。後端 `allow_fratricide` 只是「不擋」，
  // 誤傷是要寫進 AAR 的事，得有一個刻意的動作。
  return !fratricideTarget.value || o.fratricideAck
})

/** 目前鎖定的目標是不是友軍/盟軍（＝這一發會是誤傷）。 */
const fratricideTarget = computed(
  () => !!props.targetUnit && props.isFriendly(props.targetUnit.faction),
)
</script>

<template>
<!-- eslint-disable vue/no-mutating-props -- `ordering` 是共享的可變狀態束（reactive 包起來的
     useCopOrdering 回傳值），寫入本來就要回寫父層同一份 ref；理由同 MapEditorPanel。 -->
<div class="wsec-hd">單位（{{ unitCount }}）</div>
<div class="units" data-testid="unit-list">
  <div
    v-for="g in unitsByFaction"
    :key="g.faction"
    class="ufac"
    data-testid="unit-faction-group"
  >
    <button
      class="ufac-hd"
      data-testid="unit-faction-head"
      :title="`${g.faction}：${g.units.length} 單位、戰力 ${Math.round(g.power.pct)}%`
        + `（各單位效能以量體加權平均，總量體 ${Math.round(g.power.mass)}）`
        + (g.power.ko ? `；已折損 ${g.power.ko} 個單位` : '')
        + ' — 點擊收合/展開'"
      @click="$emit('toggle-group', g.faction)"
    >
      <i class="pi" :class="collapsedFactions.has(g.faction) ? 'pi-chevron-right' : 'pi-chevron-down'" />
      <span class="u-dot" :style="{ background: factionColor(g.faction) }" />
      <b>{{ g.faction }}</b>
      <span class="ufac-count">· {{ g.units.length }}</span>
      <span
        class="ufac-pow"
        :style="{ color: healthColor(Math.round(g.power.pct)) }"
        data-testid="unit-faction-power"
      >{{ Math.round(g.power.pct) }}%</span>
      <span v-if="g.power.ko" class="ufac-ko">✖{{ g.power.ko }}</span>
    </button>
    <ul v-show="!collapsedFactions.has(g.faction)" class="ufac-units">
      <li
        v-for="u in g.units"
        :key="u.id"
        :class="{ sel: u.id === selectedId, 'out-scope': !inScope(u) }"
        :title="inScope(u) ? '' : '不在你的指揮範圍（此帳號僅獲授權指揮部分單位）'"
        data-testid="unit-item"
        @click="inScope(u) ? $emit('select', u.id) : null"
      >
        {{ u.designation }} ·
        <span class="u-hp" :style="{ color: healthColor(Math.round(liveHealth(u) ?? 100)) }">
          {{ Math.round(liveHealth(u) ?? 100) }}%
        </span>
        <span v-if="u.is_fixed" class="u-fixed" title="固定單位（指揮部等）：不可移動">🔒</span>
        <span v-if="!inScope(u)" class="u-ban" title="不在指揮範圍">🚫</span>
        <span v-if="(liveHealth(u) ?? 100) <= 0" class="u-ko">✖ 摧毀</span>
      </li>
    </ul>
  </div>
  <PanelLoading v-if="loading" />
  <div v-else-if="!unitCount" class="empty">（此 session 無可下令單位）</div>
</div>

<div v-if="selectedId" class="order" data-testid="order-panel">
  <h3>下令 · <span class="selunit" data-testid="selected-unit">{{ selectedUnit?.designation ?? selectedId }}</span></h3>
  <select v-model="ordering.orderType" data-testid="order-type">
    <option value="MOVE">移動</option>
    <option value="ENGAGE">交戰</option>
    <option value="FIRE_MISSION">火力任務（打座標）</option>
    <option value="POSTURE">姿態（掘壕/防禦）</option>
    <option value="MISSION">任務（奪佔/防守/掩護/行軍）</option>
    <option value="FORMATION">隊形 / 乘駐車</option>
    <option value="ENGINEER">障礙作業（破障/設障）</option>
  </select>
  <p v-if="selectedUnitFixed" class="fixed-note" data-testid="fixed-note">
    🔒 固定單位（指揮部等）——不可下移動令；此單位不會被派去移動或機動交戰（可於劇本編輯器調整）。
  </p>
  <template v-if="ordering.orderType === 'MOVE' && !selectedUnitFixed">
    <label class="precise">
      <input v-model="preciseMove" type="checkbox" data-testid="precise-move">
      精確移動（走到點擊處，不吸附六角格心）
    </label>
    <div class="movebtns">
      <button
        data-testid="pick-dest"
        :class="{ armed: ordering.targeting }"
        @click="ordering.waypointMode = false; ordering.targeting = true"
      >
        {{ ordering.targeting ? '點地圖設目標…' : '設定目標點' }}
      </button>
      <button
        data-testid="pick-waypoints"
        :class="{ armed: ordering.waypointMode }"
        title="逐點點擊地圖建立自訂路徑"
        @click="ordering.targeting = false; ordering.waypointMode = !ordering.waypointMode"
      >
        {{ ordering.waypointMode ? `加點中…（${ordering.moveWaypoints.length}）` : '自訂路徑' }}
      </button>
      <button
        v-if="ordering.moveWaypoints.length"
        data-testid="undo-waypoint"
        title="移除最後一個路徑點"
        @click="ordering.undoWaypoint"
      >
        <i class="pi pi-undo" /> 退一點
      </button>
      <button
        v-if="ordering.destH3 || ordering.moveWaypoints.length"
        data-testid="clear-path"
        title="清除路徑"
        @click="ordering.clearMovePath"
      >
        <i class="pi pi-times" /> 清除
      </button>
    </div>
    <div class="dest" data-testid="dest-h3">
      {{ ordering.destH3 || '未設目標' }}
      <span v-if="ordering.destH3 && !preciseMove && !ordering.moveWaypoints.length" class="snaphint">· 吸附至六角格心（大範圍省算；近距會跑回格心）</span>
      <span v-if="ordering.destH3 && preciseMove && !ordering.moveWaypoints.length" class="snaphint precise">· 精確落點（單位走到黃色標記；近距作戰建議）</span>
      <span v-if="ordering.moveWaypoints.length" class="snaphint precise">· 自訂路徑 {{ ordering.moveWaypoints.length }} 點</span>
    </div>
    <!-- #28 路徑成本試算 -->
    <div v-if="ordering.movePreview" class="mvprev" data-testid="move-preview">
      <div class="mv-row">
        <span>距離 <b>{{ (ordering.movePreview.distance_m / 1000).toFixed(2) }} km</b></span>
        <span>約 <b>{{ ordering.movePreview.duration_ticks }}</b> tick</span>
        <span v-if="ordering.movePreview.fuel_cost > 0">油耗 <b>{{ ordering.movePreview.fuel_cost.toFixed(0) }}</b></span>
      </div>
      <!-- #80/#81：機動能力 + 實際速度（已含地形/坡度調變） -->
      <div class="mv-row mv-sub">
        <span :title="`機動 profile（由編裝導出）：${ordering.movePreview.mobility_profile}`">
          <i class="pi pi-forward" /> {{ mobilityLabel(ordering.movePreview.mobility_profile) }}
          <b>{{ ordering.movePreview.speed_kmh.toFixed(1) }}</b> km/h
        </span>
        <span v-if="ordering.movePreview.terrain_routed" class="mv-routed" title="已依地形 A* 繞開不可通行區（非直線）">
          <i class="pi pi-share-alt" /> 地形繞路
        </span>
      </div>
      <!-- #84：油料是否夠走完全程 -->
      <div v-if="ordering.movePreview.fuel_remaining > 0" class="mv-row mv-sub">
        <span :class="{ 'mv-lowfuel': !ordering.movePreview.fuel_sufficient }">
          <i class="pi pi-bolt" /> 油料 <b>{{ ordering.movePreview.fuel_remaining.toFixed(0) }}</b>
          <template v-if="!ordering.movePreview.fuel_sufficient">（不足，將中途拋錨）</template>
        </span>
      </div>
      <div v-if="ordering.movePreview.terrain_impassable" class="mv-forced" data-testid="move-impassable">
        ⛔ 路徑穿越此單位<b>無法通行</b>的地形（{{ mobilityLabel(ordering.movePreview.mobility_profile) }}）——將於邊界停止
      </div>
      <div v-else-if="ordering.movePreview.feasible" class="mv-ok" title="此預覽僅檢查路徑上的已知障礙與地形；地形可達性於送出時再驗證">
        ✓ 無障礙阻擋（地形可達性於送出時驗證）
      </div>
      <div v-else class="mv-forced" data-testid="move-forced">
        ⚠ 需強穿 {{ ordering.movePreview.crossings.length }} 處阻礙（隨機額外耗損）
        <ul>
          <li v-for="(c, i) in ordering.movePreview.crossings" :key="i">
            {{ crossKindLabel(c.kind) }}{{ c.label ? `（${c.label}）` : '' }}
          </li>
        </ul>
      </div>
    </div>
  </template>
  <!-- WP-C10.2 面目標射擊：打座標，不打單位。 -->
  <template v-else-if="ordering.orderType === 'FIRE_MISSION'">
    <div class="hint">點地圖設落點（紅色準星）。間瞄火力不需視線——打的就是看不見的地方。</div>
    <div class="movebtns">
      <button
        data-testid="pick-fire-point"
        :class="{ armed: ordering.targeting }"
        @click="ordering.waypointMode = false; ordering.targeting = true"
      >
        {{ ordering.targeting ? '點地圖設落點…' : '設定落點' }}
      </button>
      <button
        v-if="ordering.firePoint"
        data-testid="clear-fire-point"
        title="清除落點"
        @click="ordering.firePoint = null"
      >
        <i class="pi pi-times" /> 清除
      </button>
    </div>
    <div class="dest" data-testid="fire-point">
      <template v-if="ordering.firePoint">
        🎯 {{ ordering.firePoint.lat.toFixed(5) }}, {{ ordering.firePoint.lng.toFixed(5) }}
      </template>
      <template v-else>未設落點</template>
    </div>
    <label class="rounds">
      發數
      <input
        v-model.number="ordering.fireRounds"
        type="number"
        min="1"
        max="200"
        data-testid="fire-rounds"
      >
      <span class="dim">· 每發各自散布，多發覆蓋一片</span>
    </label>
    <!-- 本局要求火協時要掛核准單；沒有可用的單就直說，別讓人對著空下拉猜。 -->
    <select
      v-if="ordering.approvedFireRequests.length"
      v-model="ordering.fireRequestId"
      data-testid="fire-request"
    >
      <option :value="null">不掛核准單</option>
      <!-- 申請單本身沒有摘要欄位（附言是另一封 REQUEST 信文）——以申請者 + tick 辨識。 -->
      <option v-for="r in ordering.approvedFireRequests" :key="r.id" :value="r.id">
        火協核准 · T{{ r.requested_at_tick }} · {{ r.requested_by }}
      </option>
    </select>
    <p v-else class="fm-warn dim" data-testid="no-fire-request">
      無已核准的火力支援申請（本局若要求火協，此令會被預檢擋下——請先向 FSO 申請）。
    </p>
    <p class="fm-warn" data-testid="fire-danger">
      ⚠ 落彈半徑內<b>敵我皆受損</b>——砲彈不會挑人。落點附近有友軍時請先確認。
    </p>
  </template>
  <!-- WP-A2 任務級下令：下一道任務，由分解器持續展開成低階令並執行到完成。 -->
  <template v-else-if="ordering.orderType === 'MISSION'">
    <select v-model="ordering.missionType" data-testid="mission-type">
      <option v-for="m in MISSION_OPTS" :key="m.value" :value="m.value">
        {{ m.label }} · {{ m.hint }}
      </option>
    </select>
    <div class="hint">
      下的是「任務」而非單一動作——系統會自動展開成移動、接敵、就位構築姿態並執行到完成，
      不必每回合重下。取消任務會連帶取消它派生的所有未完成子令。
    </div>
    <div class="movebtns">
      <button
        data-testid="pick-mission-geometry"
        :class="{ armed: ordering.targeting }"
        @click="ordering.waypointMode = false; ordering.targeting = !ordering.targeting"
      >
        {{ ordering.targeting ? '點地圖標定…' : (ordering.missionNeedsPoint ? '標定目標' : '標定路線') }}
      </button>
      <button
        v-if="ordering.missionPoint || ordering.missionPath.length"
        data-testid="clear-mission-geometry"
        title="清除任務幾何"
        @click="ordering.clearMission()"
      >
        <i class="pi pi-times" /> 清除
      </button>
    </div>
    <div class="dest" data-testid="mission-geometry">
      <template v-if="ordering.missionPoint">
        🎯 {{ ordering.missionPoint.lat.toFixed(5) }}, {{ ordering.missionPoint.lng.toFixed(5) }}
      </template>
      <template v-else-if="!ordering.missionNeedsPoint && ordering.missionPath.length">
        路線 {{ ordering.missionPath.length }} 點
      </template>
      <template v-else>未標定</template>
      <span v-if="ordering.missionNeedsPoint && ordering.missionPath.length" class="snaphint">
        · 軸線 {{ ordering.missionPath.length }} 點
      </span>
    </div>
    <label v-if="ordering.missionNeedsPoint" class="rounds">
      半徑
      <input
        v-model.number="ordering.missionRadiusM"
        type="number"
        min="50"
        max="50000"
        step="50"
        data-testid="mission-radius"
      >
      <span class="dim">公尺 · 目標圈/防區範圍</span>
    </label>
  </template>
  <!-- WP-C3 隊形/乘駐車：後端收成一個 FORMATION 令，兩者至少指定一項。 -->
  <template v-else-if="ordering.orderType === 'FORMATION'">
    <label class="rounds">
      隊形
      <select v-model="ordering.formation" data-testid="formation-select">
        <option value="">（不變更）</option>
        <option value="COLUMN">縱隊 · 行軍最快、挨砲最慘</option>
        <option value="LINE">橫隊 · 火力全開、機動最慢</option>
        <option value="WEDGE">楔形 · 攻擊隊形</option>
        <option value="VEE">V 形 · 預期正面接敵</option>
        <option value="HERRINGBONE">魚骨 · 停止間環形警戒</option>
      </select>
    </label>
    <label class="rounds">
      乘駐車
      <select v-model="ordering.mounted" data-testid="mounted-select">
        <option value="">（不變更）</option>
        <option value="true">上車 · 速度快、目標大</option>
        <option value="false">下車 · 受彈面小、火力全</option>
      </select>
    </label>
    <div class="hint">
      兩者至少要指定一項；選「不變更」的欄位維持原狀——只想下車的令不該把隊形一起重設。
    </div>
  </template>
  <!-- WP-C2 障礙作業：須工兵單位且距作業點 500 m 內（預檢會擋並說明原因）。 -->
  <template v-else-if="ordering.orderType === 'ENGINEER'">
    <select v-model="ordering.engineerAction" data-testid="engineer-action">
      <option value="EMPLACE">設障 · 構築新障礙</option>
      <option value="BREACH">破障 · 清除既有障礙</option>
    </select>
    <template v-if="ordering.engineerAction === 'EMPLACE'">
      <select v-model="ordering.obstacleType" data-testid="obstacle-type">
        <option value="WIRE">鐵絲網 · 非工兵幾乎過不去</option>
        <option value="MINEFIELD">雷區 · 觸雷即戰損並停止</option>
        <option value="TANK_DITCH">戰車壕 · 實質阻擋</option>
        <option value="ABATIS">鹿砦 · 伐木障礙</option>
        <option value="BRIDGE_DEMO">斷橋 · 道路加速失效</option>
      </select>
      <div class="movebtns">
        <button
          data-testid="pick-engineer-point"
          :class="{ armed: ordering.targeting }"
          @click="ordering.waypointMode = false; ordering.targeting = !ordering.targeting"
        >
          {{ ordering.targeting ? '點地圖標定…' : '標定作業點' }}
        </button>
      </div>
      <div class="dest" data-testid="engineer-point">
        <template v-if="ordering.engineerPoint">
          🚧 {{ ordering.engineerPoint.lat.toFixed(5) }}, {{ ordering.engineerPoint.lng.toFixed(5) }}
        </template>
        <template v-else>未標定</template>
      </div>
    </template>
    <label v-else class="rounds">
      標的
      <input
        v-model="ordering.engineerFeatureId"
        type="text"
        placeholder="障礙標註 id"
        data-testid="engineer-feature-id"
      >
    </label>
    <div class="hint">
      ⚠ 須工兵單位（ORBAT 的 <code>unit_kind=ENGINEER</code>）且距作業點 500 m 內。
      破障/設障各有工時（雷區約 45 分鐘、斷橋約 2 小時），**完工才會改變地圖**。
    </div>
  </template>

  <!-- WP-C1 姿態令：宣告要進入的姿態。**轉換要時間**，這裡只是下令開始做。 -->
  <template v-else-if="ordering.orderType === 'POSTURE'">
    <div class="hint">構工要時間——下令當下不會立刻生效，期間仍以前一級計算。移動會讓進度作廢。</div>
    <select v-model="ordering.posture" data-testid="posture-select">
      <option v-for="p in POSTURE_OPTS" :key="p" :value="p">
        {{ POSTURE_LABELS[p]?.text }} · {{ POSTURE_LABELS[p]?.hint }}
      </option>
    </select>
  </template>
  <template v-else>
    <div class="hint">
      點地圖上的敵方單位鎖定目標（紅環），或從清單選：<template v-if="allowFratricide"
        ><br >本局<b>允許誤傷</b>——友軍要先按「設定目標」進入瞄準才點得到。</template
      >
    </div>
    <select v-model="ordering.targetUnitId" data-testid="engage-target">
      <option :value="null">選目標</option>
      <option v-for="u in engageTargets" :key="u.id" :value="u.id">
        {{ isFriendly(u.faction) ? `⚠ ${u.designation}（友軍）` : u.designation }}
      </option>
    </select>
    <div class="dest" data-testid="target-label">
      {{ targetUnit ? `🎯 ${targetUnit.designation}（${targetUnit.faction}）` : '未鎖定目標' }}
    </div>
    <!-- WP-C9 誤傷二次確認。換目標會自動退回未勾（見 useCopOrdering 的 watch）。 -->
    <label v-if="fratricideTarget" class="fratricide" data-testid="fratricide-ack">
      <input v-model="ordering.fratricideAck" type="checkbox" >
      <span
        >⚠ <b>對友軍開火</b>：{{ targetUnit?.designation }} 與本軍為同盟關係。此令將照常執行並<b
          >記入 AAR</b
        >。</span
      >
    </label>
    <template v-if="ordering.weapons.length">
      <select v-model="ordering.weaponId" data-testid="engage-weapon">
        <option :value="null">{{ ordering.weapons.length >= 2 ? '聯合火力（全武器一起打）' : '預設武器' }}</option>
        <option v-for="w in ordering.weapons" :key="w.id" :value="w.id">
          {{ w.name }}<span v-if="ordering.liveAmmo(w) != null"> · 彈 {{ ordering.liveAmmo(w) }}</span>
        </option>
      </select>
      <!-- 聯合火力（未選單一武器且 ≥2 武器）：顯示將開火的武器組合 + 火力政策（P4）。 -->
      <template v-if="ordering.combinedMode">
        <select v-model="ordering.firePolicy" data-testid="engage-fire-policy">
          <option v-for="p in FIRE_POLICY_OPTS" :key="p.value" :value="p.value">
            {{ p.label }}
          </option>
        </select>
        <ul class="weapon-mix" data-testid="weapon-mix">
          <li v-for="w in ordering.weapons" :key="w.id">
            <i class="pi pi-bullseye" /> {{ w.name }}
            <span v-if="w.max_range_m" class="dim">· {{ (w.max_range_m / 1000).toFixed(1) }} km</span>
            <span v-if="ordering.liveAmmo(w) != null" class="dim">· 彈 {{ ordering.liveAmmo(w) }}</span>
          </li>
        </ul>
      </template>
      <!-- 指定單一武器：彈種選擇（單武器射擊路徑）。 -->
      <select
        v-if="!ordering.combinedMode && ordering.ammoOptions.length"
        v-model="ordering.ammoType"
        data-testid="engage-ammo"
      >
        <option :value="null">彈種（預設）</option>
        <option v-for="a in ordering.ammoOptions" :key="a" :value="a">{{ a }}</option>
      </select>
    </template>
  </template>
  <button data-testid="submit-order" :disabled="!canSubmit" @click="ordering.submit">
    {{ SUBMIT_LABELS[ordering.orderType] }}
  </button>
  <p v-if="ordering.message" data-testid="order-message">{{ ordering.message }}</p>
  <div v-if="ordering.precheck" class="precheck" data-testid="precheck">
    <div :class="ordering.precheck.feasible ? 'ok' : 'bad'">
      預檢：{{ ordering.precheck.feasible ? '可行' : '不可行' }}
    </div>
    <ul>
      <li v-for="(c, i) in ordering.precheck.checks" :key="i">
        {{ c.passed ? '✓' : '✗' }} {{ c.name }} <span v-if="c.detail">— {{ c.detail }}</span>
      </li>
    </ul>
  </div>
</div>
</template>

<style scoped>
.wsec-hd {
  font-size: 0.78rem;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 0.4rem;
}
.order h3 {
  margin: 0.75rem 0 0.375rem;
  font-size: 0.8125rem;
  color: #94a3b8;
}
.units {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.units li {
  padding: 0.375rem 0.5rem;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
  cursor: pointer;
}
/* 單位小工具依陣營分組（可收合/展開） */
.ufac {
  margin-bottom: 0.35rem;
}
.ufac-hd {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  width: 100%;
  padding: 0.25rem 0.4rem;
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 0.3rem;
  color: #cbd5e1;
  font-size: 0.8rem;
  cursor: pointer;
}
.ufac-hd:hover {
  border-color: #334155;
}
.ufac-hd .pi {
  font-size: 0.7rem;
  color: #64748b;
}
.ufac-count {
  color: #64748b;
  font-size: 0.75rem;
}
/* 陣營戰力（量體加權）——靠右對齊，與各單位的效能%同一套色帶。 */
.ufac-pow {
  margin-left: auto;
  font-size: 0.78rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.ufac-ko {
  color: #ef4444;
  font-size: 0.7rem;
}
.ufac-units {
  list-style: none;
  margin: 0.2rem 0 0;
  padding: 0 0 0 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.units li.sel {
  border-color: #2563eb;
  background: #172554;
}
.ufac-hd .u-dot {
  display: inline-block;
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  flex: none;
}
.units li .u-hp {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.units li .u-ko {
  margin-left: 0.35rem;
  color: #ef4444;
  font-size: 0.72rem;
  font-weight: 700;
}
.units li .u-fixed {
  margin-left: 0.3rem;
  font-size: 0.78rem;
}
.units li.out-scope {
  opacity: 0.45;
  cursor: not-allowed;
}
.units li.out-scope:hover {
  border-color: #1e293b;
}
.units li .u-ban {
  margin-left: 0.3rem;
  font-size: 0.72rem;
}
.order .fixed-note {
  margin: 0.35rem 0;
  padding: 0.35rem 0.5rem;
  background: rgba(251, 191, 36, 0.12);
  border: 1px solid rgba(251, 191, 36, 0.4);
  border-radius: 0.3rem;
  color: #fde68a;
  font-size: 0.74rem;
  line-height: 1.4;
}
.empty {
  color: #64748b;
  cursor: default !important;
}
.order {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  margin: 0.5rem 0;
}
.order select,
.order button {
  padding: 0.375rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  cursor: pointer;
}
.order button.armed {
  border-color: #eab308;
}
.dest {
  font-family: monospace;
  color: #94a3b8;
}
.dest .snaphint {
  font-family: system-ui, sans-serif;
  color: #eab308;
  font-size: 0.68rem;
}
.dest .snaphint.precise {
  color: #f472b6;
}
.order .precise {
  display: flex;
  gap: 0.35rem;
  align-items: center;
  color: #94a3b8;
  font-size: 0.72rem;
  cursor: pointer;
}
/* #28 移動路徑：按鈕列 + 成本試算 */
.order .movebtns {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.order .movebtns button {
  font-size: 0.72rem;
  padding: 0.3rem 0.45rem;
}
.mvprev {
  margin-top: 0.3rem;
  padding: 0.4rem 0.5rem;
  border: 1px solid #334155;
  border-radius: 0.3rem;
  background: #0b1220;
  font-size: 0.74rem;
}
.mvprev .mv-row {
  display: flex;
  gap: 0.75rem;
  color: #cbd5e1;
}
.mvprev .mv-row b {
  color: #38bdf8;
}
.mvprev .mv-sub {
  color: #94a3b8;
  font-size: 0.78rem;
}
.mvprev .mv-routed {
  color: #7dd3fc;
}
.mvprev .mv-lowfuel {
  color: #fbbf24;
}
.mvprev .mv-ok {
  margin-top: 0.25rem;
  color: #4ade80;
}
.mvprev .mv-forced {
  margin-top: 0.25rem;
  color: #f59e0b;
}
.mvprev .mv-forced ul {
  margin: 0.2rem 0 0;
  padding-left: 1.1rem;
  color: #fbbf24;
}
.order .selunit {
  color: #60a5fa;
  font-weight: 600;
}
.order .hint {
  color: #94a3b8;
  font-size: 0.72rem;
  line-height: 1.4;
}
/* WP-C10.2 火力任務：發數輸入 + 誤傷警語。 */
.order .rounds {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  color: #94a3b8;
  font-size: 0.72rem;
}
.order .rounds input {
  width: 4.5rem;
  padding: 0.2rem 0.35rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
}
.order .dim {
  color: #64748b;
}
.order .fm-warn {
  margin: 0.2rem 0 0;
  font-size: 0.7rem;
  line-height: 1.4;
  color: #fca5a5;
}
.order .fm-warn.dim {
  color: #94a3b8;
}
/* 聯合火力武器組合清單（P4）：顯示將一起開火的武器。 */
.weapon-mix {
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0.35rem 0.5rem;
  background: rgba(30, 58, 95, 0.25);
  border: 1px solid #1e3a5f;
  border-radius: 0.35rem;
  font-size: 0.72rem;
  color: #cbd5e1;
}
.weapon-mix li {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.1rem 0;
}
.weapon-mix .dim {
  color: #94a3b8;
}
.precheck .ok {
  color: #4ade80;
}
.precheck .bad {
  color: #f87171;
}
.precheck ul {
  margin: 0.25rem 0 0;
  padding-left: 1rem;
}
.fratricide {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  padding: 6px 8px;
  border: 1px solid var(--p-red-500);
  border-radius: 4px;
  background: color-mix(in srgb, var(--p-red-500) 12%, transparent);
  font-size: 11px;
  line-height: 1.45;
  cursor: pointer;
}
.fratricide input {
  margin-top: 2px;
  flex: none;
}
</style>
