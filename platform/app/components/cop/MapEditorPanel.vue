<script setup lang="ts">
/**
 * 地圖編輯器面板（WP-G1 自 cop.vue 抽出）——標註/工事的繪製工具列、清單與屬性編輯。
 *
 * **狀態不在這裡**：整個編輯器的狀態機是 `useMapEditor()`，本元件把它**整包當一個 prop**
 * 收下。理由：這個面板要雙向綁二十幾個欄位，逐個開 prop + emit 等於把 MapCanvas 那個
 * 「50 個 props」的毛病複製一份——而那正是本卡要修的東西之一。ref 傳下來仍是 ref，
 * 故 `editor.drawLabel` 可直接 v-model，型別由 composable 的回傳型別保證。
 */
import { factionColor } from '~/composables/useUnits'
import {
  FEATURE_KINDS,
  SUPPLY_POINT_KIND,
  featureDestroyed,
  featureDisplayColor,
  ZONE_CLASSES,
} from '~/composables/useMapFeatures'
import { NATO_SUPPLY_CLASSES, supplyClassLabel } from '~/composables/useLabels'
import type { UnwrapNestedRefs } from 'vue'
import type { useMapEditor } from '~/composables/useMapEditor'

/**
 * ⚠ 收的是 `reactive(useMapEditor(...))` 而非原始回傳值。
 * Vue 樣板只會自動 unwrap **setup 頂層**的 ref；巢狀在物件裡的 ref 不會 unwrap，
 * `editor.drawLabel` 會拿到 Ref 物件本身（v-model 綁上去就壞了）。`reactive()` 會遞迴
 * unwrap 且寫入會回寫原 ref，故父子兩邊看到的是同一份狀態。
 */

defineProps<{
  editor: UnwrapNestedRefs<ReturnType<typeof useMapEditor>>
  /** 全知（統裁/白軍/管理）——歸屬變更與禁射級別僅其可設。 */
  canControl: boolean
  /** 歸屬下拉的選項。 */
  sessionFactions: string[]
  /** 首次載入尚未完成——顯示載入中而不是空狀態（空狀態要留給「真的沒有」）。 */
  loading?: boolean
  /** session-local 隱藏的標註 id（顯隱切換由頁面持有，不是編輯器狀態）。 */
  hiddenFeatureIds: string[]
}>()

defineEmits<{ (e: 'toggle-hidden', id: string): void }>()

/**
 * 標註類別的中文。**沿用繪製下拉的同一份 `FEATURE_KINDS`**，不另立對照表——
 * 清單與編輯區過去直接印 `kind`，於是沒取名的標註在清單上顯示成 `OBSTACLE`。
 * 另抄一份到 useLabels 只會讓兩邊漂移；查無仍原樣回傳（未來新增類別時看得到那個代號）。
 */
function featureKindLabel(kind: string): string {
  return FEATURE_KINDS.find((k) => k.value === kind)?.label ?? kind
}

/** WP-C2 障礙型別。與 `core/app/adjudication/obstacles.py` 的 `ObstacleType` 一致。 */
const OBSTACLE_TYPES = [
  { value: 'MINEFIELD', label: '雷區', hint: '通過時可能觸雷（依密度）' },
  { value: 'WIRE', label: '鐵絲網', hint: '減速；工兵可破障' },
  { value: 'TANK_DITCH', label: '戰車壕', hint: '車輛難以通過' },
  { value: 'ABATIS', label: '路障', hint: '倒木/障礙物阻絕' },
  { value: 'BRIDGE_DEMO', label: '斷橋', hint: '橋樑破壞' },
]
</script>

<template>
<!-- eslint-disable vue/no-mutating-props -- `editor` 是**共享的可變狀態束**（`reactive`
     包起來的 useMapEditor 回傳值），不是傳值 prop：寫入本來就要回寫到父層的同一份 ref，
     語義等同把 store 當 prop 傳。該規則要防的是「子元件改傳值 prop 導致父層真相分岔」，
     這裡沒有第二份真相。改用逐欄位 emit 的話就是四十個 emit——那正是本卡要消滅的形狀。 -->
<div class="map-editor" data-testid="map-editor">
<div v-if="!editor.drawActive" class="me-tools">
  <label class="me-kind">
    類別
    <select v-model="editor.drawFeatureKind" data-testid="draw-kind">
      <option v-for="k in editor.drawableKinds" :key="k.value" :value="k.value">{{ k.label }}</option>
    </select>
  </label>
  <!-- 補給點只畫得成點：`read_point()` 只解得開 [lng, lat]，存成線/面就整筆被略過，
       而它在圖上與有效的補給點長得一模一樣。與其畫完吃 422，不如不給那幾顆鈕。 -->
  <div class="me-btns">
    <button data-testid="draw-point" @click="editor.startDraw('POINT', editor.drawFeatureKind)">點</button>
    <template v-if="!editor.drawPointOnly">
      <button data-testid="draw-line" @click="editor.startDraw('LINE', editor.drawFeatureKind)">線</button>
      <button data-testid="draw-polygon" @click="editor.startDraw('POLYGON', editor.drawFeatureKind)">面</button>
      <button data-testid="draw-rect" @click="editor.startDraw('RECTANGLE', editor.drawFeatureKind)">矩形</button>
      <button data-testid="draw-circle" @click="editor.startDraw('CIRCLE', editor.drawFeatureKind)">圓形</button>
    </template>
  </div>
  <div class="me-attrs">
    <input v-model="editor.drawLabel" class="me-in" data-testid="draw-label" placeholder="名稱（選填）">
    <div class="me-row2">
      <label class="me-color" title="顏色">
        <input v-model="editor.drawColor" type="color">
        顏色
      </label>
      <label class="me-h" title="線條粗細（點狀標註不適用）">
        線寬<input
          v-model.number="editor.drawWidth"
          data-testid="draw-width"
          type="range"
          min="0.5"
          max="12"
          step="0.5"
        >{{ editor.drawWidth }}
      </label>
      <label v-if="editor.drawFeatureKind === 'OBSTACLE' || editor.drawFeatureKind === 'BUILDING'" class="me-h">
        高度<input v-model.number="editor.drawHeight" type="number" min="0" step="0.5"> m
      </label>
    </div>
    <!-- WP-C2 障礙型別/密度：不填＝純幾何障礙（與過去相同），填了才進裁決。 -->
    <div v-if="editor.drawFeatureKind === 'OBSTACLE'" class="me-row">
      <label class="me-h">型別
        <select v-model="editor.drawObstacleType" data-testid="draw-obstacle-type">
          <option value="">（不指定）</option>
          <option v-for="o in OBSTACLE_TYPES" :key="o.value" :value="o.value" :title="o.hint">
            {{ o.label }}
          </option>
        </select>
      </label>
      <label v-if="editor.drawObstacleType" class="me-h" title="0–1；愈高愈難通過、觸雷機率愈大">
        密度<input
          v-model.number="editor.drawDensity"
          type="number"
          min="0"
          max="1"
          step="0.1"
          data-testid="draw-density"
        >
      </label>
    </div>
    <!-- WP-C7.2 補給點庫存：**沒有庫存的補給點撥不出任何補給**，而它在圖上看起來完全正常。
         空倉庫請明寫 0——那與「忘了填」是不同的意思（後端也擋，422）。 -->
    <div v-if="editor.drawFeatureKind === SUPPLY_POINT_KIND" class="me-stock" data-testid="draw-supply-stock">
      <div class="me-sub">庫存（補給類別）</div>
      <label v-for="c in NATO_SUPPLY_CLASSES" :key="c" class="me-h">
        {{ c }}·{{ supplyClassLabel(c) }}
        <input
          v-model.number="editor.drawSupplyStock[c]"
          :data-testid="`draw-stock-${c}`"
          type="number"
          min="0"
          step="10"
          placeholder="不備"
        >
      </label>
      <div class="me-hint me-hint-zone">
        <i class="pi pi-exclamation-triangle" />
        補給點只撥交給<b>同陣營</b>單位（3 km 內）——請確認上方已切到該陣營視角，
        否則會落在共同層而沒有任何單位拉得到。
      </div>
    </div>
    <!-- WP-A3 禁射級別：**繪製當下就要選得到**。在此之前只有「畫完 → 選取 → 編輯」那一路
         寫得進 zone_class，於是畫完就以為圈好了禁射區，實際上火力裁決完全不認得它。
         僅全知可設（同編輯面板；一般指揮官只看得到下面那則提示）。 -->
    <label
      v-if="canControl"
      class="me-own"
      title="只有面（面／矩形／圓形）成得了區——點與線不參與火力裁決"
    >
      禁射
      <select v-model="editor.drawZoneClass" data-testid="draw-zone-class">
        <option v-for="z in ZONE_CLASSES" :key="z.value" :value="z.value">{{ z.label }}</option>
      </select>
    </label>
    <div v-if="editor.drawZoneNameUnset" class="me-hint me-hint-zone" data-testid="draw-zone-warn">
      <i class="pi pi-exclamation-triangle" />
      名稱像是禁射區，但<b>未指定禁射級別</b>——這樣畫出來的區對火力裁決沒有效力。
      <template v-if="!canControl">禁射級別須由統裁／白軍設定。</template>
    </div>
    <NatoSymbolSelect v-model="editor.drawSidc" data-testid="draw-sidc" title="北約符號（僅點）" />
    <input v-model="editor.drawNotes" class="me-in" data-testid="draw-notes" placeholder="備註（選填）">
  </div>
  <div class="me-weapon">
    <select v-model="editor.drawWeaponTemplate" data-testid="draw-weapon-tmpl" @focus="editor.ensureWeaponTemplates">
      <option value="">選武器範本…</option>
      <option v-for="t in editor.weaponTemplates" :key="t.id" :value="t.id">{{ t.name }}</option>
    </select>
    <button data-testid="draw-weapon" :disabled="!editor.drawWeaponTemplate" @click="editor.startWeaponDraw">
      <i class="pi pi-bullseye" /> 武器據點
    </button>
  </div>
</div>
<div v-else class="me-drawing">
  <span v-if="editor.drawKind === 'CIRCLE'">繪圓：先點中心，再點邊緣</span>
  <span v-else-if="editor.drawKind === 'RECTANGLE'">繪矩形：點兩個對角</span>
  <span v-else>繪製中 · {{ editor.draftCoords.length }} 點 · 點地圖加點</span>
  <div class="me-btns">
    <button
      v-if="editor.drawKind === 'LINE' || editor.drawKind === 'POLYGON'"
      data-testid="draw-finish"
      @click="editor.finishDraw"
    >完成</button>
    <button data-testid="draw-cancel" @click="editor.cancelDraw">取消</button>
  </div>
</div>
<div class="me-list">
  <div class="me-sub">標註 / 工事（{{ editor.mapFeatures.length }}）</div>
  <ul>
    <li
      v-for="f in editor.mapFeatures"
      :key="f.id"
      :class="{ sel: f.id === editor.selectedFeatureId, hidden: hiddenFeatureIds.includes(f.id) }"
      data-testid="feature-row"
      @click="editor.onFeatureClick({ id: f.id })"
    >
      <span class="fdot" :style="{ background: featureDisplayColor(f) }" />
      <span class="fname">{{ f.label || featureKindLabel(f.kind) }}</span>
      <!-- #92 歸屬陣營：共同層標「共同」，否則以該陣營色點+代號標示 -->
      <span
        class="fown"
        data-testid="feature-owner"
        :title="
          f.owner_faction === 'WHITE_CELL'
            ? '共同標註（全體可見）'
            : `${f.owner_faction} 的標註（僅該陣營與白軍可見）`
        "
      >
        <template v-if="f.owner_faction === 'WHITE_CELL'">共同</template>
        <template v-else>
          <span class="u-dot" :style="{ background: factionColor(f.owner_faction) }" />
          {{ f.owner_faction }}
        </template>
      </span>
      <button
        class="feye"
        data-testid="feature-toggle-vis"
        :title="hiddenFeatureIds.includes(f.id) ? '顯示' : '隱藏'"
        @click.stop="$emit('toggle-hidden', f.id)"
      >{{ hiddenFeatureIds.includes(f.id) ? '🚫' : '👁' }}</button>
      <button class="frm" data-testid="feature-delete" @click.stop="editor.removeFeature(f.id)"><i class="pi pi-times" /></button>
    </li>
    <li v-if="loading"><PanelLoading /></li>
    <li v-else-if="!editor.mapFeatures.length" class="empty">（尚無標註）</li>
  </ul>
</div>
<!-- 選取特徵的屬性編輯（#11）：名稱/顏色/備註/高度 → PATCH。 -->
<div v-if="editor.selectedFeature" class="me-edit" data-testid="feature-edit">
  <div class="me-sub">編輯：{{ featureKindLabel(editor.selectedFeature.kind) }}</div>
  <!-- #99 整形操作說明（控制點是地圖上的互動，面板裡看不到 → 需明講怎麼用）。
       #99b 未解鎖時顯示上鎖狀態＋解鎖鈕，讓「為什麼拖不動」有答案。 -->
  <div v-if="editor.canEditSelectedFeature" class="me-hint" data-testid="reshape-hint">
    <div class="me-hint-row">
      <span>
        <template v-if="editor.selectedFeature.geometry_type === 'POINT'">
          <i class="pi pi-arrows-alt" /> 調整中：直接拖曳圖示可移動位置
        </template>
        <template v-else>
          <i class="pi pi-arrows-alt" /> 調整中：拖白點改形狀 · 拖小圈可加點 · 拖線/面本身整體移動 ·
          <b>Alt＋點白點</b>或<b>右鍵白點</b>刪點
        </template>
      </span>
      <button class="me-lock" data-testid="reshape-lock" @click="editor.armReshape(null)">
        <i class="pi pi-lock" /> 完成
      </button>
    </div>
  </div>
  <div
    v-else-if="editor.mayEditSelectedFeature"
    class="me-hint me-hint-locked"
    data-testid="reshape-locked"
  >
    <div class="me-hint-row">
      <span><i class="pi pi-lock" /> 形狀已鎖定（避免誤觸）——右鍵此物件選「編輯形狀」</span>
      <button
        class="me-lock"
        data-testid="reshape-unlock"
        @click="editor.armReshape(editor.selectedFeatureId)"
      >
        <i class="pi pi-lock-open" /> 調整形狀
      </button>
    </div>
  </div>
  <input v-model="editor.editFeatLabel" class="me-in" data-testid="edit-feat-label" placeholder="名稱">
  <div class="me-row2">
    <label class="me-color"><input v-model="editor.editFeatColor" type="color"> 顏色</label>
    <label class="me-h" title="線條粗細">
      線寬<input
        v-model.number="editor.editFeatWidth"
        data-testid="edit-feat-width"
        type="range"
        min="0.5"
        max="12"
        step="0.5"
      >{{ editor.editFeatWidth }}
    </label>
    <label v-if="editor.selectedFeature.kind === 'OBSTACLE' || editor.selectedFeature.kind === 'BUILDING'" class="me-h">
      高度<input v-model.number="editor.editFeatHeight" type="number" min="0" step="0.5"> m
    </label>
  </div>
  <!-- #92 歸屬變更：僅全知可改（一般角色不顯示；後端亦擋 403）。 -->
  <label v-if="canControl" class="me-own">
    歸屬
    <select v-model="editor.editFeatOwner" data-testid="edit-feat-owner">
      <option value="WHITE_CELL">共同（全體可見）</option>
      <option v-for="f in sessionFactions" :key="f" :value="f">{{ f }}</option>
    </select>
  </label>
  <!-- WP-A3 禁射區：僅全知可設，且只對面有意義（點/線不成區）。 -->
  <label
    v-if="canControl && editor.selectedFeature.geometry_type === 'POLYGON'"
    class="me-own"
  >
    禁射
    <select v-model="editor.editFeatZone" data-testid="edit-feat-zone">
      <option v-for="z in ZONE_CLASSES" :key="z.value" :value="z.value">
        {{ z.label }}
      </option>
    </select>
  </label>
  <div v-if="editor.editFeatZone" class="me-hint me-hint-zone" data-testid="zone-hint">
    <i class="pi pi-ban" />
    {{
      editor.editFeatZone === 'NO_STRIKE'
        ? '此區內的目標不得射擊：AI 的交戰令會被護欄剔除，人員下令一律被拒。'
        : '此區內的目標需確認才可射擊：AI 令保留但升白軍確認，人員須明確勾選確認且會留痕。'
    }}
  </div>
  <!-- WP-C7.2 補給點庫存編輯。清空某一格＝「不備該類別」；0＝「有這一格但空了」。
       兩者對撥交端是不同的事，所以 placeholder 明講「不備」而不是留白。 -->
  <div v-if="editor.selectedFeature.kind === SUPPLY_POINT_KIND" class="me-stock" data-testid="edit-supply-stock">
    <div class="me-sub">庫存（補給類別）</div>
    <div v-if="featureDestroyed(editor.selectedFeature)" class="me-hint me-hint-zone" data-testid="supply-destroyed">
      <i class="pi pi-times-circle" /> <b>此補給點已被摧毀</b>——下游單位再也拉不到補給。
      （刻意留在圖上：AAR 要看得到它曾經在那裡。）
    </div>
    <label v-for="c in NATO_SUPPLY_CLASSES" :key="c" class="me-h">
      {{ c }}·{{ supplyClassLabel(c) }}
      <input
        v-model.number="editor.editFeatStock[c]"
        :data-testid="`edit-stock-${c}`"
        type="number"
        min="0"
        step="10"
        placeholder="不備"
      >
    </label>
  </div>
  <NatoSymbolSelect
    v-if="editor.selectedFeature.geometry_type === 'POINT'"
    v-model="editor.editFeatSidc"
    data-testid="edit-feat-sidc"
  />
  <!-- #26 旋轉：面/線繞質心旋轉；武器點旋轉射向。 -->
  <div class="me-row2 me-rot-row">
    <span class="me-rot-lbl">旋轉</span>
    <button class="me-rot" data-testid="feat-rotate-ccw" @click="editor.rotateFeature(-15)"><i class="pi pi-undo" /> 15°</button>
    <button class="me-rot" data-testid="feat-rotate-cw" @click="editor.rotateFeature(15)"><i class="pi pi-refresh" /> 15°</button>
  </div>
  <!-- 武器射向/雷達扇區（#11 C）：射程 + 方向 + 張角（360=全向圓）。 -->
  <template v-if="editor.selectedFeature.kind === 'WEAPON_EMPLACEMENT' || editor.editFeatRange != null">
    <div class="me-sub">射程 / 射向扇區</div>
    <div class="me-row2">
      <label class="me-h">射程<input
        v-model.number="editor.editFeatRange"
        type="number"
        min="0"
        max="100000"
        step="50"
        style="width: 5.5rem"
      > m</label>
      <label class="me-h">方向<input v-model.number="editor.editFeatDir" type="number" min="0" max="359"> °</label>
    </div>
    <label class="me-h" data-testid="edit-feat-arc">
      張角 {{ editor.editFeatArc }}°（360＝全向）
      <input v-model.number="editor.editFeatArc" type="range" min="10" max="360" step="5" style="width: 100%">
    </label>
    <!-- 地形裁切（#11）：逐方位 LOS 把稜線/反斜面啃出缺口。 -->
    <div class="me-row2">
      <button
        class="me-clip"
        :disabled="editor.clipBusy || !editor.editFeatRange"
        data-testid="apply-terrain-clip"
        @click="editor.applyTerrainClip"
      >
        <i class="pi pi-eye" /> {{ editor.clipBusy ? '裁切計算中…' : '地形裁切射界' }}
      </button>
      <button
        v-if="editor.terrainClips[editor.selectedFeature.id]"
        class="me-clip me-clip-off"
        data-testid="clear-terrain-clip"
        @click="editor.onClearTerrainClip(editor.selectedFeature.id)"
      >
        還原理想射界
      </button>
    </div>
  </template>
  <input v-model="editor.editFeatNotes" class="me-in" data-testid="edit-feat-notes" placeholder="備註">
  <button class="me-save" data-testid="save-feat-edit" @click="editor.saveFeatureEdit">儲存屬性</button>
</div>
</div>
</template>

<style scoped>
.feye {
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 0.75rem;
  opacity: 0.75;
}
.feye:hover {
  opacity: 1;
}
/* WP-C7.2 補給點庫存：每一格一列，數字欄靠右對齊（比大小時眼睛要能掃過去）。 */
.map-editor .me-stock {
  margin: 0.35rem 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.map-editor .me-stock label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.4rem;
}
.map-editor .me-stock input {
  width: 5rem;
  text-align: right;
}
.map-editor .me-list li.hidden .fname {
  opacity: 0.4;
  text-decoration: line-through;
}
/* 地圖編輯器面板（stage ③b）——浮在地圖左上。 */
.map-editor {
  position: absolute;
  left: 3.5rem;
  top: 1rem;
  z-index: 11;
  width: 14rem;
  padding: 0.6rem 0.75rem;
  border-radius: 0.5rem;
  border: 1px solid #3f3f1e;
  background: rgba(15, 23, 42, 0.96);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  font-size: 0.78rem;
  color: #e2e8f0;
}
.map-editor .me-kind {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  color: #94a3b8;
  font-size: 0.72rem;
  margin-bottom: 0.35rem;
}
.map-editor select {
  flex: 1;
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.2rem 0.3rem;
  font-size: 0.74rem;
}
.map-editor .me-btns {
  display: flex;
  gap: 0.3rem;
  margin-bottom: 0.35rem;
}
.map-editor .me-btns button,
.map-editor .me-weapon button {
  flex: 1;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #172554;
  color: #e2e8f0;
  cursor: pointer;
  padding: 0.2rem 0.35rem;
  font-size: 0.74rem;
}
.map-editor .me-weapon {
  display: flex;
  gap: 0.3rem;
  margin-bottom: 0.4rem;
}
.map-editor .me-weapon button:disabled {
  opacity: 0.5;
  cursor: default;
}
.map-editor .me-drawing {
  color: #fde68a;
  font-size: 0.74rem;
  margin-bottom: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
/* 繪製/編輯屬性欄（#11） */
.map-editor .me-attrs,
.map-editor .me-edit {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin: 0.3rem 0;
}
.map-editor .me-edit {
  border-top: 1px solid #1e293b;
  padding-top: 0.4rem;
}
.map-editor .me-in {
  padding: 0.25rem 0.4rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.75rem;
}
.map-editor .me-row2 {
  display: flex;
  gap: 0.6rem;
  align-items: center;
}
.map-editor .me-color,
.map-editor .me-h {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.72rem;
  color: #94a3b8;
}
/* #92 歸屬變更下拉（僅全知可見）。 */
.map-editor .me-own {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.72rem;
  color: #94a3b8;
}
.map-editor .me-own select {
  flex: 1;
  padding: 0.2rem 0.3rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #e2e8f0;
  font-size: 0.72rem;
}
.map-editor .me-color input {
  width: 1.6rem;
  height: 1.3rem;
  padding: 0;
  border: none;
  background: none;
}
.map-editor .me-h input {
  width: 3rem;
  padding: 0.15rem 0.3rem;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  background: #0a1626;
  color: #e2e8f0;
}
.map-editor .me-rot-row {
  align-items: center;
}
.map-editor .me-rot-lbl {
  color: #94a3b8;
  font-size: 0.72rem;
}
.map-editor .me-rot {
  padding: 0.2rem 0.4rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #0a1626;
  color: #cbd5e1;
  cursor: pointer;
  font-size: 0.72rem;
}
.map-editor .me-rot:hover {
  border-color: #2563eb;
}
.map-editor .me-save {
  padding: 0.3rem;
  border: 0;
  border-radius: 0.25rem;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
  font-size: 0.75rem;
}
.map-editor .me-sub {
  color: #64748b;
  font-size: 0.68rem;
  border-top: 1px solid #1e293b;
  padding-top: 0.35rem;
  margin-bottom: 0.25rem;
}
/* #99 整形操作提示 / #99b 鎖定狀態 */
.map-editor .me-hint {
  color: #7dd3fc;
  background: #0c2233;
  border: 1px solid #164e63;
  border-radius: 0.25rem;
  font-size: 0.66rem;
  line-height: 1.35;
  padding: 0.28rem 0.4rem;
  margin-bottom: 0.3rem;
}
.map-editor .me-hint-zone {
  color: #fca5a5;
  background: #2a1215;
  border-color: #7f1d1d;
}
.map-editor .me-hint-locked {
  color: #94a3b8;
  background: #111827;
  border-color: #334155;
}
.map-editor .me-hint-row {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  justify-content: space-between;
}
.map-editor .me-lock {
  flex: none;
  padding: 0.15rem 0.4rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 0.66rem;
  white-space: nowrap;
  cursor: pointer;
}
.map-editor .me-lock:hover {
  border-color: #2563eb;
}
.map-editor .me-clip {
  flex: 1;
  padding: 0.3rem;
  border: 1px solid #0e7490;
  border-radius: 0.25rem;
  background: #0e7490;
  color: #e0f2fe;
  cursor: pointer;
  font-size: 0.72rem;
}
.map-editor .me-clip:disabled {
  opacity: 0.5;
  cursor: default;
}
.map-editor .me-clip-off {
  flex: 0 0 auto;
  background: transparent;
  border-color: #475569;
  color: #94a3b8;
}
.map-editor .me-list ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  max-height: 12rem;
  overflow-y: auto;
}
.map-editor .me-list li {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.15rem 0.25rem;
  border-radius: 0.2rem;
  cursor: pointer;
}
.map-editor .me-list li.sel {
  background: #1e293b;
}
.map-editor .fdot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  flex: none;
}
.map-editor .fname {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* #92 標註歸屬陣營徽章：讓「這是誰畫的、誰看得到」一眼可辨。 */
.map-editor .fown {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  flex: none;
  padding: 0.05rem 0.3rem;
  border: 1px solid #334155;
  border-radius: 0.2rem;
  color: #94a3b8;
  font-size: 0.68rem;
  white-space: nowrap;
}
.map-editor .fown .u-dot {
  display: inline-block;
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
}
.map-editor .frm {
  border: none;
  background: transparent;
  color: #f87171;
  cursor: pointer;
}
.map-editor .empty {
  color: #64748b;
  cursor: default;
}
</style>
