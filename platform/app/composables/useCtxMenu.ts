/**
 * 地圖右鍵選單（#3，ATAK 式移動/攻擊；#26/#99 圖形編修）。
 *
 * 流程：右鍵我方單位 → 選單「移動/攻擊」→ 十字準星 → 點地圖選落點/目標 → 於下令面板確認。
 *
 * **這是一個派送器，不是獨立的狀態機**：它自己只有一個 `ctxMenu` ref（選單開在哪、
 * 游標下有什麼），其餘每個動作都是把一次右鍵翻譯成「對下令狀態機」或「對地圖編輯器」的呼叫。
 * 所以它的相依面天生就寬——`ordering` 與 `editor` 兩整包都收下正是為了**把這件事攤在明處**：
 * 看呼叫點就知道右鍵選單會動到哪兩台機器，而不是散在頁面裡各自伸手。
 * 後續若想「整理」成隱藏相依的樣子，等於把耦合藏起來，不要那樣做。
 *
 * #99b 的刻意設計：`ctxEditFeature` 同時**解鎖整形**。控制點與拖曳只在明確從右鍵選單
 * 進入後才生效——單純點選圖形不解鎖，否則點一下再手滑就把標註拖歪了。
 */
import { computed, nextTick, ref, type ComputedRef, type Ref } from 'vue'
import { latLngToCell } from 'h3-js'
import type { UnitView } from '~/composables/useOrders'
import type { useCopOrdering } from '~/composables/useCopOrdering'
import type { useMapEditor } from '~/composables/useMapEditor'

/** 右鍵當下游標底下有什麼（MapCanvas 一併帶上來）。 */
export interface CtxMenuState {
  x: number
  y: number
  lng: number
  lat: number
  unitId?: string
  faction?: string
  kind?: string
  featureId?: string
  vertexIndex?: number // #99 游標下的控制點索引（右鍵可刪點）
}

export function useCtxMenu(opts: {
  ordering: ReturnType<typeof useCopOrdering>
  editor: ReturnType<typeof useMapEditor>
  selectedId: Ref<string | null>
  selectUnit: (id: string) => void
  realUnits: Ref<UnitView[]>
  realUnitIds: ComputedRef<Set<string>>
  isFriendly: (faction?: string | null) => boolean
  preciseMove: Ref<boolean>
  /** 座標查詢開著時不彈選單（避免干擾）。 */
  coordQuery: Ref<boolean>
}) {
  const {
    ordering,
    editor,
    selectedId,
    selectUnit,
    realUnits,
    realUnitIds,
    isFriendly,
    preciseMove,
    coordQuery,
  } = opts

  const ctxMenu = ref<CtxMenuState | null>(null)

  function onContextMenu(e: CtxMenuState) {
    // 繪圖/座標查詢時不彈選單（避免干擾）。
    if (editor.drawActive.value || coordQuery.value) return
    ctxMenu.value = e
  }
  function closeCtx() {
    ctxMenu.value = null
  }

  const ctxIsMine = computed(
    () =>
      !!ctxMenu.value?.unitId &&
      realUnitIds.value.has(ctxMenu.value.unitId) &&
      isFriendly(ctxMenu.value.faction),
  )
  const ctxIsEnemy = computed(
    () =>
      !!ctxMenu.value?.unitId &&
      realUnitIds.value.has(ctxMenu.value.unitId) &&
      !isFriendly(ctxMenu.value.faction),
  )
  const ctxUnitName = computed(() => {
    const id = ctxMenu.value?.unitId
    return (id && realUnits.value.find((u) => u.id === id)?.designation) || id || ''
  })

  // ---- 對地圖編輯器的動作（#26）：編輯（開編輯工具列並選取）/ 旋轉 / 刪除 / 刪點 ----
  function ctxEditFeature() {
    const id = ctxMenu.value?.featureId
    closeCtx()
    if (!id) return
    editor.onFeatureClick({ id })
    editor.armReshape(id) // 必須在 onFeatureClick 之後：selectedFeatureId 的 watch 會清掉不相符的解鎖
  }
  async function ctxRotateFeature(deg: number) {
    const id = ctxMenu.value?.featureId
    closeCtx()
    if (!id) return
    if (editor.selectedFeatureId.value !== id) editor.onFeatureClick({ id })
    await nextTick()
    await editor.rotateFeature(deg)
  }
  async function ctxDeleteFeature() {
    const id = ctxMenu.value?.featureId
    closeCtx()
    if (id) await editor.removeFeature(id)
  }
  /** #99 右鍵控制點 → 刪除該頂點。 */
  async function ctxDeleteVertex() {
    const idx = ctxMenu.value?.vertexIndex
    closeCtx()
    if (idx != null) await editor.deleteVertexAt(idx)
  }

  // ---- 對下令狀態機的動作（#3）----
  // 武裝「移動」：選該單位（若右鍵在單位上），進入 MOVE 目標設定（十字準星）。
  function ctxArmMove() {
    if (ctxMenu.value?.unitId && ctxIsMine.value) selectUnit(ctxMenu.value.unitId)
    if (!selectedId.value) return closeCtx()
    ordering.orderType.value = 'MOVE'
    ordering.targeting.value = true
    closeCtx()
  }
  // 武裝「攻擊」：選該單位，進入 ENGAGE，點敵方單位鎖定目標。
  function ctxArmAttack() {
    if (ctxMenu.value?.unitId && ctxIsMine.value) selectUnit(ctxMenu.value.unitId)
    if (!selectedId.value) return closeCtx()
    ordering.orderType.value = 'ENGAGE'
    ordering.targeting.value = true
    closeCtx()
  }
  // 直接「移動至此」：用右鍵點擊處為落點（免再點一次）。
  function ctxMoveHere() {
    const c = ctxMenu.value
    if (!c || !selectedId.value) return closeCtx()
    ordering.orderType.value = 'MOVE'
    ordering.destH3.value = latLngToCell(c.lat, c.lng, 8)
    ordering.destLatLng.value = preciseMove.value ? { lng: c.lng, lat: c.lat } : null
    ordering.targeting.value = false
    closeCtx()
  }
  // 右鍵敵方單位（已選我方）→ 直接鎖為攻擊目標。
  function ctxLockTarget() {
    const c = ctxMenu.value
    if (!c?.unitId || !selectedId.value) return closeCtx()
    ordering.orderType.value = 'ENGAGE'
    ordering.targetUnitId.value = c.unitId
    ordering.targeting.value = false
    ordering.precheck.value = null
    ordering.message.value = `已鎖定目標：${ctxUnitName.value}`
    closeCtx()
  }

  return {
    ctxMenu,
    ctxIsMine,
    ctxIsEnemy,
    ctxUnitName,
    onContextMenu,
    closeCtx,
    ctxEditFeature,
    ctxRotateFeature,
    ctxDeleteFeature,
    ctxDeleteVertex,
    ctxArmMove,
    ctxArmAttack,
    ctxMoveHere,
    ctxLockTarget,
  }
}
