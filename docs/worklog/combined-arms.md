---
task: "#44–#48 (SPEC_EXTEND P1–P5)"
status: DONE   # P1–P4 完成；P5 依 SPEC 為後續（僅記設計方向），列 #48 追蹤
started: 2026-07-23T00:00+08:00
updated: 2026-07-23T04:00+08:00
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
- [x] **P4 (#47)** COP 武器組合 UI + fire_policy 下拉 + 戰況 feed 聯合火力標示 ✅（AAR 頁逐武器表列為 follow-up）
- [x] **P4.5 (#49)** precheck 聯合兵種可達性：未指定武器時對武器組合逐件判可達，任一可打即 feasible + 0 彈武器不算可打 ✅（實測發現：舊 precheck 只判主武器 LOS 擋死聯合令）
- [x] **#51 失敗原因報告修正**：全數不可打時舊碼 max(passed) 挑到 cheap-first 空彈武器→誤導成 NO_AMMO(Javelin)。改為**逐武器**列出各自失敗原因、**有彈者優先當代表**（決定錯誤碼與標題）→ B1→R1 由「ORDER_NO_AMMO/Javelin 無彈藥」正名為「ORDER_NO_LOS/ATGM 地形遮蔽」+ 4 武器逐列 breakdown。**釐清：引擎本來就逐武器獨立評估、空彈武器從不擋其餘武器；問題只在訊息挑錯代表**。✅
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
- **P4 完成**：`cop.vue` ENGAGE 面板——武器 select 預設項改為「聯合火力（全武器一起打）」（≥2 武器時）；未選單一武器且 ≥2 武器＝聯合模式（`combinedMode`）→ 顯示 **fire_policy 下拉**（FREE/僅輕兵器/反裝甲留給裝甲，型別 `components['schemas']['FirePolicy']`）+ **武器組合清單**（各武器 名稱/射程/彈量）；指定單一武器→單武器路徑（彈種選擇）。下令 payload：未選武器且政策非 FREE → 夾 `fire_policy`。`clearSelection` 重置 firePolicy。戰況 feed（`formatEvent`）：`payload.mode==='COMBINED'` → 標「（聯合火力）」；後端 `broadcaster.build_event_envelope` 把 `mode` 帶進 EVENT envelope（非 ledger hash，golden 不受影響）。前端 lint/typecheck 綠、載入無 console error。
- **P4 掛帳（follow-up）**：AAR 頁的逐武器明細表列（`ai_decision.per_weapon[]` 已存進 ledger，AAR 讀得到；但 AAR 儀表板頁的表格渲染未做）。live feed 已標聯合火力。demo session 單位多為單武器 → 聯合 UI 的完整視覺 demo 待有 ≥2 武器單位的想定。

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
| core/tests/unit/test_adjudicator.py | 修改（P2/P3） | +2 gating + 2 drain/explicit-weapon 測試 |
| contracts/core_api.yaml | 修改（P3） | FirePolicy enum + ENGAGE payload fire_policy? 文件 |
| contracts/ai_output.schema.json | 修改（P3） | tactical_order 加選填 fire_policy |
| platform/app/types/api.ts | 生成（P3） | gen:api 重生（FirePolicy） |
| platform/app/pages/session/[id]/cop.vue | 修改（P4） | ENGAGE 武器組合 UI + fire_policy 下拉 + feed 聯合火力標示 |
| core/app/state/broadcaster.py | 修改（P4） | EVENT envelope 帶 mode（供 feed 標示交戰型態） |
| core/app/orders/precheck.py | 修改（P4.5） | _precheck_engage_any：武器組合任一可達即 feasible（cheap-first 短路 + 0 彈不算可打） |
| core/tests/unit/test_order_precheck.py | 修改（P4.5） | +4 測試（直瞄擋但飛彈可達/全直瞄擋/0彈不算/指定武器走單路徑） |

## 測試證據
- P1：`test_engage_wiring.py` → **21 passed**（+7）；ruff 綠、mypy 181 clean、golden **6 passed（不受影響）**。
- P2：`test_combined_engagement.py` **9 passed** + `test_adjudicator.py` **7 passed**（+2 gating）；
  engagement/volley/strength/weapons/missile/weapon/combined/adjudicator/scripted_battle/replay 合計 **92 passed（無回歸）**；
  ruff 綠、mypy **182 files clean**、**golden replay 6 passed（gating 保單武器路徑不變，不需重錄）**。
- P3：`test_combined_engagement.py` **14 passed**（+5 政策）+ `test_adjudicator.py` **9 passed**（+2 drain/gating）；
  契約 YAML/JSON 合法、schema_sync OK（16 tables/139 columns）、前端 gen:api + typecheck 綠；
  mypy 182 clean、golden 6 綠不受影響、engagement 區 51 passed 無回歸。
- P4：前端 `npm run lint` + `npm run typecheck` 綠、cop 頁載入無 console error；後端 broadcaster
  ruff/mypy 綠 + broadcast/event 24 passed；golden 6 綠（envelope 非 ledger hash，不受影響）。
- **P4 實測（session e2e-orders，B1 有 4 武器）**：ENGAGE 面板正確顯示「聯合火力（全武器一起打）」+
  火力政策下拉 + 武器組合清單（ATGM/Javelin/RIFLE_556/AUTOCANNON_30 各射程/彈量）；單位卡拖曳可用。
  **發現**：舊 precheck 只判主武器 LOS → B1→R1 被 ORDER_NO_LOS 擋死（其實有頂攻武器）→ 促成 P4.5。
- P4.5：`test_order_precheck.py` **16 passed**（+4）；ruff/mypy 綠、golden 6 綠；重建 core 容器上線。
  **實測**：P4.5 上線後同一張 B1→R1 聯合 ENGAGE **precheck feasible（201, combined_fires: 可由 Javelin，50ms 零 terrain 呼叫）**→ 活 sim COMBINED 裁決（mode=COMBINED、per_weapon 逐武器原因）→ order COMPLETED。此單位因 Javelin 無彈 + 其餘被擋/超射程 → 該次全 REJECTED（正確物理，非 bug；促成 0 彈不算可打的把關）。

## 決策與陷阱
- 武器清單**穩定排序**（依 weapon_id）：P2 每武器一次 dispersion 抽樣的順序需決定性，才能 replay。
- per-weapon 彈藥 seed **僅在鍵不存在時寫**：避免執行期重啟把 Redis 已扣量重置回 DB 初值。
- WeaponEntry.ammo = DB 初始彈藥（供 seed）；執行期活彈藥在熱狀態 ammo_by_weapon（P2 讀）。

## 中斷續作指引（⚠ 停筆前更新）
- **現況**：**P1–P4 全部完成並各自 commit**（分支 feat/combined-arms-fires，未 push）。單位以武器組合聯合兵種交戰全鏈路上線：資料層武器清單 + per-weapon 彈藥 → combined 加總裁決（射程帶/裝甲類自動匹配、gating 保 golden）→ fire_policy 火力政策（契約 + 篩選）→ COP 武器組合 UI + feed 標示。
- **P5（#48）依 SPEC_EXTEND 為「後續/較大、本階段只記設計方向」→ 不在本輪實作**（目標編成組成逐平台消耗 + 多目標火力分配；需 model/contract 擴充 + 可能 golden 重錄）。
- **兩個較小 follow-up**：(1) AAR 儀表板頁渲染 `per_weapon[]` 逐武器明細表（資料已進 ledger）；(2) 聚合 Lanchester（#33a）攻擊係數改用武器組合加總（目前仍用主武器 pk）。
- **驗證聯合 UI 需 ≥2 武器單位的想定**：demo session 單位多單武器，完整視覺 demo 待建對應想定或用 ORBAT 編輯器加第二種武器。
- **目前卡點**：無。
