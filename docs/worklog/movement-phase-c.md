---
task: "#82 移動真實化 Phase C — 地形 A* 繞路 + 任意點位起終點 + 預覽/執行統一"
status: DONE（路由核心）；道路/油料未做見「未完成」
started: 2026-07-26T00:00+08:00
updated: 2026-07-26T01:30+08:00
agent: Opus 4.8
spec: SPEC_MOVEMENT.md §2.3/§5(C)
---

# #82 Phase C：沿地形路徑繞行（不再直線穿越河流/山脈）

## 交付
- **`movement/router.py`（新）**：`plan_route(path_fn, start/dest, profile) → PlannedRoute`。
  - **任意點位起終點（使用者特別提醒的重點，SPEC §2.3 MUST）**：路徑 = 精確起點 →（A* **中間**格心）
    → **精確終點**。丟掉首格（單位已在格內某精確位置，不倒退回格心）與末格（用精確目的地，
    **不被 `latlng_to_cell` 吸附到格心**）→ 首末自然成為「部分格」幾何段。
  - 起訖同格 / 路徑僅 2 跳（相鄰格）→ 退化為單段精確直線，不強制繞經格心。
  - 不可達（含超出 hex 快取範圍）/ 服務中斷 → **退回直線 + reason**（不否決移動，避免長距離誤拒）。
- **執行器**（`engine/movement.py`）：admit 時規劃 → 存 `payload._route_wp` → 沿用既有多段（leg）
  推進器前進；`_targets` 優先序 = 使用者自訂 waypoints > `_route_wp` > 單一目的地。規劃後**重算
  targets**，使行軍耗損依**實際繞行距離**計（繞遠路更耗）。事件：`MOVE_ROUTE_PLANNED` /
  `MOVE_ROUTE_FALLBACK`（可觀測退回原因）。
- **統一預覽/執行**（SPEC 驗收）：`api/movement.preview` 與執行器共用同一 `plan_route` → 預覽畫的線
  ＝實際會走的線；回傳加 `terrain_routed`。
- **DI 化路徑來源**：`deps.get_movement_path_fn`（STUB_GATEWAY → None）。**修測試污染**：預覽單元測試
  原會實際打到本機 terrain 容器（開/關容器結果不同）→ 改以 DI 覆寫為 None，回復決定性。

## 測試
- 新 `test_movement_router.py` 7（純函數）：**精確終點非格心**、**首段由精確位置出發**、中間格心途經、
  同格直線、相鄰格退化、不可達退回、服務中斷退回。
- 新 `test_movement_routing.py` 5（執行器）：規劃並實際偏離直線（繞北）、**最終停在精確目的地非格心**、
  不可達退回+記事件、使用者自訂 waypoints 不被覆寫、無 path_fn ＝ Phase B 行為。
- gates：**pytest 1037 passed / 8 skipped**、**golden 6 未破**、ruff/mypy(197)/schema-sync 綠。

## 未完成（誠實記錄；不在本卡交付）
- **道路網（road-following）＝資料阻擋，非程式問題**：`mobility_matrix` 無 `ROAD` class；terrain 的
  `terrain_class` 目前**只由坡度+高程**導出（`hexgrid.classify_terrain` 明載 URBAN/FOREST 需 OSM
  土地利用，屬 terrain 模組 Phase 2）；`MATSO_ROAD_GRAPH_PATH`（taiwan_drive.graphml）config 標
  **「尚未使用」**。→ 需先做 OSM 土地利用/道路網 ingestion（另開卡），否則「沿道路加速」無資料可依。
- **油料消耗 / 油盡 HALTED_FUEL**：未做（SEED_VEHICLES 有 `fuel_burn_per_tick`，`UnitMobility` 已留
  `fuel_burn_per_km` 欄位）。
- **hex 格網覆蓋擴大 / on-demand 建格**：未做；本卡以「不可達→退回直線」消除**誤拒**症狀（使用者可
  移動），但超範圍區域仍不會繞路。
- **`GetPath.eta_ticks` 真實化**：未做（執行端已自行以 per-unit 速度算 ETA，該佔位值僅用於 precheck
  說明文字）。
- 前端未動：預覽已回 `terrain_routed`/`terrain_impassable`，COP 尚未顯示「已繞路/不可通行」提示。

## 中斷續作指引
- 路由核心已完成並全綠。剩：容器重建 + live 驗證 → PROGRESS/TASKS 更新。
- 後續卡建議：**#83 道路網與土地利用 ingestion（OSM）**、**#84 油料消耗**、**#85 hex 格網覆蓋**。
