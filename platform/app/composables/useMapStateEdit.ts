/**
 * 地圖狀態編輯（開打前布局）——暫停推演、拖放單位定位、繪障礙，完成再「開始兵推」。限白軍/導演。
 *
 * 「先暫停才准拖」是刻意的：單位位置在推演進行中屬於引擎權威，前端硬改會與 tick 賽跑；
 * 故本模式一律走 PAUSE → reposition → RESUME。後端仍會擋權限，這裡的 gate 只是 UX。
 *
 * 每次 reposition **無論成敗都重載單位**：成功→定位到新座標，失敗→彈回 DB 權威位置，
 * 不讓畫面停在一個後端沒接受的位置上。
 */
import { ref, type Ref } from 'vue'
import { apiFetch } from '~/composables/useApi'
import { fetchUnits, type UnitView } from '~/composables/useOrders'
import type { WidgetId, WStat } from '~/composables/useCopWidgets'

export function useMapStateEdit(opts: {
  sessionId: Ref<string>
  /** 進入編輯模式時順帶叫出繪圖工具。 */
  widgets: Ref<Record<WidgetId, WStat>>
  focusWidget: (id: WidgetId) => void
  /** reposition 後重載的目標（成敗都重載）。 */
  realUnits: Ref<UnitView[]>
  toasts: ReturnType<typeof useToasts>
}) {
  const { sessionId, widgets, focusWidget, realUnits, toasts } = opts

  const mapEditMode = ref(false)
  const selectedUnitCount = ref(0) // 地圖狀態編輯多選數量（「已選 N 個」徽章）

  async function enterMapEdit() {
    try {
      await apiFetch(`/sessions/${sessionId.value}/control`, {
        method: 'POST',
        body: { action: 'PAUSE' },
      })
      mapEditMode.value = true
      widgets.value.mapedit.open = true // 順帶開啟繪圖工具（障礙/建築）
      focusWidget('mapedit')
      toasts.push({
        severity: 'info',
        title: '地圖狀態編輯：已暫停推演',
        detail: '拖曳單位調整位置、用地圖編輯繪製障礙/建築，完成後按「開始推演」。',
        timeoutMs: 6000,
      })
    } catch {
      toasts.push({
        severity: 'error',
        title: '進入編輯模式失敗（需白軍/導演權限）',
        timeoutMs: 4000,
      })
    }
  }
  async function startWargame() {
    try {
      await apiFetch(`/sessions/${sessionId.value}/control`, {
        method: 'POST',
        body: { action: 'RESUME' },
      })
    } finally {
      mapEditMode.value = false
    }
    toasts.push({ severity: 'success', title: '開始推演', timeoutMs: 2500 })
  }
  async function onUnitMove(e: { id: string; lng: number; lat: number }) {
    try {
      await apiFetch(`/sessions/${sessionId.value}/units/${e.id}/reposition`, {
        method: 'POST',
        body: { lat: e.lat, lng: e.lng },
      })
    } catch {
      toasts.push({ severity: 'error', title: '單位移動失敗', timeoutMs: 3000 })
    }
    // 無論成敗都重載（成功→定位、失敗→還原到 DB 權威位置）。
    realUnits.value = await fetchUnits(sessionId.value).catch(() => realUnits.value)
  }
  function onUnitsSelected(e: { count: number }) {
    selectedUnitCount.value = e.count
  }
  // 地圖狀態編輯 · 多選整組移動：逐一 reposition（並行）後只重載一次。
  async function onUnitsMove(e: { moves: { id: string; lng: number; lat: number }[] }) {
    if (!e.moves.length) return
    try {
      await Promise.all(
        e.moves.map((m) =>
          apiFetch(`/sessions/${sessionId.value}/units/${m.id}/reposition`, {
            method: 'POST',
            body: { lat: m.lat, lng: m.lng },
          }),
        ),
      )
      toasts.push({ severity: 'success', title: `已移動 ${e.moves.length} 個單位`, timeoutMs: 2000 })
    } catch {
      toasts.push({
        severity: 'error',
        title: '批次移動失敗（部分單位可能未套用）',
        timeoutMs: 3000,
      })
    }
    realUnits.value = await fetchUnits(sessionId.value).catch(() => realUnits.value)
  }

  return {
    mapEditMode,
    selectedUnitCount,
    enterMapEdit,
    startWargame,
    onUnitMove,
    onUnitsSelected,
    onUnitsMove,
  }
}
