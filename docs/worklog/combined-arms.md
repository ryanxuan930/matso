---
task: "#44–#48 (SPEC_EXTEND P1–P5)"
status: IN_PROGRESS
started: 2026-07-23T00:00+08:00
updated: 2026-07-23T03:00+08:00
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
- [x] **P3 (#46)** fire_policy 契約 + 接線 + AI decider 欄 ✅
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
- **P3 完成**：契約先行——`core_api.yaml` 加 `FirePolicy` enum（FREE/SMALL_ARMS_ONLY/ANTI_ARMOR_HOLD）+ ENGAGE payload 文件 `fire_policy?`；`ai_output.schema.json` tactical_order 加選填 fire_policy；前端 `npm run gen:api` 重生 types（FirePolicy enum）。`combined.py` 加 `fire_policy` 參數 + `_policy_allows`（SMALL_ARMS_ONLY 排除反裝甲/重火力[missile 或 kinetic_kind∈{ATGM,TANK_MAIN_GUN,RECOILLESS,ROCKET,AUTOCANNON}]；ANTI_ARMOR_HOLD 反裝甲僅在 pk>0 才用），HELD 武器不發射/不耗彈/不抽 dispersion；全被保留→REJECTED HOLD_FIRE。`EngageCommand.fire_policy` + drain 讀 payload。**gating 改**：`weapon_template_id is None`（未指定單一武器）才走 combined——**指定 weapon_id＝操作員選單武器＝走既有單武器路徑**（SINGLE 即此，無需獨立 enum 值）。
- **決策：SINGLE 政策 ≡ 指定 payload.weapon_id**（走既有單/齊射路徑），故 FirePolicy enum 只留 3 值，未加 SINGLE。

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
- P3：`test_combined_engagement.py` **14 passed**（+5 政策）+ `test_adjudicator.py` **9 passed**（+2 drain/gating）；
  契約 YAML/JSON 合法、schema_sync OK（16 tables/139 columns）、前端 gen:api + typecheck 綠；
  mypy 182 clean、golden 6 綠不受影響、engagement 區 51 passed 無回歸。

## 決策與陷阱
- 武器清單**穩定排序**（依 weapon_id）：P2 每武器一次 dispersion 抽樣的順序需決定性，才能 replay。
- per-weapon 彈藥 seed **僅在鍵不存在時寫**：避免執行期重啟把 Redis 已扣量重置回 DB 初值。
- WeaponEntry.ammo = DB 初始彈藥（供 seed）；執行期活彈藥在熱狀態 ammo_by_weapon（P2 讀）。

## 中斷續作指引（⚠ 停筆前更新）
- **下一步第一件事**：**P4 前端**。`platform/app/pages/session/[id]/cop.vue` 的 ENGAGE 面板：由「單選武器」升級為**顯示武器組合**——列出單位武器清單（`GET /units/{id}/weapons` 已回全部武器；標示各自 in-range/彈量/是否被政策排除），加 **fire_policy 下拉**（FREE/SMALL_ARMS_ONLY/ANTI_ARMOR_HOLD，型別 `components['schemas']['FirePolicy']` 已生成）；保留「指定單一武器」＝送 payload.weapon_id（走單武器路徑）。下令送出時把 fire_policy 放進 ENGAGE payload。AAR：`ENGAGEMENT_RESOLVED.ai_decision.per_weapon[]` → 逐武器命中/毀傷/彈藥明細呈現。
- **P4 注意**：現有 ENGAGE UI 用 `weaponId`（EquipmentInstance.id）單選 + `ammoType`。新流程「不選武器＝聯合兵種、選武器＝單武器」。fire_policy 只在「不選武器」時有意義（選了武器就是單武器，政策無用）。
- **目前卡點**：無。
- **尚未驗證的假設**：cop.vue ENGAGE 面板目前的 weaponId/ammoType 綁定與送單 payload 結構——P4 開工先讀該區塊。P4 需要重啟 dev server 或靠 HMR（nuxt.config 未改，應 HMR 即可）。
