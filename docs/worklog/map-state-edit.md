---
task: "#55 地圖狀態編輯"
status: DONE
started: 2026-07-24T05:30+08:00
updated: 2026-07-24T06:05+08:00
agent: Opus 4.8
---

# #55 地圖狀態編輯（COP 布局：拖放單位 + 繪障礙，完成再開始兵推）

## 目標摘要
在 COP 讓白軍/導演暫停推演、直接拖放編輯單位位置、用既有地圖編輯器繪障礙/建築，布局完成後按
「開始兵推」恢復。障礙繪製與地圖特徵拖曳早已存在（#12/#26/#11）；新做的是**單位直接拖放定位**與
**編輯模式（暫停→布局→開始）**。

## 設計決策
- **編輯模式＝暫停**：進入時 POST /control PAUSE，完成 POST /control RESUME（「完成後再開始兵推」）。
- **限白軍/導演**（is_omniscient）：編輯任一陣營位置是布局動作（god setup）。
- **single-writer 紅線**（同 #52）：sim 的 RedisHotState mirror 忽略外部寫 → 座標編輯走**命令通道**
  （`live_position`），sim 迴圈 pre_tick drain 套自己的 hot；暫停中編輯 → RESUME 後第一 tick 生效。
  座標同時寫 DB（權威，供顯示/reconnect/seed）。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/state/live_position.py | 新增 | pos_cmd 通道（push/drain/apply；後到覆寫、未 seed 略過） |
| core/app/api/units.py | 改 | POST /{sid}/units/{uid}/reposition（白軍 gate；寫 DB current_lat/lng + push_pos_cmd） |
| core/app/sim_runtime.py | 改 | _apply_live_edits 併入 drain/apply_pos_cmds |
| platform/.../MapCanvas.vue | 改 | editUnits prop + unitMove emit + 單位拖曳（重用 FEAT_DRAG 落點預覽） |
| platform/.../cop.vue | 改 | mapEditMode + enterMapEdit(PAUSE)/startWargame(RESUME)/onUnitMove(reposition→refetch)；header 入口 + 編輯工具列（開始兵推鈕） |
| core/tests/unit/test_live_position.py | 新增 | 4 tests（通道 roundtrip/apply/last-wins/skip-unseeded） |
| core/tests/unit/test_units_api.py | 改 | +2 tests（reposition 白軍 OK / commander 403） |

## 測試證據
- test_live_position（4）+ test_units_api reposition（2）+ live_ammo 迴歸 → 綠
- 前端 lint/typecheck → 綠

## 測試證據（補）
- mypy 全量 192 → Success；ruff 全量 → All checks passed
- reposition 路由於運行容器 OpenAPI 確認註冊；core 重建 healthy

## 完成 / 後續可強化
- **完成**：白軍於 COP 按「地圖狀態編輯」→ 暫停 → 拖單位定位 + 繪障礙 → 「開始兵推」恢復。
- **後續可強化**：拖曳時單位圖標即時跟隨（目前用落點預覽點，放開後 refetch 定位）；編輯模式內新增/刪除單位；ORBAT 面板數值化改座標。

## 追加：多選 + 框選整組移動（2026-07-24）
編輯模式下可一次移動多個單位：
- **Shift＋點單位**＝加入/移除多選（青色高亮環）；**Shift＋空白處拖曳**＝框選矩形範圍內單位（累加）；點空白（無 Shift）清空多選。
- 拖曳任一已選單位 → **整組依相同經緯位移平移**（拖曳中顯示多點落點預覽），放開後批次 reposition（並行 POST）再重載一次。
- 設計：MapCanvas 自持選取集 `selectedUnitIds` + `unit-multiselect-ring` 高亮層（`in` filter）+ `unit-multidrag` 預覽層 + 框選 rubber-band（掛地圖容器的 inline-style div）；emit `unitsMove{moves[]}` → cop `onUnitsMove` 批次 reposition。離開編輯模式自動清空多選。
- 檔案：MapCanvas.vue（多選/框選/整組拖曳 + 2 圖層 + 1 預覽源）、cop.vue（onUnitsMove + `@units-move` + 工具列提示 Shift/框選）。
- **瀏覽器實測**（e2e-orders，經 `window.__matsoMap` 派真事件驗證）：Shift 多選 filter 命中 2 單位 + 青環渲染；框選矩形選中範圍內 2 單位、點空白清空；整組拖曳後 DB 兩單位位移量完全一致（dLng 相同）；驗畢還原座標 + RESUME。前端 lint/typecheck 綠。

## 修正 + 增強（2026-07-24，使用者回報「Shift 點選/框選無作用、無法拖移」）
**根因（兩個）**：
1. **MapLibre 內建 boxZoom**：Shift+拖曳預設是「縮放框」，會攔截 trusted（真使用者）Shift 事件——
   我上一版用**非 trusted 合成事件**驗證（boxZoom 不觸發）故誤判為過。修法：編輯模式進入時
   `map.boxZoom.disable()`（離開還原）；並把 **Shift 多選/框選集中到通用 `map.on('mousedown')`**
   自行 queryRenderedFeatures 判定（不再依賴會被 boxZoom 干擾的 layer 委派事件）。
2. **cop.vue 模板 inline handler 帶 TS 型別註記** `@units-selected="(e: {count:number})=>…"`：vue-tsc 過、
   但**執行期模板編譯報錯**→ 整個 Cop 元件更新拋錯、地圖無法初始化（`isStyleLoaded` 恆 false）。
   修法：改用具名方法 `onUnitsSelected`。
**增強**：
- **「已選 N 個」徽章**：MapCanvas emit `unitsSelected{count}` → cop 於工具列顯示青色藥丸徽章。
- **整組拖曳圖標即時跟隨游標**：`syncUnits(posOverride)` 以覆寫座標重建 units 源，真圖標＋青環
  跟著游標移動（取代原「多點預覽點」，移除 unit-multidrag 層/源）；放開保持在落點待 refetch。
- **瀏覽器實測（trusted 事件，computer 工具）**：進編輯模式 `boxZoom` 已停用；Shift+點單位→選取
  filter 命中 + 青環 + 「已選 1 個」徽章渲染（截圖確認）——修好前 filter 恆空。地圖 201ms 正常載入。
  前端 lint/typecheck 綠。

## 再修：整組拖曳跨陣營無反應（2026-07-24，使用者回報「已選 3 個但拖動沒反應」）
**根因**：`myFaction = me.my_faction`；被指派陣營的導演（如 BLUE）視角下，**他陣營單位是
contact（敵情），不在 `props.ownUnits`**。但多選/框選可選任一 rendered 單位（含 contact），而舊
`startGroupDrag` / `onBoxUp` 只從 `props.ownUnits` 取座標 → 選了跨陣營單位時 origs 幾乎為空、
拖曳整組靜默 no-op（單拖已可動因 reposition 端點對全知放行任一單位）。編輯模式＝白軍神視角，
本就該能調任一陣營。**修法**：新增 `allUnitPositions()`（我方 + contact 皆納入）；`startGroupDrag`
origs、`onBoxUp` 框選、`syncUnits(override)` 即時跟隨全部改用之（含 contact 座標覆寫）。
lint/typecheck 綠；同陣營整組拖曳先前已驗（DB 同 delta），本修僅擴大納入集合、不影響既有路徑。
