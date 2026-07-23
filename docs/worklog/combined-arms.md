---
task: "#44–#48 (SPEC_EXTEND P1–P5)"
status: IN_PROGRESS
started: 2026-07-23T00:00+08:00
updated: 2026-07-23T02:00+08:00
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
- [x] **P2 (#45)** adjudication/combined.py（Σ volley）+ EngagementAdjudicator gating + determinism ✅
- [ ] **P3 (#46)** fire_policy 契約 + 接線 + AI decider 欄
- [ ] **P4 (#47)** COP 武器組合 UI + AAR 逐武器
- [ ] **P5 (#48)** 目標編成組成 + 多目標分配（後續，先記設計）

## 執行紀錄（附時間，由上而下追加）
- 開工：讀 engagement.py / engage_wiring.py / adjudicator.py 三路徑，確認皆降維成單一 WeaponProfile；
  golden（rng_walk/order_replay/empty）為 movement/RNG-walk 不跑武器混合交戰 → combined 路徑 gating 後 golden 天然不受影響。
- 寫 SPEC_EXTEND.md（commit 51e1b7c）；建任務 #44–#48；開分支 feat/combined-arms-fires。
- **P1 起手**：WeaponResolver 加 weapons_for（穩定序武器清單）+ seed_combat_state 加 ammo_by_weapon。
- **P1 完成**：WeaponEntry frozen dataclass（weapon_id/profile/quantity/ammo）；`_build` 逐單位收集武器 entries、依 weapon_id 穩定排序存 `_weapons_by_unit`；`weapons_for(unit_id)` 回清單；`seed_combat_state` 加 `ammo_by_weapon`（僅鍵不存在時 seed）。7 新測試綠、全 gate 綠、golden 6 綠不受影響。
- **P2 完成**：`adjudication/combined.py`（純函數 `resolve_combined_engagement`）——逐武器 `_legality_reason` 篩選（不合法貢獻 0，非拒整場）→ 各自 volley 期望毀傷（每合格武器恰一次 dispersion，順序＝武器清單穩定序）→ Σ（夾在目標戰力內，能量守恆）；全數不合法→REJECTED（reason 取優先序 NO_LOS>TRAJECTORY_BLOCKED>OUT_OF_RANGE>NO_AMMO）；輸出帶 `per_weapon[]` + `ammo_spent_by_weapon`。`EngagementResult` 加 `ammo_spent_by_weapon` 欄。`EngagementAdjudicator` gating：aggregate 優先 → 否則 `combined_weapons_for` 回 **≥2 武器 → `_resolve_combined`**，<2 落回既有單/齊射（golden 不變）；`_apply` 依 `ammo_spent_by_weapon` 非 None 逐武器扣熱狀態 `ammo_by_weapon`（純量 ammo 同步扣總量）。`make_combined_weapons_for(resolver, hot)` 讀熱狀態活彈藥組 CombinedWeapon。sim_runtime 接線。**射程帶 + 裝甲類 pk 自動 weapon-target 匹配已驗**（步槍打步兵有效/打裝甲 0；ATGM 反之；遠距只長程武器貢獻）。
- **決策：FREE 政策下零效武器仍發射耗彈**（ATGM 打步兵：合格→發射→0 毀傷但扣彈）。「不浪費重火力」是**戰術決策非物理**→ 交由 P3 fire policy（ANTI_ARMOR_HOLD）處理，引擎不硬編（守 neuro-symbolic 線）。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| SPEC_EXTEND.md | 新增 | P1–P5 權威設計 |
| core/app/engine/engage_wiring.py | 修改（P1/P2） | WeaponEntry + weapons_for + ammo_by_weapon seed + make_combined_weapons_for |
| core/tests/unit/test_engage_wiring.py | 修改（P1） | +7 測試 |
| core/app/adjudication/combined.py | 新增（P2） | resolve_combined_engagement 純函數 + CombinedWeapon |
| core/app/adjudication/engagement.py | 修改（P2） | EngagementResult 加 ammo_spent_by_weapon 欄 |
| core/app/adjudication/adjudicator.py | 修改（P2） | combined_weapons_for gating + _resolve_combined + _apply 逐武器扣彈 |
| core/app/sim_runtime.py | 修改（P2） | 接線 make_combined_weapons_for |
| core/tests/unit/test_combined_engagement.py | 新增（P2） | 9 純函數測試 |
| core/tests/unit/test_adjudicator.py | 修改（P2） | +2 gating 測試（combined vs 單武器落回） |

## 測試證據
- P1：`test_engage_wiring.py` → **21 passed**（+7）；ruff 綠、mypy 181 clean、golden **6 passed（不受影響）**。
- P2：`test_combined_engagement.py` **9 passed** + `test_adjudicator.py` **7 passed**（+2 gating）；
  engagement/volley/strength/weapons/missile/weapon/combined/adjudicator/scripted_battle/replay 合計 **92 passed（無回歸）**；
  ruff 綠、mypy **182 files clean**、**golden replay 6 passed（gating 保單武器路徑不變，不需重錄）**。

## 決策與陷阱
- 武器清單**穩定排序**（依 weapon_id）：P2 每武器一次 dispersion 抽樣的順序需決定性，才能 replay。
- per-weapon 彈藥 seed **僅在鍵不存在時寫**：避免執行期重啟把 Redis 已扣量重置回 DB 初值。
- WeaponEntry.ammo = DB 初始彈藥（供 seed）；執行期活彈藥在熱狀態 ammo_by_weapon（P2 讀）。

## 中斷續作指引（⚠ 停筆前更新）
- **下一步第一件事**：**P3 fire_policy**（契約先行）。改 `contracts/`（Order/EngageCommand payload 加 `fire_policy` enum：FREE/SMALL_ARMS_ONLY/ANTI_ARMOR_HOLD/SINGLE:<id>，選填預設 FREE）→ buf/openapi 驗證 → 再實作。`EngageCommand` 加 `fire_policy` 欄；precheck 傳遞；`combined.py` 的逐武器篩選加「政策允許？」判斷（SMALL_ARMS_ONLY 排除 missile/ATGM 類；ANTI_ARMOR_HOLD 對非裝甲目標排除反裝甲；SINGLE 只留指定武器＝相容現況）。政策**只篩選不改物理數值**。AI decider 輸出 schema 加 fire_policy（選填）。
- **P3 判斷武器類型**：SMALL_ARMS vs 反裝甲，可用 `WeaponProfile.kinetic_kind`（SMALL_ARMS/ATGM/…）或 `missile` 旗標；ANTI_ARMOR_HOLD 用「pk 對目標裝甲是否有效」判斷（或 kinetic_kind ∈ 反裝甲集）。
- **目前卡點**：無。
- **尚未驗證的假設**：contracts 的 Order payload 目前如何定義 ENGAGE 欄位（weapon_id 等）——P3 開工先讀 `contracts/` 找 ENGAGE payload schema 位置再加 fire_policy。
