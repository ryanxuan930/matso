# MATSO — 系統擴充規格（SPEC_MOVEMENT）
# 真實部隊移動（Realistic Ground Movement：機動、地形、耗損）

> 本文件擴充 [SPEC_FULL.md](SPEC_FULL.md) §4.3（地形服務 / 機動成本）、§5.3（消耗與士氣）與任務 O3.4（移動執行），為「單位移動反映**機動能力、地形與行軍耗損**」的權威設計。
> 語言/關鍵字慣例同 SPEC_FULL：正體中文敘述、程式識別字/API 欄位一律英文；MUST/SHOULD/MAY 依 RFC 2119。
> 對應任務板：**#80（Phase A）、#81（Phase B）、#82（Phase C）**。工程規範見 [HOW_TO.md](HOW_TO.md)。

---

## 0. 背景與問題陳述

使用者實測回報：AI（與人類）下移動令時，單位**速度與距離超乎常理**、**不分是否有機動載具**（機械化 vs 徒步）、且**完全不受地形影響**（上下坡、道路、涉水、跨越障礙均無感），也**沒有移動耗損**。

根因＝**三套不一致的移動模型並存**（見 [PROGRESS.md](PROGRESS.md) Backlog「移動真實化」）：

1. **預覽** `api/movement.preview` → `estimate_route`（[core/app/movement/attrition.py](core/app/movement/attrition.py)）：直線 + 手繪障礙幾何 → 常顯「路徑暢通」。
2. **預檢閘門** `_precheck_move` → `gateway.path_reachable` → terrain A\*（[modules/terrain/terrain/pathfind.py](modules/terrain/terrain/pathfind.py)）：地形成本可達性，但格網快取範圍外即回 unreachable。
3. **執行**（唯一真正動單位的）`engine/movement.py::UnitMovementSystem`：**直線內插 + 固定 40 km/h**，完全不看地形，正常行軍**不扣戰力**（只有 `_apply_forced_attrition` 強穿障礙才扣）。

現況關鍵事實（survey 實查）：

| 面向 | 現況 | 位置 |
|---|---|---|
| 速度 | 全單位一律 `MOVE_SPEED_KMH = 40.0`（單一常數） | [core/app/movement/params.py](core/app/movement/params.py) |
| 機動 profile | `mobility_profile` 恆為前端/AI 硬寫 `"FOOT"`，執行器**完全不讀** | [orders_bridge.py:36](core/app/ai_loop/orders_bridge.py), cop.vue |
| 每單位速度資料 | `mobility_class` / `max_road_speed_kmh` / `max_cross_country_speed_kmh` 已存在 seed 資料，但**從未 seed 進 DB、從未被讀** | [core/app/adjudication/seed_weapons.py](core/app/adjudication/seed_weapons.py) |
| 地形 | 執行走直線 lat/lng，不查 terrain；坡度/道路/涉水零影響 | [engine/movement.py:204](core/app/engine/movement.py) |
| 行軍耗損 | `_ATTRITION_PER_KM = 0.0`（正常行軍零耗損） | [attrition.py:236](core/app/movement/attrition.py) |
| AI | 上述全繼承；LLM 完全不知單位速度/單回合可達距離 → 下瞬移級移動令 | [ai_loop/context.py](core/app/ai_loop/context.py) |

**利多**：基礎建設多已具備——terrain `get_path`（含 `mobility_matrix` 地形×坡度成本）、`MobilityMatrix.step_cost`、每單位速度 seed 資料、`attrition.py` 的 `attrition_per_km` 掛鉤、`weather.movement_mobility_modifier`。本規格是**接線 + 速度模型**，非從零打造。

---

## 1. 設計原則與紅線（沿用，不可違反）

1. **AI 永不裁決物理**：速度、路徑、可達性、耗損皆為 `core/app/engine/` + `core/app/movement/` 的確定性計算。AI（LLM）**僅得選擇移動目的地與行軍節奏意圖（tempo）**；速度/路徑/耗損由引擎裁決。
2. **確定性可重播**：任何隨機性一律經注入的 `DeterministicRNG(seed, stream="movement")`；牆鐘/裸 random 禁用（Kernel 為熱狀態唯一寫入者）。**相同 (輸入, rng 狀態) → 相同結果**。
3. **golden replay 明確重錄**：本規格**每個 Phase 都改變移動的決定性輸出**（位置軌跡/耗損）→ 6 條 golden replay MUST 以 `ops/tools/rerecord_golden.py` 重錄，並於 worklog 記錄 before/after 與重錄理由。此為**預期**，非回歸破壞。
4. **契約先行**：改 `contracts/`（mobility_matrix / core_api / terrain proto）→ 驗證 → 再實作；DB 變更只走 `prisma migrate`。
5. **一次一張卡**：Phase A→B→C 循序交付，各自綠燈可上線；範圍外問題進 PROGRESS Backlog。
6. **預覽＝閘門＝執行同源**（消除三套不一致）：三條路徑 MUST 逐步收斂到**同一速度模型與同一路由**（Phase C 完成時）。

---

## 2. 目標模型

### 2.1 每單位機動 profile 導出（`UnitMobility` resolver）

單位的 `mobility_profile ∈ {FOOT, WHEELED, TRACKED, BOAT, AIR}` **由其編裝導出**（不由前端/AI 指定），規則（優先序，取「最能自走」者）：

- 擁有 ≥1 件 `can_self_move=true` 且 `mobility_class=TRACKED` 的裝備 → `TRACKED`（含搭載步兵的履帶 IFV）。
- 否則擁有 `can_self_move=true` 且 `mobility_class=WHEELED` → `WHEELED`（卡車化/輪型載具）。
- 否則（僅 `MAN_PORTABLE` / 無自走載具）→ `FOOT`。
- `BOAT`/`AIR` 由單位類別（naval/air）導出（本規格聚焦地面；海空為後續）。

實作：新增 `UnitMobilityResolver`（比照 `WeaponResolver`，讀單位 `EquipmentInstance → EquipmentTemplate.base_stats`），回 `MobilityProfile`（profile + `base_road_kmh` + `base_xc_kmh` + `fuel_*`）。**單位裝甲/載具為空 → FOOT**。結果可快取於 `TacticalUnit.attributes.mobility`（純導出值，非權威；重算即可）。

> 前置資料缺口（Phase A MUST 補）：`mobility_class` / `max_road_speed_kmh` / `max_cross_country_speed_kmh` / `can_self_move` / `fuel_capacity` / `fuel_burn_per_km` 目前僅在 `seed_weapons.py` 存在但**未 seed**。Phase A 補 seed 到 `EquipmentTemplate.base_stats`（載具型範本）。

### 2.2 速度模型（每段有效速度）

單位在某地形段的**有效速度**：

```
v_eff(km/h) = base_speed(profile, on_road)
              / mobility_cost(profile, terrain_class, slope_deg)
              × weather_mobility_modifier
```

- `base_speed`：`on_road ? base_road_kmh : base_xc_kmh`（由 profile 導出，見 §2.1；徒步 xc≈5、道路≈6.5；輪型 xc≈40、道路≈85；履帶 xc≈45、道路≈65——**數值為想定可覆寫的 params，非硬寫魔數**）。
- `mobility_cost`：`MobilityMatrix.step_cost(profile, terrain_class, slope_deg)`（[contracts/mobility_matrix.json](contracts/mobility_matrix.json)，成本↑＝慢；`-1` = 不可通行 → 該段禁入）。坡度懲罰已含於 `step_cost`（`base × (1 + slope_penalty × slope/45)`）。
- `weather_mobility_modifier`：沿用 `weather.movement_mobility_modifier`（暴雨/濃霧降速）。
- **坡度上/下坡**：v1 用 `mobility_matrix` 的對稱 `slope_penalty`（`abs(slope)`）；下坡優惠（負坡度加速、極陡下坡風險）列 §8 後續精修，不阻擋本規格。

`v_eff` → 每 tick 前進距離 `step_km = v_eff × tick_ms / 3.6e6`（取代現況單一 `_step_km`）。

### 2.3 路徑模型（地形繞路，統一三套）

- **Phase A/B（直線階段）**：仍走起點→目的地（或自訂 waypoints）直線，但 §2.2 的 `v_eff` **逐段依實際地形取樣調變**（B 為逐 tick 取樣；A 可用路徑端點/粗取樣近似）。直線階段**不繞開**不可通行地形——改以「進入不可通行段 → 停在邊界 + `MOVE_BLOCKED` 事件」處理（不再直穿）。
- **Phase C（路由階段）**：執行改走 terrain A\* 路徑 `get_path(from_h3, to_h3, mobility_profile)` 回的 `h3_path`，**沿 hex 逐格前進**，每格依 §2.2 `v_eff` 計時 → 自然沿道路、繞開河流/山脈/不可通行地形。**預覽/閘門/執行共用同一 `get_path` 與同一速度模型**（消除三套不一致）。需一併處理格網覆蓋（見 §3「地形覆蓋」）避免長距離誤拒。
  - **任意點位起終點（MUST）**：系統支援**任意 lat/lng**（非 hex 中心）的起點與目的地，MOVE 目標不得被 `latlng_to_cell` 靜默吸附到格中心（現況 orders_bridge/執行皆會丟失精度）。路徑 = 「精確起點 → 起點所在 hex → …A\* hex 序列… → 終點所在 hex → 精確終點」：
    - 首段（精確起點→路徑第一個 hex 邊界/中心）與末段（路徑最後 hex→精確終點）為**部分格**幾何段，其距離/ETA/耗損按實際幾何長度計（非整格成本）。
    - 若起終點同格或極近（路徑退化為單格/零 hop），直接走「精確起點→精確終點」單段，套 §2.2 `v_eff`（用該格地形），不強制繞格。
    - 精確終點保留於 payload（`to_lat`/`to_lng`），單位最終停在**精確終點**而非其所在 hex 中心。
    - 分段 ETA/耗損（預覽與執行一致）需含首末部分格段，避免「預覽用整格、執行用精確點」再度分裂。

### 2.4 耗損與消耗模型

- **行軍耗損（Phase A 起用）**：正常行軍依 **距離 × 地形難度 × 節奏** 扣 `current_strength`（開啟 `attrition_per_km` 掛鉤）：

  ```
  march_loss_pct = base_march_rate × Σ_leg( leg_km × terrain_difficulty(profile, class, slope) )
                   × tempo_factor
  ```

  - `terrain_difficulty` 由 `mobility_cost` 導出（越難走磨耗越高）。
  - `tempo_factor`：`NORMAL=1.0`、`FORCED_MARCH>1`（強行軍：更快但耗損更高——速度↔耗損取捨，由 AI/人類的 tempo 意圖選）。
  - 上限夾住（單令累計 ≤ 上限，避免一次移動殲滅自己）；`current_strength ≤ 戰鬥無效門檻` 時停止磨耗。
  - 決定性：以 `DeterministicRNG(stream="movement")` 擲小幅散佈（或純確定性 + 小抖動），golden 重錄涵蓋。
- **強穿障礙耗損**：現況 `_apply_forced_attrition` 保留（手繪障礙），與地形難度耗損疊加但各記事件。
- **油料（Phase C）**：`EquipmentInstance.currentState` 記油料；移動依 `fuel_burn_per_km` 消耗；油盡 → `HALTED_FUEL`（SPEC_FULL §5.3「油料耗盡無法移動」MUST）。徒步單位不受油料限制。Phase A/B 可先 stub（不扣油）。

---

## 3. 資料模型與契約變更

- **EquipmentTemplate.base_stats（Phase A，seed 資料，非 schema 變更）**：載具型範本補 `mobility_class`、`max_road_speed_kmh`、`max_cross_country_speed_kmh`、`can_self_move`、（Phase C）`fuel_capacity`、`fuel_burn_per_km`。走 `weaponeering.schema.json` 驗證（如需擴 schema → 契約先行）。
- **`core/app/movement/params.py`（Phase A）**：新增 per-profile 基準速度表（road/xc）+ `base_march_rate` + tempo 係數——**單一來源**供預覽（`api/movement.py`）與執行（`sim_runtime`）共用，杜絕再分裂。
- **`contracts/mobility_matrix.json`（Phase C）**：如導入道路，新增 `ROAD` 處理（terrain_class `ROAD` 或 `on_road` 成本優惠）；沿用既有 profile×class×slope 公式。
- **`MovePayload`（Phase A，契約先行 `core_api.yaml`）**：`mobility_profile` 改為**伺服端導出**（前端/AI 傳入值僅供參考、以導出值為準）；新增選填 `tempo ∈ {NORMAL, FORCED_MARCH}`。
- **terrain proto `GetPath`（Phase C）**：`eta_ticks` 由佔位改為依 §2.2 速度模型換算的真實 ETA（`pathfind.py` 現為 `ceil(total_cost)` 佔位）。
- **地形覆蓋（Phase C）**：解決 hex 格網「快取範圍外回 unreachable」——on-demand 建格 或 擴大預建範圍（見 [TASKS.md](TASKS.md) O2.2/O2.3 格網 CLI）。
- **DB**：本規格**優先以導出值避免 schema 變更**（mobility 由裝備導出、快取於 `attributes`）。若最終決定持久化 per-unit 機動欄，才走 `prisma migrate`（schema 權威 = `db/prisma/schema.prisma`）。

---

## 4. AI 整合（neuro-symbolic 邊界內）

- **profile 導出**：`orders_bridge.py` 不再硬寫 `FOOT` → 用 §2.1 `UnitMobilityResolver`（AI MOVE 與人類 MOVE 同源導出）。
- **AI context（`ai_loop/context.py`）**：每個我方單位加 `mobility_class`、`speed_kmh`、`reach_per_decision_km`（＝speed × 心跳秒數）——讓 LLM 據此挑**可達**目的地，不再瞬移。
- **decider `OUTPUT_INSTRUCTION`**：明示「單位每個決策週期只能前進約 reach_per_decision_km；遠程目標需分多次 MOVE 逐步推進」。
- **feasibility（G3）**：速度真實化後，長距離移動自然需多 tick 完成，**不需人工距離上限**；但 Phase C 後 `path_reachable` 以真實 `get_path` 為準（不可達地形 → 拒）。

---

## 5. 分階段實作（每階段一張任務卡；皆需 golden 重錄）

### Phase A — 機動速度 + 行軍耗損 + AI 機動感知　（任務 #80）
- Seed `mobility_class`/速度/`can_self_move` 到 `EquipmentTemplate`；`UnitMobilityResolver` 導出 per-unit profile+速度。
- 執行器 `UnitMovementSystem` 改讀 per-unit `step_km`（取代固定 40）。
- 開啟行軍耗損（距離 × 地形難度粗略 × tempo；`attrition_per_km` 掛鉤）。
- AI：導出 profile（去除硬寫 FOOT）+ context 加速度/單回合可達 + decider 指示。
- **驗收**：機械化與徒步同距離 ETA 明顯不同（固定 seed）；長程行軍產生 `MOVE_ATTRITION`（非強穿）；AI MOVE 用導出 profile 且 context 顯示速度；預覽與執行速度一致。golden 6 重錄。

### Phase B — 地形/坡度逐段調速　（任務 #81）
- `v_eff` 逐 tick 依實際地形類別 + 坡度取樣（`mobility_matrix.step_cost`）+ weather modifier 調變；不可通行段 → 停邊界 + `MOVE_BLOCKED`。
- 預覽 `estimate_route` 同步採同一速度模型（分段 ETA/耗損）。
- **驗收**：同一路線穿森林/山地/濕地/上坡明顯慢於開闊/平地（固定 seed 係數比較）；進入不可通行地形停在邊界並記事件；預覽分段 ETA 與執行一致。golden 6 重錄。

### Phase C — 地形繞路 + 道路 + 油料（統一三套模型）　（任務 #82）
- 執行改走 `get_path` A\* 路徑（沿道路、繞開河/山/障礙）；預覽/閘門/執行同一路由。
- 道路網整合（`taiwan_drive.graphml` 或 `ROAD` class）；油料消耗 + 油盡 `HALTED_FUEL`。
- 地形格網覆蓋擴大/on-demand，消除長距離誤拒；`GetPath.eta_ticks` 真實化。
- **驗收**：單位繞開河流/山脈而非直穿；沿道路加速；預覽路徑＝執行路徑；油盡停止 + 事件；長距離不再誤判 unreachable。golden 6 重錄。

---

## 6. 決定性與 golden replay

- 每 Phase 完成後跑 `ops/tools/rerecord_golden.py` 重錄 6 條 golden，worklog 記錄「重錄理由 + 關鍵軌跡 before/after」。
- 所有移動隨機（耗損散佈、強穿）**只**用 `DeterministicRNG(seed, "movement")`；速度/地形/路徑為純確定性計算。
- 重錄後 `uv run pytest`（含 replay）MUST 綠；`schema_sync_check`、`ruff`、`mypy`、前端 lint/typecheck 綠。

---

## 7. 開放問題 / 後續（不阻擋本規格）

- 下坡優惠 / 極陡下坡風險（v1 用對稱坡度懲罰）。
- 搭載/下車（mount/dismount）狀態切換：步兵上/下 IFV 改變 profile 的顯式指令。
- 士氣/抑制對移動的限制（SPEC_FULL §5.3 morale）——與耗損分離，後續。
- 疲勞累積跨多次移動（連續強行軍遞增耗損）。
- 海空 profile（BOAT/AIR）完整化。
