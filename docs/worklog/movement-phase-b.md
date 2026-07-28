---
task: "#81 移動真實化 Phase B — 地形/坡度逐段調速 + 不可通行阻擋"
status: DONE
started: 2026-07-25T02:00+08:00
updated: 2026-07-25T03:00+08:00
agent: Opus 4.8
spec: SPEC_MOVEMENT.md §2.2/§2.3(A/B)/§5(B)
---

# #81 Phase B：地形類別/坡度調速 + 不可通行阻擋 + 預覽同源

## 目標（SPEC_MOVEMENT §5 Phase B）
- v_eff 逐 tick 依實際地形類別 + 坡度（mobility_matrix.step_cost）調變（森林/山地/濕地/上坡慢）。
- 不可通行段（矩陣 -1）→ 停在此 + MOVE_BLOCKED。
- 天氣修正 hook。預覽採同一速度模型（路徑平均地形成本 + 不可通行標示）。
- 仍直線移動（繞路為 Phase C）。

## 交付
- **契約/服務**：terrain proto 已有 `GetCellBatch`（CellInfo：terrain_class/slope_deg/…）；於 core
  `TerrainClient.get_cell_batch` 曝露之（唯一新客戶端方法，無 proto 變更）。
- **core mobility matrix**（新 `movement/mobility_matrix.py`）：讀 `contracts/mobility_matrix.json`，
  `step_cost(profile, terrain_class, slope) → 倍率|None`（-1→None 不可通行；未知→1.0 安全退化）。
  註：terrain 的 `CellInfo.mobility_cost` 是 profile 無關，per-profile 速度必須用本矩陣。
- **執行器**（`engine/movement.py`）：注入 `terrain_sampler`（h3→terrain_class/slope）+ `weather_mobility`；
  `_advance_unit` 以「目前所在格」成本調變步距（step /= cost）；不可通行→`_block_impassable`（COMPLETED +
  MOVE_BLOCKED）；`_terrain_cost` 以 (cell,profile) 快取（地形靜態）、取樣失敗→1.0 不快取（不凍結移動）。
  admit 另存 `_mobility_profile` 供查表。
- **共用取樣器**（新 `movement/terrain_sampler.py` `build_terrain_cell_sampler()`）：sim_runtime 執行器
  與 api 預覽共用；STUB/無 grpc/失敗→None（退回 Phase A 直線 per-unit 速度）。
- **預覽**（`api/movement.py`）：沿路徑取樣 hex（res 8 去重）→ 平均地形成本調變速度 + `terrain_impassable`
  標示（穿不可通行→feasible=False）。回傳加 `terrain_impassable`。

## 紅線/韌性
- 地形取樣**確定性**（位置→class/slope，terrain 服務快取）；march/強穿仍走 DeterministicRNG(movement)。
- terrain 服務中斷 → 執行器/預覽退回不調速（不凍結移動），同交戰 LOS 中斷紀律。
- 熱狀態單寫者不變；AI 不裁決物理（地形成本為引擎計算）。

## 測試
- 新 `test_movement_terrain.py` 6：step_cost（地形/坡度/不可通行/未知退化）、森林<草地、上坡<平地、
  輪型入水 MOVE_BLOCKED+COMPLETED、terrain 中斷退化仍前進、無取樣器＝Phase A。
- 既有 movement/mobility 14：terrain_sampler=None → Phase A 行為不變。
- gates：**pytest 1025 passed / 8 skipped**、**golden 6 未破無需重錄**（golden 不注入取樣器）、
  ruff/mypy(196)/schema-sync 綠；前端未動（預覽多回一欄，前端相容）。

## 已知取捨（記後續）
- 不可通行「停邊界」為近似：以「進入該格首 tick」判定（~1 格 ≈ 0.5km 深），精確邊界交點列後續。
- 坡度懲罰對稱（abs）；下坡優惠列 SPEC §7 後續。
- 天氣：hook 已備（weather_mobility 標量，預設 1.0），實際接 per-cell 天氣列後續。
- 預覽用「路徑平均成本」近似（非逐 tick 逐格），與執行非位元一致；Phase C 以 get_path 真正統一。
- 前端預覽「不可通行/地形較慢」提示未做（後端已回 terrain_impassable/speed_kmh）。

## 中斷續作指引
- 已完成實作+測試+gates。剩：commit → 容器重建 → live 驗（get_cell_batch/step_cost 在 image）→
  PROGRESS/TASKS 更新。下一張：#82 Phase C（地形 A* 繞路 + 道路 + 油料 + 任意點位起終點）。
