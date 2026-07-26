---
task: "#84 油料消耗（移動）"
status: DONE
started: 2026-07-26T02:00+08:00
updated: 2026-07-26T03:00+08:00
agent: Opus 4.8
spec: SPEC_FULL §5.3（「油料耗盡無法移動」MUST）、SPEC_MOVEMENT §2.4
---

# #84 油料：機械化部隊會燒油、會拋錨

## 設計（沿用彈藥既有模式，免 DB migration）
- **存放**：`EquipmentInstance.currentState["fuel"]`（SPEC_FULL §5.3 明定；JSON 欄位 → 無需 prisma migrate）。
- **惰性滿油**：instance 尚無 `fuel` 鍵 → 視為滿油（取範本 `mobility.fuel_capacity`）。免額外 seed pass，
  且日後由「裝備管理」面板新增的載具自動帶滿油；session 複製（#79）沿用 currentState → 自動帶油。
- **油池**：單位的自走載具（TRACKED/WHEELED）視為共同油池，**各車燒各車的油**（依自身油耗），
  `quantity`（#30 建制數量）放大容量與油耗。徒步/無油料資料 → `needs_fuel=False`，完全不受限。
- **每 tick**：查油 → **以剩餘油量夾住本 tick 可行距離**（開到沒油就停在那裡，不會超跑）→ 推進 →
  依**實際位移**扣油、寫回 DB + 熱狀態 `fuel`。
- **油盡**：`OrderStatus.COMPLETED` + `MOVE_HALTED_FUEL` 事件（同 #81 `MOVE_BLOCKED` 機制）。
  補給後需**重下 MOVE 令**才會再動。

## 交付
- **契約先行**：`contracts/weaponeering.schema.json` mobility $def +`fuel_capacity` +`fuel_burn_per_km`；
  舊 `fuel_burn_per_tick` 標 deprecated（#81 起速度隨地形變動，per-tick 油耗已無物理意義）。
- **`movement/fuel.py`（新）**：`load_unit_fuel`（惰性滿油、quantity 放大）、`burn_fuel`（按車扣、
  寫回 currentState、floor 0）、`UnitFuel.range_km()`。
- **`movement/mobility.py`**：`UnitMobility` +`fuel_capacity`/`needs_fuel`；`_fuel_burn_per_km()` 以
  越野速度把舊 per-tick 資料換算為 per-km（不丟棄既有資料）。
- **`engine/movement.py`**：每 tick 查油/夾距離/扣油/寫回；`_halt_out_of_fuel`（MOVE_HALTED_FUEL，
  detail 帶 profile/fuel_remaining/fuel_burn_per_km/座標）。`_fuel_burn_km=0`（徒步）→ 完全略過油料處理。
- **seed 資料**：MBT 1900/4.5、IFV 660/1.65、SP 榴 510/1.5、MLRS 600/1.25（≈實裝續航 350–480km）。
- **預覽**：`fuel_cost` 改用**實際編裝油耗**（原為 1.0/km 佔位假值）+ 新增 `fuel_remaining`/`fuel_sufficient`。
- **AI**：`UnitMeta.range_km` → context `range_km` + briefing「（剩餘行程 N km）」+ decider 指示
  「勿下超出剩餘行程的目的地」。**給 LLM 公里數而非抽象油量**，與既有速度/可達距離敘述一致。

## 修正（survey 指出）
- 事件名用 **`MOVE_HALTED_FUEL`**（非 `HALTED_FUEL`）：與 dead `movement/system.py`、SPEC_MOVEMENT、
  TASKS 一致，且同 `MOVE_*` 前綴。**關鍵**：broadcaster 不送 `detail` 上 WS，故必須用獨立 event_type，
  否則 COP 戰況列無法區分「不可通行」與「沒油」。
- 彈藥路徑有個**不對稱缺陷**（純量 ammo 分支只扣熱狀態、不寫回 DB）；油料**每條路徑都寫回 DB**，不複製該缺陷。

## 測試
- 新 `test_movement_fuel.py` 9：惰性滿油、扣油寫回+floor 0、徒步免油、行進中耗油、**油盡拋錨停駛**、
  空油箱不出發、徒步不受影響、AI briefing 顯示/不顯示剩餘行程。
- gates：**pytest 1044 passed / 8 skipped**、**golden 6 未破**、ruff/mypy(198)/schema-sync 綠。

## 未做（記錄）
- **補給（resupply）加油**：本卡只做消耗；logistics 子系統仍 NoOp，加油需另卡（可經「裝備管理」面板
  手動改 currentState.fuel 暫代）。
- 前端未顯示油量/剩餘行程（後端已回 `fuel_remaining`/`fuel_sufficient`，COP 尚未渲染）。
- `UnitView` 未加 fuel（比照彈藥：走 equipment view + 熱狀態 STATE_DIFF，非 UnitView）。

## 中斷續作指引
- 已完成並全綠。剩：容器重建 + live 驗證 → PROGRESS/TASKS 更新。
