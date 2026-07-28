---
task: "#85 補給（加油）+ 移動真實化前端顯示"
status: DONE
started: 2026-07-26T04:00+08:00
updated: 2026-07-26T05:00+08:00
agent: Opus 5
spec: SPEC_FULL §5.3、SPEC_MOVEMENT §2.4
---

# #85 補給加油 + 前端顯示（#80–#85 收尾）

## A. 補給（ResupplySystem）
**無契約變更**——`logistics.capacity{FUEL}`、`resupply_rate_per_tick`、`LogisticsSystem.consume()`
協定、`RESUPPLY` OrderType **全部早已存在**，缺的只有實作（kernel 掛的是 `NoOpLogisticsSystem`）。

- `engine/logistics.py`（新）`ResupplySystem` 取代 NoOp：補給單位（LOGISTICS 裝備、capacity.FUEL>0）
  於 **2km 內**對**同陣營**目標每 tick 撥交 `resupply_rate_per_tick`，直到加滿或載運耗盡。
- 事件：`RESUPPLY_TICK` / `RESUPPLY_COMPLETED` / `RESUPPLY_FAILED`
  （NO_TARGET / NOT_SAME_FACTION / NOT_A_SUPPLY_UNIT / CARGO_EMPTY / TARGET_NEEDS_NO_FUEL）。
- **超出距離不算失敗**：標 EXECUTING 等補給車自行以 MOVE 令開過來（符合實務：先機動再撥交）。
- `movement/fuel.py` +`refuel()` +`SupplyCargo`/`load_supply_cargo()`（載運油存
  `currentState["cargo_fuel"]`，惰性滿載、quantity 放大）——同 #84 免 migration 模式。
- seed `FUEL_TRUCK`（載油 10000、400/tick ≈ 5 分鐘加滿一輛 MBT）。
- AI 可下 RESUPPLY（bridge 原本當 NoOp 丟棄）。

### 測試找到的真 bug
油箱已乾的單位**仍被課行軍/強穿耗損**——它根本沒出發。修正：admit 時**先驗油**，油乾直接
`MOVE_HALTED_FUEL` 並跳過耗損計算。

## B. 前端顯示（#80–#85 一次補齊）
**契約先行**：`core_api.yaml` `MovementPreviewView` 補上後端早已回傳但契約漏宣告的 6 欄
（mobility_profile / speed_kmh / terrain_impassable / terrain_routed / fuel_remaining /
fuel_sufficient）→ `npm run gen:api` 重生型別。

- COP 移動預覽新增：機動 profile 中文標籤 + **實際速度**（含地形/坡度調變）、「地形繞路」標記、
  「⛔ 穿越不可通行地形」警告、油料剩餘 +「不足，將中途拋錨」警示。
- 單位資訊卡新增**活油料**列（讀 STATE_DIFF `fuel`；0 顯示「拋錨（需補給）」紅字）。
  無油料模型（徒步）→ 不顯示，避免誤導。

## 驗收
- 新 `test_logistics_resupply.py` 5：加滿、超距等待不失敗、拒補他軍、非補給單位失敗、
  **拋錨→補給→重下 MOVE 可再動（end-to-end headline）**。
- gates：pytest **1051 passed / 8 skipped**、**golden 6 未破**、ruff/mypy(199)/schema-sync/buf/
  前端 lint+typecheck 全綠。
- 實測（容器）：ResupplySystem 已掛 kernel、FUEL_TRUCK 10000/400；preview API 回全部 6 新欄；
  COP 頁面渲染正常、**無 console error**。

## 觀察（供調校，非 bug）
預覽某條 8km 山區路線得 `speed_kmh 0.9` / `537 tick`（≈9 小時）。係 `mobility_matrix` MOUNTAIN=3.0
× 坡度懲罰（FOOT slope_penalty=1.0，45°→×2）所致，模型自洽且對陡峭山地徒步而言合理；若嫌過慢，
`contracts/mobility_matrix.json` 為**想定可覆寫資料**，調 base/slope_penalty 即可。

## 未做
- 補給只做**油料**（capacity.AMMO/WATER_FOOD/BATTERY 尚未撥交）。
- 補給無 precheck/validator 專屬規則（距離與陣營由子系統執行期把關）。
