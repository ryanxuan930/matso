---
task: "#44–#48 (SPEC_EXTEND P1–P5)"
status: IN_PROGRESS
started: 2026-07-23T00:00+08:00
updated: 2026-07-23T01:00+08:00
agent: Opus 4.8
---

# 單位內多武器・聯合兵種交戰（SPEC_EXTEND P1–P5）

## 目標摘要
把交戰從「單位降維成一件武器」升級為「單位以其武器組合同時對目標發揮火力」
（weapon-target kill-rate summation）。射程帶 + 裝甲類自動完成 weapon-target 匹配；
fire policy 保留玩家/AI 能動性；維持 neuro-symbolic 紅線與 golden replay 不破。
權威設計見 [SPEC_EXTEND.md](../../SPEC_EXTEND.md)。

## 計畫（分階段）
- [x] **P1 (#44)** WeaponResolver.weapons_for + 熱狀態 ammo_by_weapon（純資料，不接裁決）✅
- [ ] **P2 (#45)** adjudication/combined.py（Σ volley）+ EngagementAdjudicator gating + determinism
- [ ] **P3 (#46)** fire_policy 契約 + 接線 + AI decider 欄
- [ ] **P4 (#47)** COP 武器組合 UI + AAR 逐武器
- [ ] **P5 (#48)** 目標編成組成 + 多目標分配（後續，先記設計）

## 執行紀錄（附時間，由上而下追加）
- 開工：讀 engagement.py / engage_wiring.py / adjudicator.py 三路徑，確認皆降維成單一 WeaponProfile；
  golden（rng_walk/order_replay/empty）為 movement/RNG-walk 不跑武器混合交戰 → combined 路徑 gating 後 golden 天然不受影響。
- 寫 SPEC_EXTEND.md（commit 51e1b7c）；建任務 #44–#48；開分支 feat/combined-arms-fires。
- **P1 起手**：WeaponResolver 加 weapons_for（穩定序武器清單）+ seed_combat_state 加 ammo_by_weapon。
- **P1 完成**：WeaponEntry frozen dataclass（weapon_id/profile/quantity/ammo）；`_build` 逐單位收集武器 entries、依 weapon_id 穩定排序存 `_weapons_by_unit`；`weapons_for(unit_id)` 回清單；`seed_combat_state` 加 `ammo_by_weapon`（僅鍵不存在時 seed）。7 新測試綠、全 gate 綠、golden 6 綠不受影響。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| SPEC_EXTEND.md | 新增 | P1–P5 權威設計 |
| core/app/engine/engage_wiring.py | 修改（P1） | WeaponEntry + weapons_for + ammo_by_weapon seed |
| core/tests/unit/test_engage_wiring.py | 修改（P1） | +7 測試（武器清單/穩定序/單武器一致/未知空/ammo_by_weapon seed+保留） |

## 測試證據
- P1：`uv run pytest core/tests/unit/test_engage_wiring.py -q` → **21 passed**（+7）
- P1 gate：`ruff` 綠、`mypy` 181 files clean、golden replay **6 passed（不受影響）**、engagement/volley/weapons/adjudicator/scripted_battle **24 passed**（無回歸）

## 決策與陷阱
- 武器清單**穩定排序**（依 weapon_id）：P2 每武器一次 dispersion 抽樣的順序需決定性，才能 replay。
- per-weapon 彈藥 seed **僅在鍵不存在時寫**：避免執行期重啟把 Redis 已扣量重置回 DB 初值。
- WeaponEntry.ammo = DB 初始彈藥（供 seed）；執行期活彈藥在熱狀態 ammo_by_weapon（P2 讀）。

## 中斷續作指引（⚠ 停筆前更新）
- **下一步第一件事**：**P2** — 新增 `core/app/adjudication/combined.py`（純函數 `resolve_combined_engagement`：逐武器 `_legality_reason` 篩選 → 各自 volley 期望毀傷 → Σ → 單一 EngagementResult 含 per_weapon + ammo_spent_by_weapon；E 空 → REJECTED，reason 取最能說明者）。接線層提供 `env_for(shooter, target, weapon)`（依武器飛行剖面給 indirect_fire/trajectory_clear）。`EngagementAdjudicator.resolve` gating：**單位武器系統數 ≥2 且非聚合 → combined**，否則走既有單發/齊射（golden/單武器不變）。RNG：每武器恰一次 dispersion（順序＝weapons_for 穩定序）。
- **P2 讀取熱狀態**：活彈藥改讀 `ammo_by_weapon[weapon_id]`（P1 已 seed）；扣減寫回 per-weapon。
- **P2 決定性驗收**：golden replay 全綠不需重錄（gating 保單武器路徑）；同 seed 同結果測試。
- **目前卡點**：無。
- **尚未驗證的假設**：混合武器對步兵/裝甲的 pk 差異需 base_stats 有 `pk_by_armor_class`（`expected_casualties` 來源）——P2 測試 fixture 要設好反裝甲 vs 步槍的 pk。
