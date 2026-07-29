/**
 * COP 的地圖編輯器（stage ③b，#11/#26/#43/#92/#96/#99）——標註/工事/武器據點的
 * 繪製、選取、屬性編輯、整形、旋轉、刪除，以及地形裁切射界。
 *
 * 幾何一變，已套用的地形裁切環（`attributes.viewshed_ring`）就失效：**所有會動到幾何的
 * 路徑（整形/移動/旋轉/改射界參數）都必須在同一個 PATCH 裡把它清掉**，否則地圖上會留著
 * 一個依舊幾何算出來的射界——看起來完全正常，只是錯的。這條紀律散在五個函式裡，
 * 集中到本模組正是為了讓它們待在一起。
 *
 * 權限在前端先 gate（`mayEditSelectedFeature`，與後端 `_feature_for_edit` 同一條規則），
 * 但**後端才是權威**——這裡只是避免使用者拖完才吃 403。
 */
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  createMapFeature,
  deleteMapFeature,
  draftToFc,
  editMapFeature,
  featureLineWidth,
  featureSymbolFc,
  featureZoneClass,
  featuresToFc,
  fetchMapFeatures,
  fetchTerrainFootprint,
  influenceToFc,
  MIN_VERTICES,
  openRing,
  removeVertex,
  rotatePoints,
  shapeToPolygon,
  DEFAULT_FEATURE_WIDTH,
  FEATURE_KINDS,
  type DraftKind,
  type FeatureCreate,
  type MapFeature,
} from '~/composables/useMapFeatures'
import { fetchEquipmentTemplates, type EquipmentTemplate } from '~/composables/useEquipment'

export function useMapEditor(opts: {
  sessionId: Ref<string>
  viewpoint: Ref<string>
  canControl: ComputedRef<boolean>
  myFaction: Ref<string>
  hiddenFeatureIds: Ref<string[]>
  toasts: ReturnType<typeof useToasts>
  /** 選到圖形時的副作用（頁面用來把「地圖編輯」小工具叫出來，#26）。 */
  onFeaturePicked?: () => void
}) {
  const { sessionId, viewpoint, canControl, myFaction, hiddenFeatureIds, toasts } = opts

  const mapFeatures = ref<MapFeature[]>([])
  const drawKind = ref<DraftKind | null>(null)
  const drawFeatureKind = ref('OBSTACLE')
  const drawWeaponTemplate = ref('')
  const draftCoords = ref<number[][]>([])
  // 繪製屬性（#11）：名稱/顏色/備註/高度（障礙/建築預設 2m）/北約符號/線寬。
  const drawLabel = ref('')
  const drawColor = ref('')
  const drawNotes = ref('')
  const drawHeight = ref<number | null>(null)
  const drawSidc = ref('')
  const drawWidth = ref(DEFAULT_FEATURE_WIDTH)
  const selectedFeatureId = ref<string | null>(null)
  // 選取特徵的編輯欄位（#11）
  const editFeatLabel = ref('')
  const editFeatColor = ref('')
  const editFeatOwner = ref('') // #92 歸屬陣營（僅全知可改；'' = 不變更）
  const editFeatZone = ref('') // WP-A3 禁射級別（只對面有意義）
  const editFeatWidth = ref(DEFAULT_FEATURE_WIDTH)
  const editFeatNotes = ref('')
  const editFeatHeight = ref<number | null>(null)
  const editFeatSidc = ref('')
  // 武器射向/雷達扇區（#11 C）：射程(m) + 方向(度) + 張角(度，360=全向)。
  const editFeatRange = ref<number | null>(null)
  const editFeatDir = ref(0)
  const editFeatArc = ref(360)
  // 選取當下的射程/方向/張角（供儲存時判斷是否真的改動 → 才失效地形裁切環）。
  const origRange = ref<number | null>(null)
  const origDir = ref(0)
  const origArc = ref(360)
  // 地形裁切（#11）：feature id → viewshed 環（後端逐方位 LOS）；套用中旗標。
  const terrainClips = ref<Record<string, number[][]>>({})
  const clipBusy = ref(false)
  const weaponTemplates = ref<EquipmentTemplate[]>([])

  const canDraw = computed(() => canControl.value || !!myFaction.value)
  const drawActive = computed(() => drawKind.value !== null)

  // #12 元素顯隱：地圖只渲染未隱藏的特徵（清單仍列全部，供切換）。
  const shownFeatures = computed(() =>
    mapFeatures.value.filter((f) => !hiddenFeatureIds.value.includes(f.id)),
  )
  const featureFc = computed(() => featuresToFc(shownFeatures.value))
  const featSymbol = computed(() => featureSymbolFc(shownFeatures.value)) // 北約符號點特徵（#11）
  const influenceFc = computed(() => influenceToFc(shownFeatures.value, terrainClips.value))
  const draftFc = computed(() => draftToFc(drawKind.value, draftCoords.value))
  const drawableKinds = computed(() =>
    FEATURE_KINDS.filter((k) => k.value !== 'WEAPON_EMPLACEMENT'),
  )
  const selectedFeature = computed(
    () => mapFeatures.value.find((f) => f.id === selectedFeatureId.value) ?? null,
  )

  // 標註套用（WP-E3 抽出）：快照與單獨重載共用同一份，避免兩處各自還原裁切環而漂移。
  function applyFeatures(features: MapFeature[]) {
    mapFeatures.value = features
    // 還原已持久化的地形裁切環（#43）：裁切射界存在 attributes.viewshed_ring，重新整理後仍在。
    const clips: Record<string, number[][]> = {}
    for (const f of mapFeatures.value) {
      const ring = (f.attributes as Record<string, unknown> | undefined)?.viewshed_ring
      if (Array.isArray(ring) && ring.length >= 3) clips[f.id] = ring as number[][]
    }
    terrainClips.value = clips
  }

  async function loadFeatures() {
    // #92：帶視角 → 後端只回「共同 + 該陣營」的標註（過濾在後端，紅線 #3）。
    applyFeatures(await fetchMapFeatures(sessionId.value, viewpoint.value || null).catch(() => []))
  }
  async function ensureWeaponTemplates() {
    if (!weaponTemplates.value.length) {
      weaponTemplates.value = await fetchEquipmentTemplates().catch(() => [])
    }
  }

  // ---- 繪製 ----
  function startDraw(kind: DraftKind, featureKind: string) {
    selectedFeatureId.value = null
    armReshape(null) // #99b 開始繪製 → 收掉上一個物件的控制點
    drawFeatureKind.value = featureKind
    drawKind.value = kind
    draftCoords.value = []
    drawLabel.value = ''
    drawColor.value = ''
    drawNotes.value = ''
    drawSidc.value = ''
    // 障礙/建築預設高度 2m（#11）。
    drawHeight.value = featureKind === 'OBSTACLE' || featureKind === 'BUILDING' ? 2 : null
  }
  async function startWeaponDraw() {
    await ensureWeaponTemplates()
    startDraw('POINT', 'WEAPON_EMPLACEMENT')
  }
  function cancelDraw() {
    drawKind.value = null
    draftCoords.value = []
  }
  /** 地圖點擊時累積頂點（POINT 一點完成；CIRCLE/RECTANGLE 兩點完成；線/面累積至「完成」）。 */
  function addDraftPoint(lng: number, lat: number) {
    if (drawKind.value === 'POINT') {
      draftCoords.value = [[lng, lat]]
      void finishDraw()
      return
    }
    draftCoords.value = [...draftCoords.value, [lng, lat]]
    if (
      (drawKind.value === 'CIRCLE' || drawKind.value === 'RECTANGLE') &&
      draftCoords.value.length >= 2
    ) {
      void finishDraw() // 中心+邊 / 兩對角
    }
  }
  async function finishDraw() {
    if (!drawKind.value || !draftCoords.value.length) {
      cancelDraw()
      return
    }
    const isWeapon = drawFeatureKind.value === 'WEAPON_EMPLACEMENT'
    const tmpl = isWeapon
      ? weaponTemplates.value.find((t) => t.id === drawWeaponTemplate.value)
      : null
    const range = Number((tmpl?.base_stats as Record<string, unknown> | undefined)?.max_range_m)
    // 圓/矩存為 POLYGON（環由中心+邊 / 兩對角導出）；其餘照舊。
    const isShape = drawKind.value === 'CIRCLE' || drawKind.value === 'RECTANGLE'
    const ring = isShape ? shapeToPolygon(drawKind.value, draftCoords.value) : null
    const attrs: Record<string, unknown> = {}
    if (drawColor.value) attrs.color = drawColor.value
    if (drawWidth.value !== DEFAULT_FEATURE_WIDTH) attrs.width = drawWidth.value
    if (drawNotes.value.trim()) attrs.notes = drawNotes.value.trim()
    if (drawHeight.value != null) attrs.height_m = drawHeight.value
    if (drawSidc.value && drawKind.value === 'POINT') attrs.sidc = drawSidc.value
    const body: FeatureCreate = {
      kind: drawFeatureKind.value,
      geometry_type: isShape ? 'POLYGON' : drawKind.value,
      geometry: isShape
        ? ring
        : drawKind.value === 'POINT'
          ? draftCoords.value[0]
          : draftCoords.value,
      label: drawLabel.value.trim() || tmpl?.name || null,
      // #92 歸屬：套了陣營視角時，繪出的標註歸該陣營（否則全知繪製一律落 WHITE_CELL 共同層，
      // 等於白軍替某軍畫的東西全體都看得到）。一般角色不帶，後端一律歸本軍。
      owner_faction: viewpoint.value || null,
      weapon_template_id: isWeapon ? drawWeaponTemplate.value || null : null,
      influence_radius_m: isWeapon && Number.isFinite(range) ? range : null,
      attributes: Object.keys(attrs).length ? attrs : undefined,
    }
    try {
      await createMapFeature(sessionId.value, body)
      await loadFeatures()
      toasts.push({ severity: 'success', title: '已新增地圖標註', timeoutMs: 2500 })
    } catch (e) {
      toasts.push({
        severity: 'error',
        title: '新增標註失敗',
        detail: (e as { message?: string }).message,
        timeoutMs: 0,
      })
    }
    cancelDraw()
  }

  // ---- 選取 + 編輯欄位載入 ----
  function onFeatureClick(e: { id: string }) {
    selectedFeatureId.value = e.id
    // #26 點地圖物件即跳出「地圖編輯」小工具的編輯工具列（若有繪製權）。
    if (canDraw.value) opts.onFeaturePicked?.()
    const f = mapFeatures.value.find((x) => x.id === e.id)
    const a = (f?.attributes ?? {}) as Record<string, unknown>
    editFeatLabel.value = f?.label ?? ''
    editFeatColor.value = typeof a.color === 'string' ? a.color : ''
    editFeatOwner.value = f?.owner_faction ?? ''
    editFeatZone.value = f ? featureZoneClass(f) : ''
    editFeatWidth.value = f ? featureLineWidth(f) : DEFAULT_FEATURE_WIDTH
    editFeatNotes.value = typeof a.notes === 'string' ? a.notes : ''
    editFeatHeight.value = typeof a.height_m === 'number' ? a.height_m : null
    editFeatSidc.value = typeof a.sidc === 'string' ? a.sidc : ''
    editFeatRange.value = typeof f?.influence_radius_m === 'number' ? f.influence_radius_m : null
    editFeatDir.value = typeof a.direction_deg === 'number' ? a.direction_deg : 0
    editFeatArc.value = typeof a.arc_deg === 'number' ? a.arc_deg : 360
    origRange.value = editFeatRange.value
    origDir.value = editFeatDir.value
    origArc.value = editFeatArc.value
  }

  // ---- 地形裁切射界（#11/#43）----
  async function applyTerrainClip() {
    const f = selectedFeature.value
    const range = editFeatRange.value
    if (!f || !range || range <= 0) return
    const g = f.geometry as unknown
    const center = f.geometry_type === 'POINT' ? (g as number[]) : ((g as number[][])?.[0] ?? null)
    if (!center) return
    const arc = editFeatArc.value
    clipBusy.value = true
    try {
      const fp = await fetchTerrainFootprint(sessionId.value, {
        origin: [center[0]!, center[1]!],
        max_range_m: range,
        direction_deg: arc < 360 ? editFeatDir.value : null,
        arc_deg: arc < 360 ? arc : 360,
        steps: arc < 360 ? 24 : 36,
        observer_height_m: 10,
        target_height_m: 2, // 目標/障礙離地高 default 2m（#11）
      })
      if (fp.ring.length >= 3) {
        terrainClips.value = { ...terrainClips.value, [f.id]: fp.ring as number[][] }
        // 持久化到 attributes.viewshed_ring（#43）：重新整理後仍保留裁切；失敗僅保留本地。
        try {
          await editMapFeature(sessionId.value, f.id, { attributes: { viewshed_ring: fp.ring } })
        } catch {
          // 持久化失敗不擋操作：本地裁切仍生效，僅無法存活重新整理。
        }
        toasts.push({
          severity: fp.clipped ? 'success' : 'info',
          title: fp.clipped ? '已套用地形裁切（射界受稜線遮蔽）' : '地形裁切：此扇區全通視',
          timeoutMs: 2500,
        })
      }
    } catch (err) {
      toasts.push({
        severity: 'warn',
        title: '地形裁切不可用',
        detail: '地形服務未就緒，改用理想射界。' + ((err as { message?: string }).message ?? ''),
        timeoutMs: 4000,
      })
    } finally {
      clipBusy.value = false
    }
  }
  function clearTerrainClip(fid: string) {
    if (!(fid in terrainClips.value)) return
    const next: Record<string, number[][]> = {}
    for (const [k, v] of Object.entries(terrainClips.value)) {
      if (k !== fid) next[k] = v
    }
    terrainClips.value = next
  }
  // 使用者按「還原理想射界」（#43）：本地移除 + 持久化清除，才不會重新整理又跑回來。
  async function onClearTerrainClip(fid: string) {
    clearTerrainClip(fid)
    try {
      await editMapFeature(sessionId.value, fid, { attributes: { viewshed_ring: null } })
    } catch {
      // 清除持久化失敗不擋操作；地圖已即時還原理想射界。
    }
  }

  // ---- 編修權與整形解鎖（#99）----
  /**
   * 本使用者對選取圖形**有沒有編修權**（與後端 `_feature_for_edit` 同一條規則：
   * 全知可編任一；否則只能編本軍的，**共同層 WHITE_CELL 標註對一般指揮官是唯讀**）。
   * 有權 ≠ 現在可拖，見 `reshapeArmedId`。
   */
  const mayEditSelectedFeature = computed(() => {
    const f = selectedFeature.value
    if (!f || !canDraw.value) return false
    if (canControl.value) return true
    return !!myFaction.value && f.owner_faction === myFaction.value
  })
  /**
   * #99b 整形須先「解鎖」：只有經右鍵選單（或編輯面板按鈕）明確進入調整狀態的那一個圖形
   * 才畫控制點、才吃拖曳。**單純點選不解鎖**——否則在圖上點一下再手滑就把標註拖歪了，
   * 而地圖上點選是最頻繁的操作。換選別的圖形、取消選取、開始繪製都會自動上鎖。
   */
  const reshapeArmedId = ref<string | null>(null)
  const canEditSelectedFeature = computed(
    () => mayEditSelectedFeature.value && reshapeArmedId.value === selectedFeatureId.value,
  )
  function armReshape(id: string | null) {
    reshapeArmedId.value = id
  }
  watch(selectedFeatureId, (id) => {
    if (id !== reshapeArmedId.value) reshapeArmedId.value = null // 換對象/取消選取 → 自動上鎖
  })

  // ---- 整形 / 移動 / 旋轉 / 刪除 ----
  /**
   * #99 整形落地：拖頂點/中點/本體後 PATCH 新幾何。
   * **失敗也要 loadFeatures**：地圖上此刻顯示的是拖曳後的本地預覽，不重載就會停在一個
   * 伺服器沒接受的形狀。
   */
  async function onFeatureReshape(e: { id: string; geometry: number[][] }) {
    if (!e.geometry?.length) return
    try {
      await editMapFeature(sessionId.value, e.id, {
        geometry: e.geometry,
        attributes: { viewshed_ring: null },
      })
      clearTerrainClip(e.id)
      await loadFeatures()
    } catch (err) {
      toasts.push({
        severity: 'error',
        title: '調整形狀失敗',
        detail: (err as { message?: string }).message,
        timeoutMs: 0,
      })
      await loadFeatures() // 還原成伺服器上的權威幾何
    }
  }
  /**
   * #99 刪除線/面的一個控制點。低於最少頂點數（線 2、面 3）則拒絕並說明——
   * 硬刪下去會變成退化幾何（2 點的面 `toGeometry` 回 null，該標註會直接從地圖上消失）。
   * 兩條入口共用：右鍵選單、Alt＋點控制點。
   */
  async function deleteVertexAt(index: number, featureId?: string) {
    const f = featureId
      ? (mapFeatures.value.find((x) => x.id === featureId) ?? null)
      : selectedFeature.value
    if (!f) return
    const ring = openRing((f.geometry as number[][]) ?? [])
    const next = removeVertex(ring, index, f.geometry_type)
    if (!next) {
      toasts.push({
        severity: 'warn',
        title: '無法刪除控制點',
        detail: `${f.geometry_type === 'POLYGON' ? '面' : '線'}至少需要 ${MIN_VERTICES[f.geometry_type] ?? 2} 個控制點`,
        timeoutMs: 3000,
      })
      return
    }
    await onFeatureReshape({ id: f.id, geometry: next })
  }
  /** #99c Alt＋點控制點 → 刪除該頂點（免開選單的快捷路徑）。 */
  async function onFeatureVertexDelete(e: { id: string; index: number }) {
    await deleteVertexAt(e.index, e.id)
  }

  // 拖放移動點特徵（#11 B2）：MapCanvas emit 新座標 → PATCH 幾何 → 重載。
  async function onFeatureMove(e: { id: string; lng: number; lat: number }) {
    const f = mapFeatures.value.find((x) => x.id === e.id)
    if (!f || f.geometry_type !== 'POINT') return
    try {
      await editMapFeature(sessionId.value, e.id, {
        geometry: [e.lng, e.lat],
        attributes: { viewshed_ring: null },
      })
      clearTerrainClip(e.id)
      await loadFeatures()
    } catch (err) {
      toasts.push({
        severity: 'error',
        title: '移動失敗',
        detail: (err as { message?: string }).message,
        timeoutMs: 0,
      })
    }
  }
  // #26 旋轉選取的物件：武器扇區點→調方向角；面/線→頂點繞質心旋轉。
  async function rotateFeature(deg: number) {
    const f = selectedFeature.value
    if (!f) return
    if (f.geometry_type === 'POINT') {
      editFeatDir.value = (((editFeatDir.value + deg) % 360) + 360) % 360
      if (editFeatArc.value >= 360) editFeatArc.value = 90 // 全向圓→轉成可見扇形才看得到方向
      await saveFeatureEdit()
      return
    }
    const g = f.geometry as number[][]
    if (!Array.isArray(g) || g.length < 2) return
    try {
      clearTerrainClip(f.id)
      await editMapFeature(sessionId.value, f.id, {
        geometry: rotatePoints(g, deg),
        attributes: { viewshed_ring: null },
      })
      await loadFeatures()
    } catch (err) {
      toasts.push({
        severity: 'error',
        title: '旋轉失敗',
        detail: (err as { message?: string }).message,
        timeoutMs: 0,
      })
    }
  }
  async function saveFeatureEdit() {
    const fid = selectedFeatureId.value
    if (!fid) return
    const f = mapFeatures.value.find((x) => x.id === fid)
    const attrs: Record<string, unknown> = { ...((f?.attributes ?? {}) as Record<string, unknown>) }
    if (editFeatColor.value) attrs.color = editFeatColor.value
    else delete attrs.color
    if (editFeatWidth.value !== DEFAULT_FEATURE_WIDTH) attrs.width = editFeatWidth.value
    else delete attrs.width
    if (editFeatNotes.value.trim()) attrs.notes = editFeatNotes.value.trim()
    else delete attrs.notes
    if (editFeatHeight.value != null) attrs.height_m = editFeatHeight.value
    else delete attrs.height_m
    if (editFeatSidc.value) attrs.sidc = editFeatSidc.value
    else delete attrs.sidc
    // WP-A3：禁射級別。清空即移除該鍵——但 PATCH 對 attributes 是 merge，刪不掉鍵，
    // 故以 null 明示「取消」，後端 merge 後值為 null，`no_strike.py` 只認非空字串故等同無效。
    if (editFeatZone.value) attrs.zone_class = editFeatZone.value
    else attrs.zone_class = null
    // 武器射向/雷達扇區（#11 C）：張角 <360 才存方向/張角（否則全向圓）。
    if (editFeatArc.value > 0 && editFeatArc.value < 360) {
      attrs.direction_deg = editFeatDir.value
      attrs.arc_deg = editFeatArc.value
    } else {
      delete attrs.direction_deg
      delete attrs.arc_deg
    }
    // 只有射程/方向/張角真的變動才失效地形裁切環；否則（改名/顏色/備註）保留已套用的裁切（#43）。
    const arcChanged =
      editFeatRange.value !== origRange.value ||
      editFeatDir.value !== origDir.value ||
      editFeatArc.value !== origArc.value
    if (arcChanged) attrs.viewshed_ring = null
    try {
      const ownerChanged =
        canControl.value && !!editFeatOwner.value && editFeatOwner.value !== f?.owner_faction
      await editMapFeature(sessionId.value, fid, {
        label: editFeatLabel.value.trim() || null,
        influence_radius_m: editFeatRange.value,
        attributes: attrs,
        // 僅全知且確實變更才送——一般角色帶此欄後端會 403，不該因為存個名稱就撞上。
        ...(ownerChanged ? { owner_faction: editFeatOwner.value } : {}),
      })
      if (arcChanged) clearTerrainClip(fid)
      origRange.value = editFeatRange.value
      origDir.value = editFeatDir.value
      origArc.value = editFeatArc.value
      await loadFeatures()
      toasts.push({ severity: 'success', title: '已更新標註', timeoutMs: 2000 })
    } catch (e) {
      toasts.push({
        severity: 'error',
        title: '更新失敗',
        detail: (e as { message?: string }).message,
        timeoutMs: 0,
      })
    }
  }
  async function removeFeature(fid: string) {
    try {
      await deleteMapFeature(sessionId.value, fid)
      if (selectedFeatureId.value === fid) selectedFeatureId.value = null
      await loadFeatures()
    } catch (e) {
      toasts.push({
        severity: 'error',
        title: '刪除失敗',
        detail: (e as { message?: string }).message,
        timeoutMs: 0,
      })
    }
  }

  return {
    mapFeatures,
    drawKind,
    drawFeatureKind,
    drawWeaponTemplate,
    draftCoords,
    drawLabel,
    drawColor,
    drawNotes,
    drawHeight,
    drawSidc,
    drawWidth,
    selectedFeatureId,
    selectedFeature,
    editFeatLabel,
    editFeatColor,
    editFeatOwner,
    editFeatZone,
    editFeatWidth,
    editFeatNotes,
    editFeatHeight,
    editFeatSidc,
    editFeatRange,
    editFeatDir,
    editFeatArc,
    terrainClips,
    clipBusy,
    weaponTemplates,
    canDraw,
    drawActive,
    shownFeatures,
    featureFc,
    featSymbol,
    influenceFc,
    draftFc,
    drawableKinds,
    mayEditSelectedFeature,
    canEditSelectedFeature,
    reshapeArmedId,
    applyFeatures,
    loadFeatures,
    ensureWeaponTemplates,
    startDraw,
    startWeaponDraw,
    cancelDraw,
    addDraftPoint,
    finishDraw,
    onFeatureClick,
    applyTerrainClip,
    clearTerrainClip,
    onClearTerrainClip,
    armReshape,
    onFeatureReshape,
    deleteVertexAt,
    onFeatureVertexDelete,
    onFeatureMove,
    rotateFeature,
    saveFeatureEdit,
    removeFeature,
  }
}
