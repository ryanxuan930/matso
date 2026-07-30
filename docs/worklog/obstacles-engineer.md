---
task: V2.1 WP-C2
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C2 障礙工事與工兵裁決

## 目標摘要

讓障礙從「地圖上一塊會扣血的區域」變成**有型別語意的東西**：雷區炸人並把縱隊釘住、
鐵絲網/戰車壕實質阻擋、斷橋讓道路加速失效；工兵可以破障（BREACH）與設障（EMPLACE），
兩者都要付出工時。中性預設保證既有局位元不變。

## 動手前先查證：規格說「完全無視」並不精確

SPEC_V2 §4 寫「移動 A* 與交戰完全無視它」。**不是**——`movement/attrition.py` 的
`classify_crossings` + `_apply_forced_attrition`（#28）早就讓「強穿阻礙」付出隨機額外耗損。

真正缺的是**型別語意**：對引擎而言，一片雷區與一圈鐵絲網是同一個東西。本卡補的是這一層。
（規格文字已一併修正。）

## 執行紀錄

- 建 `core/app/adjudication/obstacles.py`（純函數）：5 種型別 × (速度倍率, 每公里觸雷機率, 破障工時)。
- 擴 `movement/attrition.py` 的 `Obstacle`：`obstacle_type` / `density` / `breached`
  三個欄位**全中性預設**；`obstacle_from_feature` 從 `MapFeature.attributes` 讀出來
  （在此之前 `attributes` 只被拿去判 `is_impassable` 就丟掉了）。
- 加 `obstacles_at()`：逐 tick 的「站在哪些障礙裡」。用**退化線段**（起訖同點）重用
  `_segment_hits_obstacle`——另寫一份點位測試，兩份幾何必然漂移。
- 建 `core/app/engine/obstacle_wiring.py`（接線）：`typed()` 過濾、`transit_speed_multiplier()`、
  `roll_mine_strike()`、`apply_mine_suppression()`、`drain_engineer_orders()`。
- 接進 `UnitMovementSystem._advance_unit`：**在道路加速之後**乘障礙倍率；觸雷 → 扣戰力 +
  壓制 + 令 COMPLETED（停在原地）。
- 加 `ENGINEER` 令型（payload / validator / precheck / contract / 前端型別）。
- 接進 `sim_runtime` 的 pre_tick（`_engineer_tick`），事件走 `LedgerWriter`。
- 三個係數進 `SimParams`。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/adjudication/obstacles.py | 新增 | 純裁決：型別 → 效果表、觸雷機率、破障工時、`blocks_road` |
| core/app/engine/obstacle_wiring.py | 新增 | 接線：型別過濾、擲觸雷、壓制、ENGINEER 令執行（含工時） |
| core/app/movement/attrition.py | 修改 | `Obstacle` 加三欄（中性預設）；`obstacle_from_feature` 讀 attributes；新增 `obstacles_at` |
| core/app/engine/movement.py | 修改 | 逐 tick 障礙速度倍率 + `_roll_mine`（MINE_STRIKE 事件） |
| core/app/orders/schemas.py | 修改 | `OrderType.ENGINEER` + `EngineerPayload`（BREACH/EMPLACE 形狀互斥） |
| core/app/orders/validator.py | 修改 | 登錄 `EngineerPayload`（未登錄＝靜靜略過驗證） |
| core/app/orders/precheck.py | 修改 | `_precheck_engineer`：要是工兵、標的要在、人要在 500 m 內 |
| core/app/sim_runtime.py | 修改 | pre_tick 加 `_engineer_tick`；事件交 LedgerWriter |
| core/app/sim_params.py | 修改 | 三個 C2 係數（觸雷率/觸雷戰損/工兵倍率） |
| contracts/core_api.yaml | 修改 | OrderType enum + ENGINEER payload 說明 |
| platform/app/types/api.ts | 重生 | `npm run gen:api` |
| core/tests/unit/test_obstacles.py | 新增 | 27 條：中性、型別語意、觸雷、ENGINEER 令 |
| core/tests/unit/test_movement_obstacles.py | 新增 | 7 條：**移動接線層**的中性與驗收條文 |

## 測試證據

- `uv run pytest -q` → **1772 passed, 8 skipped**（含 5 條 golden replay，**未重錄**）
- `uv run ruff check .` / `uv run mypy` / `schema_sync_check.py` → clean
- `cd platform && npm run lint && npm run typecheck` → clean
- 突變測試（每次清 `__pycache__`，見 A2 教訓）——10 個突變**全數被抓**：

| # | 突變 | 結果 |
|---|------|------|
| M1 | `typed()` 不過濾 | 1 failed |
| M2 | `obstacle_type_of` 缺值回 MINEFIELD | 2 failed |
| M3 | `is_engineer` 缺值回 True | 1 failed |
| M4 | 觸雷後不停（照走） | 1 failed |
| M5 | 工兵不減半 | 2 failed |
| M6 | 破障不解除阻擋 | 2 failed |
| M7 | 標的消失判 COMPLETED（假成功） | 1 failed |
| M8 | 破障工時歸零 | 1 failed |
| M9 | 鐵絲網不減速 | 3 failed |
| M10 | 疊障礙取最寬鬆 | 1 failed |

## 決策與陷阱

**中性保證做成結構性的，不只是「係數剛好等於 1.0」。** `typed()` 在入口就把沒有
`attributes.obstacle_type` 的標註整個濾掉：既有局那條路徑拿到空 list → 一次幾何判定都不做、
**一次 RNG 都不抽**。WP-C3 就是在接線層栽的（`mounted` 缺鍵被 `bool()` 收成 False，
既有局命中率無聲掉 20%），所以本卡的中性測試也全部打在接線層，不是在純函數層。

**RNG 串流有狀態，多抽一次會讓後面所有隨機結果位移。** `roll_mine_strike` 只在真的踩到
雷區時才 `rng.random()`；有一條測試直接比對 `get_state()` 前後相同。

**觸雷後令即結束（COMPLETED），不是扣血照走。** 雷區真正的價值是把進攻縱隊釘在原地；
「炸完照走」會讓它退化成一個扣血地形。

**疊障礙取最嚴格的那個，不是連乘。** 雷區＋鐵絲網連乘會比鐵絲網難走一個數量級；
現實裡疊障礙的效果是「以最難的那道為準」。

**障礙倍率乘在道路加速之後。** 障礙就是拿來卡住道路的——讓道路基準把它蓋掉，
等於障礙對主要接近路線完全無效。

**工兵是 `TacticalUnit.attributes.unit_kind`，不開新欄位。** ORBAT 的兵種屬性
（`platform_count` 等）本來就住在那裡；為一個布林開 migration 換不到任何查詢能力。
缺值＝不是工兵，方向是刻意的：**多算成工兵才會讓雷區失效**。

**BREACH/EMPLACE 收成一個 `ENGINEER` 令型**（同 WP-C3 把 MOUNT/DISMOUNT 收成 FORMATION 的理由）：
兩者是同一件事——工兵對障礙做工，都要工兵、都要時間、都改同一張 MapFeature。

**ENGINEER 令與 POSTURE/FORMATION 不同形狀。** 那兩個是**宣告**（一 tick 完成）；
障礙作業是**工作**（破雷區 45 tick、炸橋 120 tick）。進度記在 order payload 的
`_work_until_tick`（與 MOVE 的 `_leg` 同一套：住在令上，checkpoint 自動涵蓋）。
完工那一刻才改 MapFeature——破到一半的雷區還是雷區。

**標的在施工期間被刪 → CANCELLED + `ENGINEER_WORK_ABORTED`，不可判 COMPLETED**：
那會讓 AAR 看起來像「破障成功」。狀態機沒有 FAILED，故用 CANCELLED。

**SQLAlchemy 的 JSON 欄位要整包換掉才會被視為 dirty**；原地 mutate 不會落庫
（`feature.attributes = {**old, "breached": True}`）。

**預檢在下令時就擋**（非工兵 / 距作業點 > 500 m / 標的不存在）。工兵令要花數十分鐘，
等到收工才發現「這單位不是工兵」，那段時間已經回不來了。

`_feature_centroid` 認不得的幾何**回 None 而不是 (0,0)**：後者會讓 proximity 檢查
以「距離 10000 km」的形式報錯，真正的原因（幾何壞了）就被蓋掉。

## 中斷續作指引

- **下一步第一件事**：C2 已完成。剩下的是**前端下令 UI**（見未竟項）。
- **未竟項**（已記入 PROGRESS Backlog）：
  1. **前端 ENGINEER / FORMATION 下令 UI**——`useCopOrdering.ts` 的 `orderType` union 目前是
     `MOVE|ENGAGE|FIRE_MISSION|POSTURE|MISSION`，C3 的 FORMATION 與本卡的 ENGINEER 都還進不去。
     後端與契約都通了，但**使用者點不到**。V2.1 exit 的 armor-breakthrough CPX 需要破障，
     這一項是那張卡的前置。
  2. 地圖編輯器標注障礙時可選 `obstacle_type` / `density`（目前只能靠 API 寫 attributes）。
  3. ORBAT 編輯器可勾選 `unit_kind=ENGINEER`（目前同上）。
  4. `blocks_road` 尚未接進路由/道路加速（斷橋目前只是「不減速的障礙」）。
- **尚未驗證的假設**：v0 校準值（觸雷 0.35/km、破障工時 20–120 tick）沒有實測基準，
  只保證方向正確與可由 SimParams 調整。
