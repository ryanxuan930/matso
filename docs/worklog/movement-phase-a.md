---
task: "#80 移動真實化 Phase A — 機動速度 + 行軍耗損 + AI 機動感知"
status: DONE
started: 2026-07-25T00:00+08:00
updated: 2026-07-25T01:30+08:00
agent: Opus 4.8
spec: SPEC_MOVEMENT.md §2.1/§2.2/§2.4/§5(A)
---

# #80 Phase A：per-unit 機動速度 + 行軍耗損 + AI 機動感知

## 目標（SPEC_MOVEMENT §5 Phase A）
1. Seed mobility 資料到 EquipmentTemplate（SEED_ARTILLERY/SEED_VEHICLES 目前未 seed）。
2. `UnitMobilityResolver`：由單位編裝導出 profile（TRACKED/WHEELED/FOOT）+ 速度（road/xc）。
3. 執行器 `UnitMovementSystem` 改讀 **per-unit** step_km（取代固定 40）——於 admit 解析、存 payload。
4. 行軍耗損：admit 時依「總路徑距離 × per-km 磨耗 × tempo」扣 strength（確定性，記 MOVE_ATTRITION reason=MARCH）。
5. 預覽 `api/movement.preview` 用同一 per-unit 速度 + 磨耗率 → 預覽＝執行一致。
6. AI：`load_unit_meta` 解析 mobility → UnitMeta + context 顯示 speed_kmh；decider 指示「速度受限、遠目標分多次 MOVE」；orders_bridge/submit 導出 profile（去硬寫 FOOT）。

## 關鍵現況（開工前查證）
- 執行器 `engine/movement.py`：`self._step_km` 單一常數（40 km/h）；march attrition = `_ATTRITION_PER_KM=0.0`；只有強穿 `_apply_forced_attrition`。
- mobility 資料在 `seed_weapons.py` SEED_ARTILLERY/SEED_VEHICLES 的 `base_stats["mobility"]`（can_self_move / mobility_class / max_road_speed_kmh / max_cross_country_speed_kmh / fuel_burn_per_tick），但 `seed_equipment.ensure_weapon_templates` 只 seed KINETIC → 未落地。
- 一般單位預設只配 RIFLE_556（FOOT）；載具需經 armory/ORBAT 指派。
- `params.py`：MOVE_SPEED_KMH=40、MOVE_TICK_RATE_MS=60000（單一真相，預覽+執行共用）。
- `attrition.estimate_route(..., attrition_per_km=)` 已有磨耗掛鉤（現值 0）。
- MovePayload 為內部 pydantic（payload 於 core_api 為泛型 dict）→ 加 tempo 免動契約。

## 設計決定
- **執行器以編裝為速度權威**（忽略 payload.mobility_profile 做速度）；payload profile 僅供 precheck（Phase A 不改 routing，Phase C 統一）。
- 速度 profile 導出：有自走載具 → 取 TRACKED（優先）否則 WHEELED；同級多車取**最慢**（車隊受限）；純人攜 → FOOT（速度來自 params）。
- march attrition **純確定性**（不動 movement RNG stream；forced-crossing 仍用 RNG）；地形難度 Phase A 固定 1.0（Phase B 才逐段取樣）。
- **golden 6 重錄**（速度/耗損改變決定性軌跡）——預期，非破壞。

## 進度（實作完成）
- `params.py`：FOOT_XC/ROAD_KMH、MARCH_ATTRITION_PER_KM（per-profile）、TEMPO_SPEED/ATTRITION_FACTOR、march_attrition_per_km()。
- `movement/mobility.py`（新）：UnitMobility + mobility_from_stats（純）+ resolve_unit_mobility / resolve_session_mobility（批次）。規則：自走 TRACKED>WHEELED，同級取最慢；無載具→FOOT。
- `seed_equipment.py`：_upsert_templates 泛化 + ensure_mobility_templates（seed ARTILLERY/VEHICLE，使 mobility 可用）；seed_session_equipment 呼叫之。
- `engine/movement.py`：admit 解析 per-unit step_km 存 payload._step_km；_advance_unit 讀之；`_apply_march_attrition`（距離×per-km×tempo，上限 30%，記 MOVE_ATTRITION reason=MARCH）。**修一 bug**：mid-step `_step_towards` 原誤用 self._step_km（後備 40）→ 改用 per-unit step_km（否則單位超越到達門檻而振盪不停）。
- `api/movement.py`：預覽用 resolve_unit_mobility 的 per-unit 速度 + march 磨耗率；回傳加 mobility_profile/speed_kmh。
- `orders/schemas.py`：MovePayload +tempo。
- `ai_loop/context.py`：UnitMeta +mobility_profile/speed_kmh；own-unit view + briefing 顯示「機動：profile speed km/h」。
- `ai_loop/worker.py`：load_unit_meta 批次 resolve_session_mobility 填 UnitMeta。
- `ai_loop/orders_bridge.py`：tactical_order_to_request +mobility_lookup（G3+submit 同源導出 profile，去硬寫 FOOT）+tempo 透傳。
- `ai_loop/decider.py`：OUTPUT_INSTRUCTION 告知速度上限、遠目標分多次 MOVE、tempo 選項。

## 測試
- 新 `test_mobility.py` 7：mobility_from_stats（FOOT/TRACKED/WHEELED/優先/車隊最慢）、resolve 讀編裝、**機械化 vs 徒步 ETA（headline）**、march 耗損記錄+扣戰力。
- 改既有 4（速度真實化後）：test_movement 到點 tick 預算/首事件；test_movement_forced march+forced 並存/無障礙仍有 march/短距 waypoints。
- **golden 6 未破、無需重錄**（golden 想定不觸及 live 地面移動路徑）——比 SPEC 預估更省。
- gates：pytest 1019 passed/8 skipped、ruff/mypy(194)/schema-sync 綠；前端未動。

## 中斷續作指引
- 已完成實作+測試+gates。剩：commit → 容器重建 → live 驗（機械化 vs 徒步 ETA、行軍耗損事件）→ PROGRESS/TASKS 更新。Phase B（地形/坡度逐段調速）為下一張卡 #81。
