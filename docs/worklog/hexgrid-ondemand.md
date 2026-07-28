---
task: "#88 hex 格網覆蓋擴大／on-demand + #87 多類補給"
status: DONE
started: 2026-07-28T00:00+08:00
updated: 2026-07-28T01:00+08:00
agent: Opus 5
spec: SPEC_MOVEMENT §2.3/§3（地形覆蓋）、SPEC_FULL §4.2/§5.3
---

# #88 隨需補格 + #87 多類補給

## A. #88 隨需補格（on-demand hex cells）
**根因（實測）**：預建 parquet 快取只涵蓋 **lat 23.2–24.6 / lng 120.8–121.9**（26,375 格，1.2MB）。
範圍外 `get_cell` 回 `None` → A* 視為不可通行 → 長距離/跨區移動被誤拒（#82 現以退回直線容錯，
但該區完全不會繞路）。

**做法**：`HexGridCache` 可注入 `HexGridBuilder`（需 DTED）：快取未命中的 cell **當場由 DTED 計算**
並在行程內記憶化（不落 parquet）。`TerrainService.__init__` 於 DTED 與快取同時可用時自動注入。

- 未注入 builder（快取-only 部署）→ **行為與過去完全相同**（回 None），不破壞既有部署假設。
- 計算失敗（無 DTED／超出 DTED 範圍）→ 仍回 None，維持「不可通行」語義，不讓服務崩潰。
- `on_demand_count` 供觀測。

**實測（真 DTED，容器）**：
| 位置 | 結果 |
|---|---|
| 23.75/121.20（預建內） | MOUNTAIN slope 32.1° elev 2568m ✅ |
| 22.70/120.35（預建**外**，高雄） | GRASSLAND slope 0.8° elev 15m ✅（原本無資料） |
| 台南→高雄 A*（雙端皆在預建外） | **reachable=True, 15 hops, cost 18.1** ✅（原本直接不可達） |

## B. #87 多類補給（彈藥）
- 契約：`kinetic` +`basic_load`（預設 100，沿用既有配發慣例）——補彈補到此基準。
- `SupplyCargo` 由 FUEL 專用泛化為 **per-supply-class**（`cargo_fuel` / `cargo_ammo` 鍵，
  `cargo_key()` 產生）；`load_supply_cargo(db, unit, supply_class)`。
- `ResupplySystem` 每 tick **同時**撥交油料與彈藥（各自載運量/缺口/速率）；事件 detail 改回報
  `{fuel, ammo}`。seed `FUEL_TRUCK` 另載 5000 AMMO。
- **WATER_FOOD / BATTERY 刻意不接**：目前沒有任何消耗模型，撥交只是無效果的帳面數字。

## 驗收
- 新測試：terrain `test_on_demand_fills_cells_outside_prebuilt_bbox`（無 builder→None 舊行為、
  注入後補算、記憶化不重算）；resupply `test_resupplies_ammo_too`（補到 basic_load 且油也加）。
- gates：**pytest 1053 passed / 8 skipped**、**golden 6 未破**、ruff/mypy(199)/schema-sync/buf/
  前端 lint+typecheck 全綠；terrain 容器重建並實測。

## 未做（明確記錄）
- **`GetPath.eta_ticks` 未改真實化**：core 端（preview/executor）已用 per-unit 速度 × 地形算出
  權威 ETA；若在 terrain 端另建一套速度模型，等於**重蹈「三套不一致模型」覆轍**（正是 #80–#82
  要消滅的問題）。故維持 terrain eta 為成本粗估、**core 為權威**，並於此記錄該決定。
- 隨需格**不落 parquet**（僅行程內記憶化）：重啟後重算。若要永久化可加背景寫回，屬後續。
- #83（OSM 道路網/土地利用）未做——資料已確認可用（見下），另卡處理。
