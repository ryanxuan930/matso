/**
 * COP 的操作員偏好（圖層/底圖/網格/線條樣式 + 精確移動）與其 localStorage 持久化（#3/#9）。
 *
 * 浮動視窗的幾何與開關**存在同一把鑰匙底下**（`matso.cop.layers`），故 `widgets` 由呼叫端
 * （`useCopWidgets`）傳進來一起存——分成兩把鑰匙會讓既有使用者的版面在升級後失效。
 * `preciseMove` 同理：它是下令行為的參數，但也是持久化偏好，故存檔歸這裡、使用歸下令層。
 *
 * 讀檔一律「型別對才套用」，壞資料整包忽略走預設——偏好不該讓 COP 開不起來。
 */
import { onMounted, ref, watch, type Ref } from 'vue'
import { buildBasemapSources } from '~/composables/useMapStyle'
import type { WStat, WidgetId } from '~/composables/useCopWidgets'

const LAYER_PREFS_KEY = 'matso.cop.layers'

export function useCopPrefs(widgets: Ref<Record<WidgetId, WStat>>) {
  const hex = ref(false)
  const hillshade = ref(false)
  const contour = ref(false)
  // 圖層透明度乘數 + 套疊順序（#9）+ 主/次等高線間距（#8）
  const layerOpacity = ref<Record<string, number>>({ basemap: 1, hillshade: 1, contour: 1, hex: 1 })
  const layerOrder = ref<string[]>(['hex', 'contour', 'hillshade'])
  const contourMajor = ref(100)
  const contourMinor = ref(50)
  // 座標網格（#9）
  const latlngGrid = ref(false)
  const mgrsGrid = ref(false)
  const gridStepDeg = ref(0.5)
  const hexMaxRes = ref(8) // 六角網格最細解析度上限（設定最小網格）
  const hexLimitKm = ref(0) // 交戰範圍限制（km；0=不限）
  const dayNight = ref(false) // 日照視覺（#6）
  const timeOfDay = ref(12) // 一日時間 0–24（#6）
  // 線條粗細設定（#5）：六角網格線 + 主/次等高線線寬（px）
  const hexLineWidth = ref(0.5)
  const contourMajorWidth = ref(1.2)
  const contourMinorWidth = ref(0.5)
  // #22 線條顏色 + 座標網格線寬
  const hexLineColor = ref('#38bdf8')
  const contourColor = ref('#c9a15c')
  const gridColor = ref('#5b7fa6')
  const gridWidth = ref(0.5)
  const mgrsColor = ref('#facc15')
  // APP-6A 符號詳細度（§506.1）——**純操作員偏好**，絕不下放成後端欄位。
  const symbolDetail = ref('STD')
  // 精確移動預設「開」：跳過六角格心吸附，單位精確走到點擊處。六角格心吸附在 <1km 近距作戰
  // （校園/大樓）會把落點吸回格心（≈原位）造成「下令後跑回原位」的錯覺（#2/#15）；預設關閉吸附
  // 消除此問題。需大範圍推演的粗略化/省算時，可取消勾選改回六角吸附。
  const preciseMove = ref(true)

  // 底圖來源（可抽換，#2）：離線 / 街道 / 衛星 / 軍用…由 runtimeConfig 注入。
  const pub = useRuntimeConfig().public
  const basemapSources = buildBasemapSources({
    tileUrl: pub.tileUrl as string,
    satelliteUrl: pub.satelliteUrl as string | undefined,
    basemaps: pub.basemaps as never,
    onlineBasemaps: pub.onlineBasemaps as boolean,
  })
  // 預設用「街道」（有本地 tileserver 時）；載不到才回退離線格線。
  const basemap = ref(basemapSources.some((s) => s.id === 'street') ? 'street' : 'offline')
  function onBasemapError() {
    if (basemap.value !== 'offline') basemap.value = 'offline'
  }
  // 是否已設定離線 tile server（有 .mbtiles）。未設 → 顯示離線底圖提示（SPEC §13.2）。
  const hasTiles = computed(() => !!pub.tileUrl)

  // 載入 → 存檔（跨換頁/重整保留操作員的 COP 設定：開啟的圖層、底圖、透明度、套疊順序…）。
  onMounted(() => {
    if (!import.meta.client) return
    try {
      const p = JSON.parse(localStorage.getItem(LAYER_PREFS_KEY) ?? '{}')
      if (typeof p.hex === 'boolean') hex.value = p.hex
      if (typeof p.hillshade === 'boolean') hillshade.value = p.hillshade
      if (typeof p.contour === 'boolean') contour.value = p.contour
      // 底圖：僅在該來源仍存在時還原（線上底圖可能已關閉 → 回退預設）。
      if (typeof p.basemap === 'string' && basemapSources.some((s) => s.id === p.basemap)) {
        basemap.value = p.basemap
      }
      if (p.layerOpacity) layerOpacity.value = { ...layerOpacity.value, ...p.layerOpacity }
      if (Array.isArray(p.layerOrder) && p.layerOrder.length) layerOrder.value = p.layerOrder
      if (typeof p.contourMajor === 'number') contourMajor.value = p.contourMajor
      if (typeof p.contourMinor === 'number') contourMinor.value = p.contourMinor
      if (typeof p.latlngGrid === 'boolean') latlngGrid.value = p.latlngGrid
      if (typeof p.mgrsGrid === 'boolean') mgrsGrid.value = p.mgrsGrid
      if (typeof p.gridStepDeg === 'number') gridStepDeg.value = p.gridStepDeg
      if (typeof p.hexMaxRes === 'number') hexMaxRes.value = p.hexMaxRes
      if (typeof p.hexLimitKm === 'number') hexLimitKm.value = p.hexLimitKm
      if (typeof p.dayNight === 'boolean') dayNight.value = p.dayNight
      if (typeof p.timeOfDay === 'number') timeOfDay.value = p.timeOfDay
      if (typeof p.preciseMove === 'boolean') preciseMove.value = p.preciseMove
      if (p.widgets && typeof p.widgets === 'object') {
        for (const id of Object.keys(widgets.value) as WidgetId[]) {
          const s = p.widgets[id]
          if (!s || typeof s !== 'object') continue
          const cur = widgets.value[id]
          const dock =
            s.dock === 'left' || s.dock === 'right' || s.dock === 'float' ? s.dock : cur.dock
          widgets.value[id] = {
            open: typeof s.open === 'boolean' ? s.open : cur.open,
            dock,
            x: typeof s.x === 'number' ? s.x : cur.x,
            y: typeof s.y === 'number' ? s.y : cur.y,
            w: typeof s.w === 'number' ? s.w : cur.w,
            h: typeof s.h === 'number' ? s.h : cur.h,
            // z **刻意不還原**：層序是「這一次工作階段誰在上面」，不是偏好。
            // 還原它會讓上次留在最上層的視窗蓋住這次剛打開的。
            z: cur.z,
          }
        }
      }
      if (typeof p.hexLineWidth === 'number') hexLineWidth.value = p.hexLineWidth
      if (typeof p.contourMajorWidth === 'number') contourMajorWidth.value = p.contourMajorWidth
      if (typeof p.contourMinorWidth === 'number') contourMinorWidth.value = p.contourMinorWidth
      if (typeof p.hexLineColor === 'string') hexLineColor.value = p.hexLineColor
      if (typeof p.contourColor === 'string') contourColor.value = p.contourColor
      if (typeof p.gridColor === 'string') gridColor.value = p.gridColor
      if (typeof p.gridWidth === 'number') gridWidth.value = p.gridWidth
      if (typeof p.mgrsColor === 'string') mgrsColor.value = p.mgrsColor
      if (p.symbolDetail === 'MIN' || p.symbolDetail === 'STD' || p.symbolDetail === 'FULL')
        symbolDetail.value = p.symbolDetail
    } catch {
      /* 壞資料忽略，用預設 */
    }
  })

  watch(
    [
      hex,
      hillshade,
      contour,
      basemap,
      layerOpacity,
      layerOrder,
      contourMajor,
      contourMinor,
      latlngGrid,
      mgrsGrid,
      gridStepDeg,
      hexMaxRes,
      hexLimitKm,
      dayNight,
      timeOfDay,
      preciseMove,
      hexLineWidth,
      contourMajorWidth,
      contourMinorWidth,
      hexLineColor,
      contourColor,
      gridColor,
      gridWidth,
      symbolDetail,
      mgrsColor,
      widgets,
    ],
    () => {
      if (!import.meta.client) return
      try {
        localStorage.setItem(
          LAYER_PREFS_KEY,
          JSON.stringify({
            hex: hex.value,
            hillshade: hillshade.value,
            contour: contour.value,
            basemap: basemap.value,
            layerOpacity: layerOpacity.value,
            layerOrder: layerOrder.value,
            contourMajor: contourMajor.value,
            contourMinor: contourMinor.value,
            latlngGrid: latlngGrid.value,
            mgrsGrid: mgrsGrid.value,
            gridStepDeg: gridStepDeg.value,
            hexMaxRes: hexMaxRes.value,
            hexLimitKm: hexLimitKm.value,
            dayNight: dayNight.value,
            timeOfDay: timeOfDay.value,
            preciseMove: preciseMove.value,
            widgets: widgets.value,
            hexLineWidth: hexLineWidth.value,
            contourMajorWidth: contourMajorWidth.value,
            contourMinorWidth: contourMinorWidth.value,
            hexLineColor: hexLineColor.value,
            contourColor: contourColor.value,
            gridColor: gridColor.value,
            gridWidth: gridWidth.value,
            mgrsColor: mgrsColor.value,
            symbolDetail: symbolDetail.value,
          }),
        )
      } catch {
        /* 配額/隱私模式忽略 */
      }
    },
    { deep: true },
  )

  return {
    hex,
    hillshade,
    contour,
    layerOpacity,
    layerOrder,
    contourMajor,
    contourMinor,
    latlngGrid,
    mgrsGrid,
    gridStepDeg,
    hexMaxRes,
    hexLimitKm,
    dayNight,
    timeOfDay,
    hexLineWidth,
    contourMajorWidth,
    contourMinorWidth,
    hexLineColor,
    contourColor,
    gridColor,
    gridWidth,
    mgrsColor,
    symbolDetail,
    preciseMove,
    basemapSources,
    basemap,
    hasTiles,
    onBasemapError,
  }
}
