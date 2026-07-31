# SPEC_V2 未完成項盤點（逐卡查證）

> 產出方式：8 個 agent 平行**查證程式實際狀態**（非讀文件），2026-07-31。
> 觸發原因：SPEC_V2 的「狀態」欄與各卡 ✅ 已被證實不可信——本週活體測試抓到三個標 ✅ 卻壞掉的功能。

## 為什麼不能只讀 SPEC_V2 的狀態欄

兩類問題都存在：

- **標空白但其實做了**：總表第 11/14/16/26/28/30/31 項狀態欄空白，路線圖裡 C4/C7/C9/E2/E4/F1/F3 卻都是 ✅。
- **標 ✅ 但實際是壞的**——本盤點抓到 4 張。共同成因：**測試餵給函式的資料，不是引擎真的會產生的資料**。

## 總覽

共 43 張卡：未開始 22、已完成 9、部分完成 8、⚠ 假完成 4


---

## 開發順序（依查證結果排定，與 SPEC_V2 §7 路線圖不同）

排序原則：**先修好「知道自己有沒有壞」的能力，再往上疊功能。**
這個系統目前最大的風險不是缺功能，是有功能但沒效果，而且既有測試抓不到。

### 第 0 階段 · 修好量尺與假 ✅（進行中）

| 項 | 卡 | 規模 | 為什麼排最前 |
|---|---|---|---|
| 1 | **AI eval gate 修復** | M | CI 那條 gate 結構上不可能變紅，量的是 jsonschema 有沒有裝好。**壞掉的量尺讓所有其他卡的「已驗證」都打折。** |
| 2 | **D6.2 AAR 統計對帳** | S | 命中率的分子壞掉（絕大多數交戰不計入）、分母含被拒交戰、聚合戰損單側入帳。畫面上有錯誤數字，零 golden 風險。 |
| 3 | **A1 補洞** | S | `LiveMissionPlanner` 繞過迷霧投影讀 ground truth，且不受 `ai_ground_truth` 開關管——A1 被 A2 打穿。 |
| 4 | **C2 破障工時 + 同型掃描** | M | 與 WP-C1 完全同型的時間尺度 bug；順便掃出所有「註解宣稱單位、消費端拿不到 tick_rate_ms」的常數。 |

### 第 1 階段 · C7 後勤真的能跑（L）

一整張 ★★★ 卡 100% 是死的（五層中性關閉），且是 D5／C8／H4 的前置。
**做法**：一旦真的播進 `supply`，熱狀態鍵集變了 → 8 個 golden 全要重錄。
正解是照 C1/A2 的招數**新增含補給的 golden 案例**、既有的維持不動。

### 第 2 階段 · V2.1 真正收尾

- **B5.2 空殼補完**（M）：`AIR_RECON` / `RESUPPLY_VOUCHER` 核准後什麼都不會發生。
  優先於 B5.4——「核准了卻沒效果」比「功能不存在」更誤導操作員。
- B5.4 標繪分送 + 殲敵 REPORT（M）
- G2 Tailwind 移除（S）：查證影響為零。現況比 SPEC 描述更糟——plugin 真的掛著在跑，
  每次 build 載 oxide 二進位、掃描一輪、產出零位元組 CSS。
- Backlog：`Message`/`Request` 完全不在 checkpoint 內，回滾後已 EXPENDED 的核准單不會回到 APPROVED。

### 第 3 階段 · D0（新卡）：生產路徑決定性證明 + 離線 kernel 工廠（L）

**SPEC 沒有這張卡，因為它以為地基已經有了。**
決定性只在合成 kernel 上被證明過（`replay/harness.py` 跑的是手工組的 NoOp Kernel），
與生產路徑 `sim_runtime._run_session`（530 行、硬綁 Redis/DB）沒有交集。
「同想定同 seed 位元一致」**從未在生產路徑上被驗證**——而那正是 D1 的驗收條文。
這是 D1／C6.4／D2／D4 共同的閘門。

### 第 4 階段 · D1 → D2 → C6.4

蒙地卡羅批次 → MOE 框架 → 用批次做一次聚合係數校準
（避免又一次憑感覺調數字；面射擊校準的教訓：錨點是假設不是量測）。

### 第 5 階段 · F5 訓後評量（XL）

**前置未被 SPEC 認識到**：B5/C10 的事件鏈**不在 Ledger 裡**。
申請/核覆/下令的時戳只存在於 `Request` 與 `Order` 兩張關聯表，
Ledger 的 44 種 event_type 裡一個下令或審批事件都沒有。
SPEC 寫的「評量引擎（純函數，讀 Ledger）」目前無從實作——要先補事件。

### 其餘（無強依賴，可插隊或押後）

C6.1（真正擋路的是 RNG stream 汙染，不是 golden）、C6.3 #48（XL）、
D3／D4／D5／D7、C8、E5、F2、G5、G6、H1–H4、A4、B3、H4。

> ⚠ **G5 的驗收條件是壞的，接手前要先改掉。**
> SPEC 寫「`rg "interface.*View" app/` 無契約外重複定義」——這條指令現在只抓到 2 筆，
> 改兩個 interface 名字就能宣告完成，而 9 個檔、40+ 個手寫 API 型別、19 條契約漂移一條都沒動。
> 這正是本 repo 招牌病的上游成因：驗收條件是機械的，但機械得不對地方。

### 兩個規格前提經不起查證（做相關卡之前必讀）

1. **D1 的「決定性引擎已是完美地基」只對一半**——見第 3 階段。
2. **H1 的「golden replay 證明了事件流重放＝狀態重建」不成立**。
   Golden 證明的是「相同 seed 從 tick 0 重跑的確定性」，全程不碰 WS envelope。
   而且 SPEC 寫的「Relay 收全流自行按 faction 過濾」照字面做**會洩漏迷霧**：
   每陣營投影是在 broadcaster 生產端做的，`public_diff()` 把位置凍結的輸入欄位剝掉了，
   Relay 自行重投影會讓遠端站看到斷聯敵軍的即時真實座標。
   **正解是轉發主站已投影的副本**，這句必須寫進 ADR。

---

## ⚠ 假完成（4）

**程式在、測試綠、活局裡沒有效果。** 這一類最危險：文件標 ✅，沒有人會回頭看。


### A1 — AI 敵情走真實偵測投影（迷霧誠實化）

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:150 標 ✅ 2026-07-29；總表第 1 項 ✅

**查證證據**

LLM worker 路徑查證無誤：orchestrator.py:175 `enemy_visibility = ground_truth_enemies if use_ground_truth else contacts_from_intel`，`ai_ground_truth` 預設 false（orchestrator.py:174、api/autonomy.py:61），worker.py:172 走 `projected_snapshot` + `contacts_from_intel`，盟軍走 allied_units、recent_events 走 Ledger 受眾過濾。**但另一條路徑漏了**：`core/app/engine/mission_wiring.py:213-224` 的 `LiveMissionPlanner._world_view()` 直接 `from app.ai_loop.worker import ground_truth_enemies` 並呼叫它。`rg -n "ground_truth_enemies" core/app --glob '!*test*'` 只有兩個生產呼叫端：orchestrator.py:175（有開關保護）與 mission_wiring.py:218（無保護）。LiveMissionPlanner 確實接在生產 Kernel 上（sim_runtime.py:46 import、:738 `mission_planner=LiveMissionPlanner(`）。`orders/decomposer.py:9` 的模組說明白紙黑字宣稱「world_view 是**已經過迷霧投影**的 build_faction_context() dict」——與呼叫端事實不符。SPEC_V2:251 的 A1 陷阱原文就是「分解器讀的 world_view 必須走迷霧投影，否則 AI 經由任務分解偷看 ground truth，A1 白做」。

**還缺什麼**

`core/app/engine/mission_wiring.py:_world_view` 改呼叫 `app.ai_loop.world_view.contacts_from_intel`（簽名相同，直接替換即可），並讀該局 ai_config 的 `ai_ground_truth` 退回開關（目前這條路徑完全不受開關影響）。補一條測試：RED 未被 BLUE 偵測時，BLUE 的 SEIZE 任務分解不得產生針對該單位的子令。decomposer.py 的 import 白名單測試擋得住分解器自己偷看，擋不住呼叫端餵髒資料——守門要往上移一層。

**風險**：換成 contacts_from_intel 之後任務分解會「看不到敵人」，SEIZE 的接敵階段行為會改變——那是正確行為（SPEC_V2:252 明說對鬼 contact 下 ENGAGE 是對的），但會被誤讀成 A2 壞掉。golden 案例 `mission_seize_60` 是手搭純記憶體 Kernel、不走 DB/IntelService，理論上不受影響，但要實跑確認。

**使用者價值**：AI 指揮官下任務令時不再全知：統裁做「有無迷霧」對照實驗時數字才可信，否則 A1 的 22/23、25/26 實測結論只涵蓋一半路徑。


### C2 — 障礙工事與工兵裁決（雷區/斷橋/鐵絲網）

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2:537 標 ✅ 2026-07-30（前端下令 UI 未做）；總表第 10 項同註

**查證證據**

**SPEC 的『未竟』清單已過時**（三項都做完了）：ENGINEER 下令 UI 存在（`platform/app/components/cop/UnitsOrderPanel.vue` `<option value="ENGINEER">障礙作業（破障/設障）</option>`、`useCopOrdering.ts` 有 500m 距離與工兵資格檢查）；`blocks_road` 有消費者了（`engine/obstacle_wiring.py:90 road_is_cut` ← `engine/movement.py:396 if factor is not None and road_is_cut(here): factor = None`）；地圖編輯器選得到型別與密度（`useMapEditor.ts:234-235 attrs.obstacle_type / attrs.density`）。觸雷鏈完整：movement.py:412 `_roll_mine` → obstacle_wiring.roll_mine_strike → 扣戰力 + `apply_mine_suppression` + 令即結束。**但有一個與 WP-C1 完全同型的時間尺度 bug**：`core/app/adjudication/obstacles.py:44` 的 `breach_time_ticks` 註解寫死「1 tick = 1 分鐘」，:60-66 的表是 45/20/60/30/120（雷區 45 分、斷橋 2 小時）；消費端 `core/app/engine/obstacle_wiring.py:192-193` `work = breach_ticks(...)` / `payload["_work_until_tick"] = tick + work` **完全沒有 tick_rate_ms 參數**，`drain_engineer_orders(db, session_id, tick)` 的簽名裡也沒有（sim_runtime.py:232 呼叫端同樣不傳）。對照 commit d67fe61「壓制/工事寫死 1 tick=1 分鐘」——那一輪只修了 `adjudication/suppression.py`（posture_ticks/decay 都收了 tick_rate_ms 參數），**障礙破障工時漏掉**。SimParams 的 tick_rate_ms 下限是 1000（sim_params.py:158），想定 schema 預設 60000 但 armor-breakthrough 的註解自承「**絕對不要寫 1000**——那是 schema 預設值」，代表這個值真的被踩過。另：`rg obstacle_type scenarios/` 零命中——沒有任何示範想定宣告障礙，障礙只存在於白軍手畫。

**還缺什麼**

①`core/app/adjudication/obstacles.py` 把 `breach_time_ticks` 改成 `breach_time_minutes`，加 `breach_ticks(otype, tick_rate_ms=DEFAULT_TICK_RATE_MS)`（照 suppression.py:89 `minutes_per_tick` 的形狀）；`engine/obstacle_wiring.py:drain_engineer_orders` 與 `sim_runtime.py:232 _engineer_tick` 都要多收 tick_rate_ms。②`contracts/scenario.schema.json` 加 map_features/obstacles 宣告區段 + loader 落地，否則 armor-breakthrough 的 CPX 驗收要靠手畫雷區。③A* 路徑規劃仍不避障（movement 只逐 tick 檢查行經格），SPEC 的「工兵先行破障後同路線通過無損」得靠人工排路線。④障礙 contact 偵測（敵障礙轉本軍標註）未做。

**風險**：tick_rate_ms 預設等同 60000，改完 golden 位元不變（同 d67fe61 的做法）——但**要確認既有局進行中的 ENGINEER 令**：`_work_until_tick` 已寫進 payload 的令會沿用舊值，改法要向後相容（讀得到就不重算）。想定宣告障礙會動到 scenario schema，屬紅線 4「契約先行」。

**使用者價值**：破障真的要花 45 分鐘而不是 45 秒——工兵先行、主力後續的時序決定變成一個真的要權衡的事；想定作者可以直接在 scenario.yaml 佈雷而不用開局後手畫。


### C7 — 後勤體系化：補給類別、消耗、修復、整補

**規模** L　|　**前置** B2　|　**文件說** SPEC_V2:700 標 ✅ 2026-07-30（C7.1/C7.2/C7.3 三卡全數完成）；總表第 14 項狀態欄空白

**查證證據**

**這是本次盤點最嚴重的一條：三張卡的程式全在、測試全綠、活局裡一行都執行不到。**五層中性關閉，且沒有任何生產路徑打得開：①**`supply` 熱狀態鍵零生產寫入端**——`rg -n "SUPPLY_KEY|\"supply\"" core/` 除 `engine/supply_wiring.py`（定義 + 更新既有值）與 `engine/refit_wiring.py:202`（同）外，只命中 `core/tests/unit/test_supply.py`、`test_refit.py`、`test_supply_points.py`。`engine/engage_wiring.py:268 seed_combat_state`（PROGRESS 自承是「熱狀態鍵集的單一寫入路徑」，且有 AST 守門測試釘住）播 strength/authorized_strength/health/footprint_m/ammo/ammo_by_weapon/platform_count——**沒有 supply**。想定/ORBAT 宣告不了（SPEC_V2:714 自承）；MSEL `MODIFY_UNIT`（msel_actions.py:205-250）只認 strength/lat/lng；沒有 API。②因此 `supply_wiring.py:52 read_levels` 恆回 `{}` → `tick_supply` 第 89 行 `if not levels: return None` 恆早退 → `starved_days` 永不寫 → `supply_effectiveness`（`adjudication/adjudicator.py:237` 與 `:319` 兩個真實消費點）恆為 1.0。③`needs_resupply` 恆回 [] → `auto_resupply` 永不撥交。④**`MapFeature(kind="SUPPLY_POINT")` 沒有建立路徑**：`rg SUPPLY_POINT` 的建立端只有 `core/tests/unit/test_refit.py:32` 與 `test_supply_points.py:41`；前端可選 kind 只有 OBSTACLE/BUILDING/WEAPON_EMPLACEMENT/CONTROL_MEASURE/TERRAIN/ANNOTATION（`useMapFeatures.ts:95-100`），`contracts/core_api.yaml:948` 的 kind 說明也沒列 SUPPLY_POINT，想定 schema/loader 同樣沒有。⑤`refit_wiring.py:43 REPAIR_PER_DAY = 0.0` → `refit_tick` 第 99 行 `if repair_per_day <= 0: return []` 恆早退；`adjudication/supply.py:52 DAILY_CONSUMPTION` 的 I 與 IX 都是 0.0。SPEC 的驗收條文「斷補的裝甲連 3 模擬日後效能階梯下降」「打掉補給點後下游單位水位不再回升」在**任何真實的一局裡都不可能發生**。

**還缺什麼**

①**初值播種**（最關鍵）：`contracts/orbat.schema.json` 加 `supply: {I: {on_hand, capacity}, IX: {...}}`（或由 EquipmentTemplate LOGISTICS 的分艙 capacity 導出）→ `scenario/loader.py` 落 TacticalUnit.attributes → `engine/engage_wiring.py:seed_combat_state` 播進熱狀態的 SUPPLY_KEY（注意它有 AST 守門測試，動它要一起改）。②**補給點建立路徑**：`contracts/core_api.yaml` 的 MapFeature kind 加 SUPPLY_POINT + `attributes.stock`，`platform/app/composables/useMapFeatures.ts` 的 kind 清單加一項並讓 MapEditorPanel 編得了庫存，想定 schema 加 supply_points 區段 + loader 落地。③**預設值**：`supply_daily_rates` 與 `repair_per_day` 至少讓四個示範想定其中一個給非 0 值，否則沒有人會發現它從沒跑過。④**前端顯示水位與斷補/整補狀態**（單位卡目前只有油料列）。⑤MSEL `MODIFY_UNIT` 支援 supply 欄位（白軍軟裁決通道）。⑥LOGISTICS capacity 的類別命名（AMMO/FUEL/WATER_FOOD/BATTERY）與 Class 編號不一致（要改契約）。

**風險**：一旦真的播進 `supply`，熱狀態鍵集變了 → `compute_state_hash` 變了 → **8 個 golden 全部要重錄**（現在之所以「golden 未動」正是因為這條路一次都沒跑）。重錄會摧毀 golden 唯一的價值（SPEC_V2:1223 那段論證），所以正解是照 C1/A2 的招數**新增含補給的 golden 案例**、既有的維持不動。另一個坑：`write_levels` 依類別名排序是為了雜湊穩定（supply_wiring.py:69），播種端也必須照同一個順序寫。還有 rollback——`SUPPLY_TICK_KEY` 的「按經過 tick 補算」設計依賴回滾把時間戳一起帶回去，播種若走 DB 而非熱狀態會破壞這個保證。

**使用者價值**：目前使用者看到的差別是**零**——後勤在任何一局裡都不存在。做完之後：斷糧的連隊射擊效能會階梯下降、水位低於三成會自動去補給點拉貨、打掉敵補給點下游就補不到、戰損單位退到補給點後方才修得起來。「打擊敵後勤」從一句規格文字變成一個真的能執行的戰法。


### D6.2 — AAR 統計對帳：聚合交戰雙側入帳 + 命中率分母/分子語意修正

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:965「2. **統計對帳**：聚合交戰雙側入帳（隨 WP-D2）；命中率分母語意修正。」總表第 23 列標「D6.2/D6.3 未做」。**文件低估了嚴重性**：它只說「分母語意」，實際上分子已經在生產路徑上失效。

**查證證據**

**分子壞掉，是新發現的第三型缺陷**：`core/app/aar/stats.py:60` 只認 `e.ai_decision.get("hit")`，而 `rg '\"hit\"' core/app/adjudication/` **全 repo 只有一處**：`core/app/adjudication/engagement.py:176`（單發路徑）。齊射 `engagement.py:258-265` 與聯合兵種 `combined.py:255-266` 寫的是 `"status": "HIT"/"MISS"`，**沒有 `hit` 鍵**。而 `engagement.py:142` 的閘門是 `if shooter.quantity > 1 and can_volley: return _resolve_volley(...)`——即**只要單位建制數 >1（幾乎所有班/排/連）就走齊射**，`adjudicator.py:223` 持 ≥2 武器系統走 combined。結論：真實推演的絕大多數交戰不計入 `hits`，`hit_rate` 在畫面上恆偏低甚至為 0。**有可見消費者**：`platform/app/pages/session/[id]/aar.vue:108` 直接顯示「命中率 X%」、`core/app/aar/narrative.py:56-57` 寫進 AAR 講評、`core/app/exercise/archive.py:171` 存進封存。**測試是綠的且正是綠燈原因**：`core/tests/unit/test_aar.py:284` `assert m.hits == 2 and m.hit_rate == 1.0` 用的是手寫合成事件（含 `hit` 鍵），與生產事件形狀不同。**分母語意也未修**：`stats.py:77` `individual = counts.get("ENGAGEMENT_RESOLVED", 0)` 把 REJECTED（OUT_OF_RANGE/NO_AMMO/HOLD_FIRE，見 `combined.py:219`、`engagement.py:307`）也算進分母。**聚合雙側入帳未修**：`stats.py:64-70` 仍是 `dmg = e.damage_calc; damage_by_faction[faction_of[e.target_id]] += dmg`，而 `core/app/adjudication/aggregate.py:99` 寫 `damage_calc=a_loss + b_loss`——守方一側被記上雙方總損失。D6.1 只在 `aar/replay.py` 修掉這個錯，`aar/stats.py` 原封不動。`README.md:485` 自己就寫著這兩條，但沒人動。

**還缺什麼**

1. `core/app/aar/stats.py:60` 改判 `ai_decision.get('status') == 'HIT'`（或 `hit` 為真），並定義 status 的權威來源。2. `stats.py:77` 分母排除 `status == 'REJECTED'`，新增 `attempts`/`engagements_fired` 兩個分開的欄位（別讓一個數字承載兩種語意）。3. 聚合入帳：`stats.py:64` 對 `AGGREGATE_ENGAGEMENT_RESOLVED` 改讀 `ai_decision['initiator_loss']` / `['target_loss']` 分別歸給 initiator/target 的陣營，不再用 `damage_calc`；比照 `aar/replay.py:124` 的既有註解。4. 面射擊已由 `_area_losses`（`stats.py:29`）處理，別重複計。5. 契約 `AarStatsView` 若新增欄位需先改 `contracts/core_api.yaml` 再實作（紅線 4）。6. `core/tests/unit/test_aar.py` 的合成事件要改成**由真裁決函式產生**（`resolve_combined_engagement` / `_resolve_volley`），否則同一個洞會再開一次。7. 前端 `aar.vue:108` 與 `useAar.ts:43` 對應更新。

**風險**：①最大的坑是「修好之後數字會變」——`exercise/archive.py:171` 已把舊的 hit_rate 封存進歷史演習，修完新舊局的統計不可比，要在 UI 或封存記錄註明版本。②不要動 `damage_calc` 的寫入端（`aggregate.py:99`）來「順便修乾淨」：那會改 ledger canonical payload → hash chain 變動 → 既有局驗證失敗（紅線）。只能在讀端（stats.py）改判。③golden 不動（goldens 只 hash hot_state units，見 `core/tests/replay/harness.py:52`），這張卡零 golden 風險。

**使用者價值**：統裁看到的 AAR 命中率終於是真的（現在幾乎恆為 0 而沒人發現），各陣營承受戰損不再把攻方損失記到守方頭上——這是講評時直接引用的數字，錯了會教錯東西。CP 比極高的一張卡。


---

## 部分完成（8）


### B4 — 參數治理：凍結、簽證、審計

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:391 標 ✅ 2026-07-31

**查證證據**

寫入閘門是真的：`governance/guard.py:15 require_params_unsealed` 有四個生產呼叫端（`api/equipment.py:194/211/234`、`api/system.py:169`），`active_seal` 看 Exercise.phase ∈ {REHEARSAL, EXECUTION}（seal.py:50）。雜湊涵蓋 SimParams 正規化值 + 全域武器庫 + mobility_matrix 檔案內容（seal.py:55-88），刻意不用手寫的 version 欄。開局比對接在 `sim_runtime.py:440 violation = seal_violation(db, session_id)` 的 `_ensure` 早退。前端完整：`ExercisePanel.vue:285-315` 有 seal/unseal 按鈕、簽證雜湊 vs 當前雜湊對照、`⚠ 參數已被更動——此演習的局將拒絕啟動` 提示。**缺口在「留痕」**：`sim_runtime.py:441-446` 拒起時只做 `_LOG.error(...)` 並記進 `self._seal_refused` 集合，**沒有寫任何 Ledger 事件**（guard.py:47 的註解自承「呼叫端不可每輪落一次事件」，但結論是一次都沒落）。SPEC_V2:426 的驗收條文是「篡改後 session 重啟被拒**且事件留痕**」。

**還缺什麼**

①`core/app/sim_runtime.py:_ensure` 在第一次拒絕時寫一筆 Ledger 事件（例如 `SESSION_START_REFUSED_SEAL`，帶 exercise_id 與兩個雜湊前綴），並加進 `useCopFeed.ts` 的事件標籤表。②對操作員的即時可見性：目前一個被拒起的局在 lobby/COP 上就是「永遠不動」，沒有任何錯誤訊息——需要 `GET /sessions/{id}` 或 lobby 列表帶一個 `blocked_reason`。③`docs/PARAMS.md` 的 P 層 25+ 個硬編常數仍在 SimParams 之外（SPEC 自承的已知界線）。

**風險**：Ledger 是 hash chain，落事件的時機在 runner 尚未建立之前（`_ensure` 裡沒有 sim_clock），tick 要用什麼值得先想清楚——用 0 會和開局事件撞。掃描每 3 秒重來一次，去重邏輯（`_seal_refused`）必須先於寫入，否則帳本會被灌爆。

**使用者價值**：統裁看得到「這場的參數被人動過所以局起不來」而不是對著一個不動的畫面猜；AAR 也查得到誰在什麼時候動了參數。


### C4 — 環境演進：逐 tick 天氣、晝夜與照明、煙幕

**規模** M　|　**前置** C10　|　**文件說** SPEC_V2:604 標 ✅ 2026-07-30（C4a/C4b/C4c 三卡全數完成）；總表第 11 項狀態欄空白

**查證證據**

C4c 煙幕**完全接通**：`engine/engage_wiring.py:396` 與 `engine/sensor_wiring.py:192` 都在 terrain LOS 之後查 `blocks_los`，`smoke_wiring.py:60 drift(...)` 有生產呼叫（sim_runtime.py:640 註解自承「drift() 有完整實作與測試、生產零呼叫端」已補），`purge_expired_smoke` 掛上 pre_tick（sim_runtime.py:936）。C4a 晝夜**接通但資料稀**：`movement.py:371 self._light.level_at(now)`、`sensor_wiring.py:204 optical_range_modifier`、LightClock 建於 sim_runtime.py:629；但 `rg sunrise_min scenarios/` 只有 armor-breakthrough 一個宣告（其餘三個想定光照恆 DAY，整個 C4a 對它們是中性 1.0）；ILLUM 照明彈未實作；前端無晝夜呈現。**C4b 的中性預設把整條路關掉且沒有想定/UI 打得開**：`engine/weather_wiring.py:38 DEFAULT_REFRESH_TICKS = 0`（0 ＝永不刷新），`sim_params.py:103 weather_refresh_ticks: int = _wx.DEFAULT_REFRESH_TICKS`；唯一的開關在**全域** system-settings（`platform/app/pages/system-settings.vue:335 data-testid="sim-weather-refresh"`），`contracts/scenario.schema.json` **沒有這個欄位**——想定作者無法讓某一場的天氣會變。SPEC_V2:646 原文是「預設一模擬小時」，實作改成 0。`WeatherMode.REPLAY` 仍未接（SPEC 自承）。

**還缺什麼**

①`contracts/scenario.schema.json` 加 `weather_refresh_ticks` + `scenario/loader.py` 落地到 WargameSession（同 tick_rate_ms 那條路徑的做法，loader.py:481-484），讓「這一場天氣會變」是想定作者的決定而不是全站管理員的。②預設值決策：0 保守但等於整張 C4b 在出貨狀態下不存在——至少要讓四個示範想定其中一個開起來，否則沒有人會發現它壞掉。③`WeatherMode.REPLAY`：Ledger 記每次快照內容 + 重播照放（`plugins/weather_client.py` 與 `engine/weather_wiring.py`）。④ILLUM 火力任務（要局部短暫的光照覆寫實體，接 C10 的 FIRE_MISSION ammo_type）。⑤前端晝夜/煙幕呈現。

**風險**：打開 weather_refresh_ticks 會讓天氣在局中變動 → 移動速度（weather_mobility）、命中率、通聯衰減（comms 的 attenuation 吃 weather）三處同時漂移，含天氣的 golden 若有就要重錄；SPEC_V2:634 宣稱「刷新間隔預設 0 ＝永不刷新 ＝既有的單一快照行為，實測 8 個 golden 未動」——那個實測是在**關著**的狀態下做的，打開之後的漂移沒有被量過。ILLUM 需要新的實體型別，別塞進 SmokeCloud。

**使用者價值**：天氣真的會變（現在出貨狀態下是永遠不變的單一快照）；夜戰想定不用只靠 armor-breakthrough 一個範例；照明彈讓夜間攻擊有得打。


### C6 — 交戰引擎收尾總卡：多方混戰接線 + 聚合門檻 + #48 目標編成／多目標分配 + 聚合係數校準

**規模** L　|　**前置** C6.1、C6.3、C6.4　|　**文件說** SPEC_V2 行 690–699 標題無 ✅；總表第 13 項（行 103）狀態欄空白，描述「resolve_multiway_tick 已實作未用；threshold 忽略想定欄位」——後半段已經過時（門檻 2026-07-30 已接）。

**查證證據**

四個子項實測狀態不同：(1) 多方混戰 `core/app/adjudication/aggregate.py:152 resolve_multiway_tick` 零生產呼叫端；(2) 聚合門檻已完整接線（commit 573dba2）；(3) #48 在 TASKS.md 仍 pending 且程式零痕跡；(4) `_AGG_*` 佔位常數仍寫死在 `core/app/adjudication/adjudicator.py:79-82`，`core/app/sim_params.py` 內 grep `agg|lanchester|variance` 零命中。因此整卡 PARTIAL——只有 1/4 完成。

**還缺什麼**

見 C6.1 / C6.3 / C6.4 三張子卡；C6.2 已完成。建議把這張總卡拆成三張獨立可排程的卡，因為它們的前置與風險完全不同（C6.1 只碰引擎、C6.3 要動契約+前端、C6.4 卡在 D1）。

**風險**：最大風險是把四個子項當成一張卡做：C6.4 卡在 D1（蒙地卡羅引擎完全不存在），會把已經可做的 C6.1/C6.3 一起拖住。SPEC 標「golden：重錄」也不準——五份 golden（core/tests/replay/goldens/）沒有一份走聚合路徑。

**使用者價值**：完成後統裁能看到：三方以上同格混戰真的互相消耗（不再只有被下令的那一對），以及混編單位被正確地按組成分別殺傷。但單看總卡沒有可展示的能力，價值全在子卡。


### D6.3 — 匯出管線：串流分頁 CSV/SQL 多表匯出 + AAR bundle

**規模** M　|　**前置** D6.2　|　**文件說** SPEC_V2:966-969「3. **匯出管線**：[INDSR p.43–44] 的 CSV/SQL 匯出——`GET /sessions/{id}/export?format=csv&tables=events,units,shots`（串流分頁，修『全量載入記憶體』盤點項）；AAR bundle（JSON+C

**查證證據**

只有 O8.4 時代的單表匯出：`core/app/api/aar.py:255` `GET /{session_id}/aar/export?fmt=json|csv&anonymize=`，實作 `core/app/aar/export.py`，欄位固定 6 個（`export.py:17` `_CSV_FIELDS = [seq, tick, event_type, initiator_id, target_id, damage_calc]`）——**只有 events 一張表，沒有 units / shots**。前端入口存在：`platform/app/pages/session/[id]/aar.vue:252-254`（JSON / CSV / CSV 匿名化三顆按鈕）。**規格要的三件都沒有**：①`?tables=events,units,shots` 參數不存在（`rg '/export' contracts/core_api.yaml` 無命中，端點路徑是 `/aar/export` 而非規格寫的 `/sessions/{id}/export`）；②**串流分頁沒做**——`core/app/aar/events.py:47 read_events` 一次 `select(TacticalEventLog).where(...).order_by(seq)` 全表撈進 list，`export_json`/`export_csv` 再整份 build 成字串塞進 `Response`，正是規格點名的「全量載入記憶體」盤點項；③AAR bundle（JSON+CSV+想定包 zip）不存在（`rg 'bundle' core/app` 無命中）。

**還缺什麼**

1. 契約先行：`contracts/core_api.yaml` 新增 `/sessions/{id}/export`（format=csv|json|sql、tables 多選、streaming response）。2. `core/app/aar/events.py` 新增 `iter_events(db, session_id, chunk=1000)` 產生器（`yield_per`），**但注意 `read_events:65` 的 `superseded_seqs(rows)` 需要全表才能算出被回滾棄置的 seq 區間**——串流時要先跑一次輕量 query 取 ROLLBACK 事件算出 dead set，再串流過濾。3. `core/app/aar/export.py` 改 `StreamingResponse` + 逐 chunk 產 CSV row。4. units 表匯出：從 `TacticalUnit` + `EquipmentInstance.current_state`（彈藥/油料）導；shots 表：從 `ENGAGEMENT_RESOLVED.ai_decision.per_weapon`（`combined.py:200-210` 的逐武器明細）攤平——**這張表現在完全沒有出口，逐武器資料只活在 ai_decision JSON 裡**。5. bundle：zip(events.csv, units.csv, shots.csv, scenario.json via `core/app/scenario/dump.py`, metadata.json)。6. 匿名化要覆蓋新表（`export.py:20 _anon_map` 目前只掃 events 的 initiator/target）。7. 前端 `aar.vue:250-255` 匯出區加表格勾選 + bundle 按鈕。

**風險**：①`superseded_seqs` 的全表相依是串流化最容易踩的坑——沒處理好會把已回滾世代的事件匯出去，外部分析直接錯（重複計算戰損）。②迷霧：`_visible_events`（`api/aar.py:63`）對參與者投影，新表（units/shots）**必須套同一條規則**，否則開一個沒上鎖的敵情窗口（紅線 3）；D6.1 就漏過名冊投影一次（`api/aar.py:143-149` 的註解記著）。③匿名化聲明「不得含任何使用者名或單位真名」——新增 units 表會直接帶 designation，忘了套 anon map 等於違反既有承諾。④golden 不動（純讀路徑）。

**使用者價值**：參謀/分析官能把整場演習拉進 pandas/Excel 自己算——目前只能拿到一張 6 欄的事件表，逐武器射擊明細（誰用什麼武器打了幾發、命中多少）根本沒有出口。對「想自己分析」的使用者是從 0 到 1。


### F1 — SPEC_INGEST 完整版：PDF→Qdrant 端到端、真嵌入器落地、降級可見於 UI、檢索評測有資料

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2 §6 WP-F1「🟡 最小切片 ✅ 2026-07-30」，總表第 30 項狀態欄空白。最小切片的宣稱基本屬實（少見），但列出的四項未竟低估了實際缺口。

**查證證據**

**做到的**：ai/matso_ai/rag/embedder.py:84 `load_bge_m3()` 真的實作（惰性 import、local_files_only、取不到回 None）；`describe_embedder()`（embedder.py:110）給 degraded 旗標；ai/matso_ai/rag/ingest.py:56 `_build_embedder` 降級時印「⚠ 檢索品質降級」；`write_manifest`（ingest.py:66）把嵌入器記進 corpus_manifest.json；ai/matso_ai/evals/retrieval.py 的 hit@k 與「total=0 不算通過」都在。
**沒接線的**：
1) `describe_embedder` 的非測試消費端只有 rag/ingest.py 一處（`rg -l describe_embedder` → embedder.py / ingest.py / tests / 文件）。**沒有任何 API 或前端讀它**——`rg -n "degraded|embedder" platform/app platform/server` 只命中 UnitDetailCard.vue 的 comms-degraded CSS。SPEC 說的「UI 標示檢索品質降級」在前端不存在。
2) `evaluate_retrieval` **零生產呼叫端**：`rg -l "evaluate_retrieval"` 非測試只有它自己 + SPEC/PROGRESS/TASKS/worklog。沒有 CLI 進入點、沒有進 CI，且 ai/evals/ 底下**沒有任何 retrieval QA 對檔案**（只有 3 個決策 case yaml）→ 這個指標從實作完到現在一次都沒被跑過。
3) PDF 到 Qdrant **中間是斷的**：ai/matso_ai/ingest/cli.py 只有 convert/report/promote（PDF→staging markdown→corpus/），ai/matso_ai/rag/ingest.py 只吃 markdown→Qdrant，**沒有一條命令能一路走完**。
4) 語料實況：ai/rag/corpus/ 六個 collection 裡只有 doctrine_red/red_delay_ops.md 一份真內容，其餘只有 `_collection.md` 佔位——而 `_SKIP = {README.md, _collection.md, MANIFEST.md}`（ingest.py:28）會把佔位略過 → **可入庫語料 = 1 檔**。
5) **執行期會炸的 dim 陷阱**：ingest.py 的 `--dim` 預設 64，bge-m3 是 1024（embedder.py `BGE_M3_DIM`）。`--embedder bge-m3` 不同時給 `--dim 1024`，RagStore 會用 64 建 collection（store.py:42 `VectorParams(size=self._dim)`）卻 upsert 1024 維向量。所有測試都用 HashEmbedder，這條路沒有任何測試蓋到。
6) G5 引用查核在活執行期恆等於關閉：`rg -n "citation_verifier="` 非測試只在 core/app/ai_loop/worker.py:209,282 與 opfor.py:77 之間互相傳遞，**沒有任何組裝點傳進 `QdrantCitationVerifier`**（docs/DEPLOYMENT.md:52 描述的接線是願景，不是程式）。

**還缺什麼**

(1) ai/matso_ai/rag/ingest.py：`--dim` 改成由 embedder.dim 決定（或 `--embedder bge-m3` 時自動 1024），並加一條測試蓋維度一致性；(2) 新增 `ingest-pdf` 子命令把 ingest/cli.py 的 convert 與 rag/ingest.py 串成一條（含 OCR 惰性降級）；(3) `python -m matso_ai.evals.retrieval` CLI + `ai/evals/retrieval/*.json` 第一批 QA 對（5–10 條，對現有那 1 份 red_delay_ops.md 也做得出來）+ 進 CI（報數不擋）；(4) core/app/api/system.py 增 rag 區塊（embedder degraded / chunk 數 / manifest 時間），前端系統資訊頁顯示——降級目前只有 CLI stdout 看得到，操作員看不到；(5) sim_runtime/ai_loop 組裝點在語料非空時注入 `QdrantCitationVerifier`；(6) docs/DEPLOYMENT.md 補 air-gapped 資產清單（sentence-transformers + bge-m3 模型檔 + `MATSO_BGE_M3_PATH`）。

**風險**：最大的坑是 (5)：一旦真的載 bge-m3，向量維度從 64 變 1024，**既有的 Qdrant collection 必須整個重建**，不重建的話不是明確報錯就是檢索恆空——而「檢索恆空」在這個系統裡看起來跟「語料不足」一模一樣，會被誤判成資料問題。另一個是「以為降級看得見」：三處標示裡有兩處（stdout、manifest）操作員根本不會去看。RAG 不進 golden 路徑，golden 不需重錄；紅線無涉。注意使用者紅線：**不虛構語料**，QA 對只能對真的在庫的文件出題。

**使用者價值**：現階段使用者幾乎看不到差別（語料只有 1 份）。做完 (4)(5) 後，統裁在系統資訊頁看得到「檢索品質降級／語料 N 份」，AI 講的引用第一次可被查核為真——那是「敢不敢信 AI 那段話」的分水嶺。


### F3 — RoleManager 與 AIInvocationLog 接入活執行期

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2:1097 標 ✅ 2026-07-30；總表第 31 項狀態欄空白

**查證證據**

接線是真的：`ai_loop/decider.py:229 audit: bool = True`（預設開）→ `:244 manager = _make_role_manager(...) if audit else None` → `:257 _make_role_manager` 建 `RoleManager(client, log_writer=InvocationLogWriter(default_session_factory()), ...)`，建不起來回 None 並留 warning（不讓稽核掛掉停擺整個推演）；`decide()` 於 `:150 if self._role_manager is not None:` 走 `AIRequest(..., system_prompt=system)` 覆寫——**這一步很關鍵且做對了**：不帶 system_prompt 過去的話 RoleManager 會用註冊表的靜態 prompt，而 ReplayClient 按 prompt 雜湊重播，所有已錄自主場次會在那一刻全部失效。`orchestrator.py:122 decider_factory = make_llm_faction_decider` 是生產組裝點。llm_latency 指標也因此有了寫入端（role_manager.py:144）。**但規格四項只達成兩項**：①`role_manager.py:165 guardrail_result={"status": "not_evaluated"}` 寫死——AIInvocationLog 的護欄欄位**每一筆都是無意義的**，而規格說這是 F5 評量與 G6 白軍確認流的資料基礎；②批次佇列/OPFOR 優先級未生效（decider 走單發 `invoke()`，SPEC 自承）；③`rg invocation platform/app` 零命中——**沒有任何 UI 或 AAR 讀 AIInvocationLog**，統裁要看「AI 當時為什麼這樣下令」只能下 SQL。

**還缺什麼**

①把 `GuardrailOutcome` 從 `ai_loop/worker.py` 的 `run_faction_turn` 回傳路徑接到 `InvocationRecord.guardrail_result`——目前護欄評估與 LLM 呼叫在兩個不同的層，要嘛 RoleManager 收一個 late-bind 的 setter，要嘛 decider 呼叫後補寫該筆 log。②AAR 加「AI 稽核」分頁（`core/app/api/aar.py` + `platform/app/composables/useAar.ts`）：列 prompt hash、模式、耗時、護欄結果、對應的令。③批次佇列/OPFOR 優先級要改決策時序（SPEC 自承屬另一張卡）。

**風險**：補 guardrail_result 時**不可以動 prompt 或 messages 的組成**——ReplayClient 按 prompt 雜湊重播，任何字面變更會讓已錄的自主場次全部失效（decider.py:151-153 的註解就是在講這件事，已有測試逐字比對兩條路徑的 messages）。AAR UI 要注意 prompt 內容含該陣營的敵情投影，白軍以外的席位不該看得到別軍的 prompt（紅線 3）。

**使用者價值**：目前使用者看得到的只有「LLM 延遲進了 /metrics」。做完 ①②之後統裁才真的能在 AAR 裡追究「AI 這一步為什麼這樣下、護欄有沒有攔、當時看到的敵情是什麼」——那是 AI 兵推最需要的可解釋性，也是 F5 訓後評量的前置。


### G5 — 契約型別全面化：手寫 interface 改走 types/api.ts，缺的端點先補 core_api.yaml

**規模** L　|　**前置** 無　|　**文件說** SPEC_V2.md:1163 表格列 G5，無 ✅。驗收寫「`rg "interface.*View" app/` 無契約外重複定義」。

**查證證據**

**驗收指令本身幾乎已經綠了，但那是假象。** `rg "interface.*View" platform/app/` 只有 2 筆，都在 `platform/app/pages/session/[id]/autonomy.vue:16`（`interface UnitView`）與 `:20`（`interface AutonomyView`）——因為別的地方習慣叫 `AarStats`/`OwnUnit`/`EditorUnit` 而不叫 `*View`，驗收條件抓不到。實況：\n(1) **已接契約的 17 個檔**（`rg -l "from '~/types/api'" platform/app/`）：useExercises/useC2/useOrders/useIntel/useEquipment/useFirePlans/useParticipants/useMapFeatures/useCopOrdering/useAiStatus/useApi、pages/accounts|lobby|system-settings、stores/auth|sessionStream、components/ExercisePanel。`platform/app/types/api.ts` 5128 行，由 `npm run gen:api`（package.json:12）產自 `contracts/core_api.yaml`，且**是新鮮的**（兩檔最後一次 commit 同為 2026-07-31 01:06:57）。\n(2) **仍手寫、且形狀是 API 回應的 9 個檔**：`useAar.ts:4,11,25,34,40,49,57`（AarReplay/AarReplayUnit/AarReplayChange/AarReplayStates/AarStats/AarReport/CitationAudit）、`useWhiteCell.ts:15,38,154`（CheckpointPoint/StreamEnvelope/ControlResult）、`useConditionDsl.ts`（12 個 type，整套 condition/inject DSL）、`useScenarioEditor.ts`（12 個 Editor*/ScenarioModel）、`useUnits.ts:46,103`（OwnUnit/Contact，COP 主資料流）、`useAarReplay.ts`、`pages/session/[id]/autonomy.vue:16,20`、`pages/scenarios.vue`、`pages/scenario-editor.vue`。\n(3) **鐵證級重複**：`AarReplayStates` **契約裡已經有**（`contracts/core_api.yaml:2492` 路徑 + schema，`platform/app/types/api.ts:1757` 已生成型別），`useAar.ts:34` 卻又手寫一份並在 `:104` 用 `apiFetch<AarReplayStates>` 綁自己那份——兩份會各自漂移而沒有任何閘門會發現。\n(4) **缺的端點＝真正工作量，有現成清單**：`core/tests/unit/test_contract_conformance.py`（4 passed，剛跑過）維護 19 筆既有漂移。`_IMPL_ONLY`（實作有、契約沒有 → 前端拿不到型別）12 筆：`GET /sessions/{}/aar/export|missions|replay|report|stats`、`GET|PUT|DELETE /sessions/{}/autonomy`、`GET|PUT /sessions/{}/orbat-permissions`、`POST /sessions/{}/units/{}/reposition`、`POST /system/config/test-llm`。這 12 筆與上面手寫型別的檔案**一一對應**：useAar.ts:102–117 打 aar/*、autonomy.vue:96/128/131 打 /autonomy、white-cell.vue:236,246 與 useEquipment.ts:28,34 打 /orbat-permissions（型別是行內 `{ factions: string[] }`）、useMapStateEdit.ts:65,84 打 /reposition、system-settings.vue:136 打 test-llm（型別是行內 `{ ok: boolean; detail: string; latency_ms: number|null }`）。`_CONTRACT_ONLY`（規格殘骸，會讓人照契約寫然後吃 404）7 筆：`/admin/plugins`、`/admin/plugins/{}/toggle`、`/sessions/{}/aar`（實作已拆成 aar/stats|report）、`/sessions/{}/ai/tasks/{}`、`/sessions/{}/ai/consult`、`/sessions/{}/ledger`、`POST /sessions/{}/injects`（實作是單數 /inject）。\n(5) **另一層看不見的缺口**：契約 86 個 operation 裡 **22 個沒有任何 2xx response schema**（openapi-typescript 只會生 `unknown`，所以「路徑在契約裡」不等於「前端有型別」）。扣掉 9 個 DELETE 後真正該補的是 `GET /scenarios`、`POST /scenarios`、`DELETE /scenarios/{sid}`、`GET /sessions/{}/ledger`、`GET /sessions/{}/aar`、`POST /sessions/{}/msel/{}/fire|skip`、`POST /sessions/{}/ai/consult`、`GET /sessions/{}/ai/tasks/{}`。這正是 `pages/scenarios.vue` 手寫 `ScenarioItem[]` 的原因。\n(6) **DSL 單一來源根本不存在**：`contracts/msel.schema.json` 的 `trigger` 只寫 `{"type":"object","required":["type"],"description":"condition DSL：time / faction_eliminated / strength_below / unit_in_region / all / any"}`——**是一句註解不是 schema**；`inject` 同樣只約束 `event_type`。後端真正的實作在 `core/app/scenario/triggers.py` 與 `core/app/ai_loop/victory.py`，前端在 `platform/app/composables/useConditionDsl.ts`（自己在 `:2` 註明「type 與各欄位須與後端逐字對齊；契約先行——變更前先改後端與 contracts/msel.schema.json」＝已經知道這是靠人肉紀律維持的）。三份定義、零個閘門。

**還缺什麼**

照契約先行的順序：\n**階段 1（契約補完，最重）**：在 `contracts/core_api.yaml` 補齊 `_IMPL_ONLY` 12 條端點的完整 path + request/response schema（AarReplay/AarStats/AarReport/AarMissions/AarExport、AutonomyView+FactionAI+Objective、OrbatPermissionsView、RepositionRequest、TestLlmResult），並把 `core/tests/unit/test_contract_conformance.py:_IMPL_ONLY` 對應項**刪掉**（該檔 test_the_known_drift_list_only_shrinks 會逼你刪）。同時清 `_CONTRACT_ONLY` 7 筆規格殘骸：`/sessions/{id}/aar`、`/sessions/{id}/ledger`、`/admin/plugins*`、`/sessions/{id}/ai/consult`、`/sessions/{id}/ai/tasks/{tid}`、`POST /sessions/{id}/injects` 從 core_api.yaml 移除或改名對齊實作（`/inject`）。順手補上第 (5) 點列的 8 個「有路徑沒 schema」operation。\n**階段 2（DSL 單一來源）**：把 condition DSL 與 inject action 寫成真正的 JSON Schema（擴 `contracts/msel.schema.json`，或新開 `contracts/condition_dsl.schema.json` 供 msel/victory 兩處 `$ref`），後端 `core/app/scenario/loader.py:295,426` 的 `_validate_schema` 自動就吃得到；前端由該 schema 生 TS 取代 `platform/app/composables/useConditionDsl.ts` 的 12 個手寫 type（`package.json` 加一條 `gen:dsl` script）。\n**階段 3（前端換線）**：`npm run gen:api` 後把 `useAar.ts`（7 個 interface）、`useWhiteCell.ts`（3 個）、`autonomy.vue:16,20`、`pages/scenarios.vue`、`pages/scenario-editor.vue`＋`useScenarioEditor.ts`（12 個 Editor*）、`useUnits.ts:46,103` 的 OwnUnit/Contact 改成 `components['schemas'][...]` 的 alias；`useEquipment.ts:28,34`、`system-settings.vue:136` 的行內物件型別換掉。\n**階段 4（把驗收條件修對）**：`rg "interface.*View"` 抓不到真正的重複，應改成「掃 `app/composables` 與 `app/pages` 裡有 snake_case 欄位（＝API 形狀）的 interface」之類的機械檢查，或至少在 CI 加一條「useAar/useWhiteCell/useConditionDsl 必須 import types/api」的 grep 閘門。

**風險**：(1) **`useUnits.ts` 的 OwnUnit/Contact 不是單純的 API 型別**——它是 camelCase 的前端投影（`lastReportedTick`/`errorRadiusM`），而契約 `UnitView`/`ContactView` 是 snake_case，中間隔著 WS `STATE_DIFF`/`INTEL_UPDATE` 的轉換。硬換成契約型別會擴散到 MapCanvas/useMilsymbol/血條桶號整條鏈，範圍遠超一張卡；建議這一項只做「以契約型別為輸入、投影型別為輸出」的顯式 mapper，不要直接替換。\n(2) **`useWhiteCell.ts:38` 的 StreamEnvelope 屬於 WS 協定不是 REST**——權威在 `contracts/ws_protocol.md`（純 markdown，不可生成）。要納入單一來源得先決定 WS envelope 要不要進 core_api.yaml 的 components（那是設計決策，可能該先出 ADR），不要在這張卡裡順手發明。\n(3) **清 `_CONTRACT_ONLY` 會動到別人正在改的檔**：`POST /sessions/{id}/injects` 與 white-cell 注入面板相關，而本週另有 workflow 在改 `platform/app/pages/session/[id]/white-cell.vue`——排程上要避開或最後再合。\n(4) 契約改完**一定要跑 `uv run python ops/tools/schema_sync_check.py` 與 `npm run gen:api`**，忘了重生就是「契約有、前端還是舊型別」的新一輪漂移。\n(5) 紅線 4（契約先行）直接適用：先改 `contracts/` → 驗證 → 再實作，順序反了這張卡本身就違規。\n(6) golden 不受影響（純型別與契約宣告，不動裁決），但若階段 2 把 DSL schema 收嚴，**既有 `scenarios/examples/*/msel.yaml` 與 victory 條件可能驗不過**——`armor-breakthrough`/`joint-defense`/`battalion-defense`/`tutorial-platoon` 四個想定都用 `faction_eliminated`，收嚴前要先拿它們當回歸樣本。

**使用者價值**：對統裁/參謀**看不到直接差別**，這是基礎建設。但它是可觀察缺陷的預防機制：目前 AAR、自主推演設定、白軍 ORBAT 權限、地圖狀態編輯這四條路徑的前端型別是人肉抄的，後端一改欄位名前端不會編譯失敗、只會在畫面上默默顯示空白或 undefined——本 repo 已經吃過這種虧（`d014817` 修「任務令參數收得下卻沒被讀過 + 契約欄位名漂移」、`8171283` 修「MOVE payload 的 tempo 後端與 AI 都在用、契約沒列」）。做完之後這一類回歸會在 `npm run typecheck` 當場紅，而不是在演習中途被使用者發現。


### H3 — Live-Virtual 預留：把既有 reposition 正式化為 POST /sessions/{id}/track-feed（批次位置注入、來源標記 LIVE、獨立授權 token）

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2 §WP-H3（行 1190–1194）標 ★「遠期」，V2 僅預留、不做終端整合。總表第 34 項（行 124）狀態欄空白。無 ✅、無 PROGRESS/TASKS 條目。

**查證證據**

**SPEC 說「units API 已可外部改位置」——這句話是真的**：
- `core/app/api/units.py:284` `POST /{session_id}/units/{unit_id}/reposition`，`is_omniscient(user.role)` 限統裁/白軍/管理；寫 `TacticalUnit.current_lat/lng`（DB 權威）＋ `push_pos_cmd(...)` 推入活模擬命令通道。
- 命令通道完整且是生產路徑：`core/app/state/live_position.py` 的 `push_pos_cmd`（RPUSH `session:{id}:pos_cmds`）／`drain_pos_cmds`（LRANGE+DEL，`_MAX_DRAIN=512`）／`apply_pos_cmds`（single-writer，由 sim 迴圈於 tick 前套進 hot state）。
- 有生產呼叫端（非只在測試）：`platform/app/composables/useMapStateEdit.ts:65` 與 `:84`（多選整組移動時**逐一並行呼叫**）。

**但 track-feed 本身不存在，且既有端點有三個具體缺口：**
1. **不是批次**：一次一個單位。前端多選移動就是 N 次 HTTP（useMapStateEdit.ts:78-90 註解自承「逐一 reposition（並行）」）——外部實兵回饋若每秒灌 100 個 track，這是 100 個請求 + 100 次 DB commit。
2. **無來源標記**：`push_pos_cmd` 的 payload 只有 `{unit_id, lat, lng}`，`apply_pos_cmds` 只寫 `{lat, lng}`。熱狀態、Ledger、STATE_DIFF 全都無從分辨「這個位置是模擬推的還是實兵回報的」。COP 上實兵單位與虛擬單位長得完全一樣。
3. **無獨立授權**：只認一般 access JWT ＋ `is_omniscient`。外部實兵終端要灌位置就得拿到白軍帳號的 token，權限遠大於所需。
4. **契約缺口（既有技術債，H3 正好清掉）**：`rg reposition contracts/` 回 exit 1 —— 端點**不在 `contracts/core_api.yaml`**，被 `core/tests/unit/test_contract_conformance.py:72` 的 `_IMPL_ONLY` 白名單放行（該集合註解自承「實作有、契約沒有＝前端拿不到型別」）。違反紅線 4「契約先行」。
5. `rg -in 'track.feed|live.virtual|LVC'` 全 repo 零命中（contracts/、core/、platform/ 皆無）。

**還缺什麼**

1. **契約先行**：`contracts/core_api.yaml` 補 `POST /sessions/{id}/track-feed`（`TrackFeedRequest{ tracks: [{unit_id, lat, lng, source?, reported_at_tick?}] }`）**並順手補上遺漏的 `POST /sessions/{id}/units/{uid}/reposition`**，同時把它從 `test_contract_conformance.py` 的 `_IMPL_ONLY` 移除。
2. **後端端點**：`core/app/api/units.py`（或新開 `core/app/api/track_feed.py`）——批次驗證、單次 DB bulk update、單次 pipeline RPUSH。
3. **來源標記**：`core/app/state/live_position.py` 的 payload 加 `source`，`apply_pos_cmds` 寫入熱狀態鍵（例如 `position_source`）；**注意 `broadcaster.py` 的 `_INTERNAL_FIELDS` 是 denylist**——新熱狀態鍵預設會自動進 STATE_DIFF，這裡是刻意要它進（前端要據此畫實兵符號），但必須確認它不構成情報洩漏（實兵單位是自軍，`project_diff` 的可見集已擋敵方）。
4. **獨立 token**：`core/app/auth/` 加一種 feed token 類型（scope 限定 session + 限定 track-feed 端點），或最低限度加一組 API key；需 DB migration。
5. **前端**：`MapCanvas.vue` 對 `position_source == 'LIVE'` 的單位加視覺區別（實兵 vs 虛擬）；`useMapStateEdit.ts` 的多選整組移動改呼叫批次端點（順手修掉 N 次請求）。
6. 測試：批次冪等（同單位多筆取最後，`apply_pos_cmds` 已有此語義）、feed token 不得存取其他端點、非白軍不得注入。

**風險**：（a）**`apply_pos_cmds` 會跳過熱狀態不存在的單位**（`if hot.get_unit(uid) is None: continue`）——外部 feed 在 sim 尚未 seed 時灌的位置會**靜默丟棄**，只留 DB 值。批次注入下這會表現為「一半的 track 沒生效」且沒有任何錯誤回報，需在批次端點回報逐筆結果。（b）`_MAX_DRAIN = 512`：高頻 feed 會在單 tick 內堆超過上限，多的**留在 list 下 tick 再 drain**（不會丟，但會延遲累積）；需在 ADR/驗收裡定義最大 feed 速率。（c）新增獨立 token 是認證面變更，會碰到 E2（認證強化）剛落地的東西，要確認不繞過 Guardrail/RBAC——但注入位置**不是**物理裁決（它是白軍的既有權力），不觸紅線 2。（d）golden 不需重錄（不碰裁決、不碰 RNG）。（e）補契約會讓 `test_contract_conformance` 的兩個集合同時變動，記得兩邊都改否則測試紅。

**使用者價值**：看得見：白軍可把外部（實兵 GPS／第三方 track 來源）的位置**批次**灌進推演，COP 上實兵單位與虛擬單位以不同符號同圖顯示，而且不必把白軍帳號的 token 交給外部系統。對統裁而言這是「LVC 演習的第一塊磚」；對參謀而言，實兵演訓的位置終於能和兵棋內的虛擬敵在同一張圖上。注意：SPEC 明說不做終端整合，所以做完之後**還是沒有任何實際的實兵終端**接進來——價值是介面就緒，不是端到端可用。


---

## 未開始（22）


### A4 — LLM 角色扮演小組（RESPONSE_CELL）：MSEL 觸發時由 AI 生成上級/友鄰/民政電文送進席位信文匣

**規模** L　|　**前置** B5.2　|　**文件說** SPEC_V2 行 277–292 無 ✅、無完成註記；總表第 8 項只提 B5，A4 全無狀態欄。屬「規格宣告、尚未開卡」。

**查證證據**

① `rg -n "RESPONSE_CELL"` 全 repo 只命中 SPEC_V2.md:284/286/288——零程式碼。② `ai/matso_ai/roles.py:20-30` 的 `Role` enum 只有 6 值（STRATEGIC_PLANNER / OPFOR_COMMANDER / AAR_ANALYST / INTEL_OFFICER / WHITE_CELL_ASSISTANT / FACTION_COMMANDER），`ROLE_REGISTRY`（同檔 54-100）同樣沒有 RESPONSE_CELL。③ `contracts/ai_output.schema.json` 的 $defs = [base, tactical_order, opfor_decision, coa_recommendation, intel_assessment, aar_narrative, whitecell_advice]——沒有任何「訊息/電文」型輸出 schema，G1 無從驗。④ `db/prisma/schema.prisma:318-333` 的 `Message` 沒有 `aiGenerated` 欄；`rg -n "ai_generated|aiGenerated"` 全 repo 只命中 SPEC_V2 本身。⑤ **地基其實已經有**：`core/app/scenario/msel_actions.py:254-295` 的 `_message()` 已能在 MSEL 觸發時寫一封 `MessageKind.REPORT` 進指定 faction/seat 的信文匣（`from_user_id=f"msel:{entry_id}"`），並落 `MSEL_MESSAGE` 帳本事件——缺的只是「body 由 LLM 生成」。⑥ 關於 F3 ✅ 的查證：`RoleManager` **確實已接進活執行期**（`core/app/ai_loop/decider.py:220-275` `make_llm_faction_decider(audit=True)` 預設建 RoleManager + InvocationLogWriter，`orchestrator.py:122` 以它為 decider_factory，`sim_runtime.py:29` 起 worker）——但 `rg -n "Role\." core/app` 顯示活路徑只用 `Role.FACTION_COMMANDER`（decider.py:124），註冊表裡另外 5 個角色**在生產程式沒有任何呼叫端**。

**還缺什麼**

1) `ai/matso_ai/roles.py`：新增 `Role.RESPONSE_CELL` + `RoleConfig`（priority 低於 FACTION_COMMANDER，output_schema_ref 指向新 $def）。2) `contracts/ai_output.schema.json`：新增 `response_cell_message` $def（body/tone/refs，**禁止含 orders 陣列**）。3) `core/app/guardrails/gateway.py`：`evaluate()` 現在一路假設輸出帶 orders（`_orders_of`/`_filter_orders`，gateway.py:75-102），要開一條「只跑 G1+G2、G3–G6 不適用」的非命令路徑。4) DB：`Message.aiGenerated Boolean @default(false)` + prisma migration + `core/app/models/tables.py:313` + `MessageView`（`core/app/api/c2.py:286` `_msg_view`）+ `platform/app/composables/useC2.ts` 型別。5) MSEL：`msel.schema` / `InjectActionForm.vue` 加 `ai_sender: true` 勾選；`msel_actions.py:_message()` 依旗標改呼叫 RESPONSE_CELL 生 body（AI_OFF 時退回手寫 body）。6) 前端 `platform/app/components/cop/C2Panel.vue` 信文列表加 AI 徽章。7) AAR：`core/app/api/aar.py` 目前**沒有任何 messages 端點**（只有 replay/stats/missions/report/export），要新增可過濾 `ai_generated=true` 的信文查詢。

**風險**：最大的坑是**同步性**：MSEL 注入是在 tick 迴圈內執行的（`msel_runtime` → `msel_actions.apply`，純同步且會寫 DB），在那裡直接呼 LLM 會（a）阻塞確定性核心的 tick 預算（`kernel.py:185` 已有 TICK_OVERRUN 事件），（b）把非決定性拉進 tick——必須走 `ai_loop` 那套 async worker（或先產草稿、tick 只負責投遞）。紅線 2：RESPONSE_CELL 產的是文字，**絕不可帶 order**，schema 與 G1 要把這件事釘死。AI_OFF/BARE 模式下必須有可用的退路（MSEL 原本手寫的 body），否則關掉 AI 就沒有電文。golden 不動（信文不進裁決），但 `MSEL_MESSAGE` 事件已在帳本內，改動事件欄位要留意 AAR 既有讀取。

**使用者價值**：白軍不必手打上級電文：MSEL 觸發「上級變更任務重點」時，BLUE 指揮官席位自動收到一封**引用當前戰況**的上級指導電文，且信封上標明是 AI 生成（受訓者事後可辨識）。對統裁而言，這把「角色扮演」這個最耗人力的白軍工作自動化——是本卡少數對使用者立即看得見的價值。


### B3 — 想定文書層（一般/特別狀況/訓令）+ LLM 輔助產製 + per-faction 迷霧發佈

**規模** L　|　**前置** B5.1　|　**文件說** SPEC_V2 行 374–390 無 ✅；總表第 6 項「無想定文書層」狀態欄空白。屬「已知缺口、未開卡」。

**查證證據**

① 想定 schema 完全沒有文書位：`python3 -c "json.load(contracts/scenario.schema.json)['properties']"` → [name, version, description, bbox, mode, tick_rate_ms, hex_resolution, aggregate_adjudication_level, factions, relations, victory_conditions, files, no_strike_zones, request_quotas, day_night, allow_fratricide, indirect_fire_requires_approval, survivability_move]——**無 documents**。② 載入端只認四類檔：`core/app/scenario/loader.py:197-203` 只讀 `files.orbat / files.msel / files.roe / files.overrides_dir`；`_reject_weather_script`（loader.py:107-118）示範了「宣告了但沒實作就當場拒載」的既有紀律，文書若只加 schema 不加載入端會被同一條原則打臉。③ 匯出端同理：`core/app/scenario/dump.py:31-40` 組 `files` 只填 orbat/msel/roe/overrides_dir。④ 四個官方想定目錄 `ls scenarios/examples/*/` 一律只有 `msel.yaml orbat overrides roe.yaml scenario.yaml`——**沒有任何 documents/**，包含規格驗收點名的 tutorial。⑤ `rg -n "documents" contracts/core_api.yaml` 零命中——`GET /sessions/{id}/documents?as_faction=` 端點不存在。⑥ `rg -n "SCENARIO_WRITER"` 全 repo 只命中 SPEC_V2.md:384。⑦ 前端 `platform/app/pages/scenario-editor.vue`（1001 行）是單頁分 section（meta / 戰場範圍 / 想定設定 / 陣營 / 關係 / ORBAT / MSEL / 勝負 / 匯出），**沒有分頁機制、沒有 markdown 編輯器**。⑧ 規格寫「`Scenario` package 增 `documents/` 目錄位（zip 內）」——**這個前提在本 codebase 不成立**：`core/app/exercise/archive.py:5` 明寫「repo 裡完全沒有任何 zip/stream/attachment 機制」，想定實際是 DB `Scenario.packageBlob`（schema.prisma:396-404）+ JSON bundle（`loader.load_scenario_bundle`）。

**還缺什麼**

1) `contracts/scenario.schema.json` 加 `documents`（建議 `{general_situation, special_situation: {faction: md}, op_order: {faction: md}}`，per-faction 是迷霧要求）。2) `loader.py` `_load_documents` + `LoadedScenario.documents` 欄；`dump.py` 對稱寫回（B6 的「export→import→export 位元一致」驗收要跟著擴）。3) `core/app/api/scenarios.py` 與 bundle 路徑帶上 documents。4) 新端點 `GET /sessions/{id}/documents?as_faction=`（`core/app/api/session_scope.py` 已有 as_faction 紀律可抄，過濾**必須**在後端）+ 契約 + 生成型別。5) `ai/matso_ai/roles.py` 加 `Role.SCENARIO_WRITER` + `ai/prompts/` + `contracts/ai_output.schema.json` 新 $def（與 A4 同一條「LLM 產散文不產令」的管線）。6) 新端點 `POST /scenarios/{id}/documents/draft`（餵 ORBAT/地域/MSEL 摘要，AI_BARE）。7) 前端：scenario-editor 加「文書」分頁 + markdown 編輯 + 生成鈕 +「以紅軍視角重述」；COP/session 端加本軍文書閱讀面板。8) tutorial-platoon 與 armor-breakthrough 補齊全套文書（驗收條文）。9) LLM 草稿 vs 人工定稿的 diff 留存（供 F 系列評測）——目前無任何 draft 版本表。

**風險**：① **紅線 3**：per-faction 訓令過濾只能在後端做，不能靠前端隱藏——BLUE 讀不到 RED 訓令是本卡的核心驗收。② `files.*` 新增鍵會動到 B6 的「三個官方想定 roundtrip 位元一致」驗收，dump 的鍵序必須跟著 schema 宣告序（dump.py:22-24 已有此慣例）。③ 規格假設的「zip 內 documents/ 目錄」不成立（見 evidence ⑧），設計要改成 bundle 內嵌 markdown 字串或 DB 側表——照抄規格會做出一個沒有載體的東西。④ AI_OFF 時「生成草稿」鈕必須禁用而非產空白（MEMORY 記載語料長期不足，AI_BARE 是這裡的正確模式，不要拉 RAG）。⑤ golden 不動（文書不進裁決），但 loader 新增必填欄會讓既有四個想定載不起來——documents 必須是 optional。

**使用者價值**：很高，而且是目前**完全缺席**的那一半。統裁能在系統內寫一般狀況／特別狀況／訓令並一鍵生草稿；開局後各軍指揮官席位讀得到本軍訓令、讀不到敵軍的。今天這些東西只能在系統外用 Word 傳，演習的「文書體系」在 MATSO 裡等於不存在。


### B5.4 — 標繪分送（MapFeature.shared_to 給特定席位）+ 殲敵自動 REPORT 信文給同陣營砲兵席位

**規模** M　|　**前置** B5.2　|　**文件說** SPEC_V2 行 466 列於 B5 規格；行 441 與 1231 明寫「B5.4 標繪分送 + 殲敵自動 REPORT（未做）」；總表第 8 項備註亦然。**這三處文件難得與現實一致**。

**查證證據**

① `rg -n "shared_to|sharedTo"` 全 repo（含 contracts / prisma / core / platform）**零命中**。`MapFeature` 模型（`db/prisma/schema.prisma:100-113`）欄位止於 `attributes Json`。② `core/app/api/map_features.py:139,144` 的可見性只有 `ownerFaction in (WHITE_CELL, 本軍)` 一條規則，沒有「分送給某席位」的概念。③ 殲敵 REPORT 不存在：`rg -n "Message\(" core/app` 只有 4 個生產寫入點——`c2/service.py:83`（REQUEST）、`c2/service.py:132`（APPROVAL）、`api/c2.py:341`（人工發信）、`scenario/msel_actions.py:273`（MSEL 狀況發佈）。交戰路徑一封信都不發。④ 現有的 `BDA_REPORT` **是帳本事件不是 C2 信文**（`core/app/adjudication/bda.py:75`），而且刻意 `target_id=None`、只給估計值。⑤ 殲敵判定的掛鉤材料是有的：`adjudication/engagement.py:180` 與 `combined.py:255` 都把 `target_strength_after` 寫進 `ENGAGEMENT_RESOLVED`；`ai_loop/context.py:49-52` 也是用 `strength<=0` 導出 DESTROYED（**系統沒有持久化的 DESTROYED 狀態欄，也沒有 UNIT_DESTROYED 事件**）。⑥ 前三張卡查證結果：B5.1/B5.2/B5.3 **是真的做完且有生產呼叫端**——`core/app/seats/__init__.py` 的 registry 被 `orders/validator.py` 消費；`c2/service.expend_request` 由 `orders/service.py:89-91` 呼叫（不只測試）；火協 gate 在 `orders/precheck.py:240-285`；worklog 記載「前端尚未提供選核准單的 UI」**已補上**（`platform/app/components/cop/UnitsOrderPanel.vue:312` v-model 綁 `ordering.fireRequestId`，`useCopOrdering.ts:425` 送出時帶 `fire_request_id`）。⑦ **但同屬 B5 規格的另外兩種申請單是空殼**：`rg -n "RequestKind.AIR_RECON|RequestKind.RESUPPLY_VOUCHER" core/app` 只命中 `c2/__init__.py:43,45` 的**中文標籤**——核准後既不會產生一次性感測掃描事件，也不會解鎖 RESUPPLY（`precheck.py` 對 RESUPPLY 無任何憑單檢查）。申請→核覆→扣配額全程跑得通、核准了什麼都不會發生。

**還缺什麼**

1) `MapFeature.sharedTo Json @default("[]")` + prisma migration + `core/app/models/tables.py` + 契約 `MapFeatureView.shared_to` + `POST /sessions/{id}/map-features/{fid}/share`。2) `core/app/api/map_features.py:125-147` 的 list 過濾條件併入「或 我的席位 ∈ sharedTo」；前端 `MapCanvas`/圖層面板顯示來源徽章。3) 殲敵 REPORT：在 `core/app/engine/engage_wiring.py`（I/O 側，**不可放 adjudication**）偵測 `ENGAGEMENT_RESOLVED.ai_decision.target_strength_after == 0` 且射方有觀測 → 寫一封 `MessageKind.REPORT` 給同陣營 `SeatRole.FSO_FIRES`；`fire_wiring.py:318` 的 BDA 路徑同理但只能給估計值。4) `core/app/c2/service.py` 加 `report_kill()` 服務函式並複用既有 `_push` WS 受眾邏輯（`api/c2.py:254`）。5) 前端 C2Panel 對 REPORT 類信文的呈現（目前 `useC2.ts:33` 只有標籤）。

**風險**：① **最大的坑是資訊洩漏，而且很容易在 review 漏掉**：`adjudication/bda.py` 的整個檔案註解在說「觀測者回報的戰果不等於真實戰損、逐單位 BDA 等於把敵軍編成表交給射方」。殲敵 REPORT 若直接讀真值 `target_strength_after` 並寫上目標番號，等於把 bda.py 刻意堵住的洞從另一個門打開。必須以「有觀測」為條件（`c2/service.py:181` 已有 `has_observer_on`）且受眾限本陣營。② 標繪分送同樣是迷霧問題（紅線 3）：`shared_to` 過濾**只能在 list 端點做**，不能靠前端不畫。③ **既有假設不成立**：系統沒有持久化的單位 DESTROYED 狀態，也沒有 UNIT_DESTROYED 事件，殲敵得從 `strength<=0` 的**跨 tick 轉換**自行偵測——只看單筆事件會在單位早已陣亡後每次再被打到都重發一封。④ rollback 一致性：`core/app/state/checkpoint.py` 快照了 `FirePlanTarget.fire_request_id`（:212、:536）卻**完全沒有 Message / Request 表**（`rg -n "Message|Request" core/app/state/checkpoint.py` 零命中）——回滾後已 EXPENDED 的核准單不會還原，新增的 REPORT 信文也不會撤回。這是 B5 既有的、尚未被記錄的缺陷，本卡會把它放大。⑤ golden 不動（信文與標繪不進裁決輸出）。

**使用者價值**：中等、看得見但屬 QoL。FSO 席位在目標被殲滅時自動收到一封回報，不會再對已死的單位重複下火力任務；參謀能把自己畫的一張標圖「傳送」給特定席位並在對方 COP 上看到來源徽章。相較之下，同組被漏掉的「AIR_RECON / RESUPPLY_VOUCHER 核准後沒有效果」對使用者的落差更大——核准了卻什麼都沒發生，比沒有這個功能更容易被當成壞掉。


### C6.1 — 把 N 方同格混戰 resolve_multiway_tick 接進活執行期（現為零呼叫端的死碼）

**規模** L　|　**前置** C6.2　|　**文件說** SPEC_V2:693「resolve_multiway_tick（N 方同格混戰）接進 adjudicator（現只走成對）」；總表第 13 項寫「已實作未用」——這一句是準的。

**查證證據**

`rg -n 'resolve_multiway_tick'` 全 repo 只有 6 類命中：定義 `core/app/adjudication/aggregate.py:152`、匯出 `core/app/adjudication/__init__.py:9`、測試 `core/tests/property/test_aggudicate.py`（實為 test_aggregate.py:168/181/187/260/261）、以及三份文件自承未接（`README.md:397`「已實作並匯出，但 adjudicator 只走成對」、`docs/DEPLOYMENT.md:37` ⬜、`PROGRESS.md:484` 掛帳）。**零生產呼叫端**。裁決層唯一的聚合入口是 `core/app/adjudication/adjudicator.py:213` → `_resolve_aggregate()`（:273）→ `resolve_aggregate_tick()`（:358），純成對。`core/app/engine/` 底下 21 個 wiring/subsystem 檔沒有任何一個做「同格敵對部隊聚類」。

**還缺什麼**

1) 新增 `core/app/engine/multiway_wiring.py`（或掛進既有 tick phase）：每 tick 以 h3 res-8 格（現成用法見 `core/app/engine/movement.py:336`）把**聚合級以上**（`should_aggregate(level, threshold)`）的單位分桶，桶內 ≥3 個且存在 ≥2 個互為 HOSTILE 的陣營時，組 `AggregateForce[]` 呼叫 `resolve_multiway_tick`。2) 熱狀態/DB 回寫：`MultiwayResult.strength_after` 需要一份等同 `adjudicator._apply_agg_force()` 的回寫路徑（目前那是 `EngagementAdjudicator` 的私有方法，要抽出來共用）。3) **與成對路徑去重**：同一對單位若同時被 ENGAGE 令與 multiway 裁決會被扣兩次血——需要明確裁示（建議：multiway 只處理沒有 ENGAGE 令覆蓋的配對，或 multiway 取代該 tick 的成對聚合）。4) 補齊 `_resolve_aggregate` 已經有、但 multiway 沒有的四件事：ROE 篩選（adjudicator:284）、彈藥扣除與 NO_AMMO 合法性（:293-296）、`supply_effectiveness` 乘進 lethality（:319）、壓制累積。5) 想定/前端：白軍要看得到「三方混戰」事件（`AGGREGATE_ENGAGEMENT_RESOLVED` 每配對一則，戰況 feed 需能顯示）。6) 端到端測試：三陣營互為 HOSTILE 同格（SPEC 明列的驗收）。

**風險**：① **雙重扣血**是最可能踩的坑：ENGAGE 令的成對聚合與 tick 級 multiway 並存時，一對敵對營一個 tick 被算兩次。② `resolve_multiway_tick` 內部每個 HOSTILE 配對抽兩次 `rng.random()`，抽樣次數隨同格單位數變動——與交戰共用 `rngs['adjudication']` stream 會擾動所有既有交戰的隨機序列（同樣的理由 `AreaFireAdjudicator` 已經被迫用獨立 stream，見 sim_runtime 註解），**必須開新 stream**。③ golden：五份 golden（empty_100/mission_seize_60/order_replay_60/rng_walk_100/suppression_defense_60）都不走聚合路徑，且 adjudicator.py:349-351 已明文查證過這一點——所以真正擋路的不是 golden 而是 stream 汙染。④ 紅線 2：分桶與敵對判定必須留在 `adjudication/`＋engine 的純同步路徑，不可讓 LLM 決定誰跟誰打。⑤ 紅線 1：分桶不可用 wall clock，一律 SimClock tick。

**使用者價值**：看得見：三個以上互相敵對的陣營在同一格相遇時會真的三方互耗、被合圍的一方掉血最快，而不是「只有下了 ENGAGE 令的那一對在打、第三方站著看」。這是多陣營想定（joint-defense）唯一能表現「混戰」的機制。


### C6.3 — #48：目標編成組成（armor/infantry/soft 比例）+ 多目標火力分配

**規模** XL　|　**前置** 無　|　**文件說** SPEC_V2:695-696 列為 C6 第三項；TASKS.md 任務 #48「P5 保真（後續）：目標編成組成 + 多目標火力分配」狀態 **pending**（文件這次是誠實的）。

**查證證據**

`rg -n 'composition|armor_fraction|infantry_fraction|soft_target'` 全 repo 零命中（只有 test 檔名裡的 `weapon_mix`）。目標側現況是**單一 armor_class**：`core/app/adjudication/combined.py:189 pk = w.profile.expected_casualties(target.armor_class)`，而 `armor_class` 由 `core/app/adjudication/armor.py:47 armor_class_from_stats()` 取「編裝中最強的那一件」——該檔 :24-25 自己寫著「混編單位會被高估…真正的解法是逐平台的目標編成組成（#48），那是另一張卡」。多目標側：`core/app/adjudication/adjudicator.py:58-65 EngageCommand` 只有**單一 `target_id`**，`fire_policy` 只做武器篩選（combined.py:79 `_policy_allows`）不做分配。三處下游明文等這張卡：`armor.py:24`、TASKS.md:281（C7.3「人員補充未與裝備修復分開，要先有 #48」）、TASKS.md:288（C3「載具毀損→乘員傷亡，待 #48」）。

**還缺什麼**

1) 契約：`contracts/orbat.schema.json` 目前只有 `equipment`（範本名清單，:68-70），要讓編成組成**由裝備清單導出**（同 armor.py / establishment.py 的「導出而非要求填」紀律），或新增明示欄位。2) 裁決層：`core/app/adjudication/engagement.py` 的 `Target` dataclass 要從單一 `armor_class` 擴成組成向量（armor/infantry/soft 各自的 platform 數與戰力份額）；`combined.py:189-191` 的 `pk × cp_per_platform` 要改成對每個組成分量各算一份再加總——這會動到 `_resolve_volley` 與 `resolve_area_fire` 兩條路徑的同一個假設。3) 錯配懲罰：AP 打步兵、HE 打裝甲——資料掛在 `WeaponProfile.pk_by_armor_class`（core/app/adjudication/weapon.py:63），但目前只查一個 key。4) 多目標分配：`EngageCommand` 要能帶 target 列表，`fire_policy` 擴充分配規則；`core/app/orders/precheck.py` 的可達性檢查（#49 的「任一武器可打即放行」）要跟著改成逐目標。5) 前端：AAR 逐武器明細（`per_weapon[]`）要再多一層「打到哪個組成」；下令面板要能選多目標。6) `establishment.platform_count_for()` 導出的單一 platform_count 要拆成分量。

**風險**：① 這張卡動的是**整個漸進消耗模型的分母** `cp_per_platform = authorized/platform_count`（combined.py:137），改壞了每一場交戰的傷亡量級都會變——而歷史上這個分母出過一次大事（`establishment.py` 模組說明：`platform_count` 全系統沒有寫入端，預設 1 讓一發步槍扣 70 戰力，所有測試仍綠，因為每條測試都自己手塞值）。**新的組成欄位極可能重演同一個病：契約有、loader 不寫、預設值變成唯一路徑**。② golden 一定要重錄——`order_replay_60`/`suppression_defense_60` 都走逐平台交戰路徑，`Target` 形狀一改事件序列化就變、ledger 雜湊鏈跟著變。③ 「AP/HE 錯配懲罰」需要準則依據，出貨種子 `seed_weapons.py` 的 `pk_by_armor_class` 是 v0 值，憑感覺加懲罰係數會兩個方向都失真（同 PROGRESS.md:15 面射擊校準那段的教訓：量出了機制但刻意沒動數字）。④ 紅線 2：多目標分配是物理裁決，不能讓 LLM 決定火力怎麼分。

**使用者價值**：看得見：一個機步連（IFV + 下車步兵）被步槍掃射時，只有步兵那一部分會傷亡，IFV 不會；反戰車飛彈打上去只殺 IFV。今天整個混編單位共用「最強的那一件」的裝甲級別，所以下車步兵享有 IFV 的防護——參謀會直接察覺這件事是錯的。多目標分配則讓「一個連同時壓制兩個方向」變得可下令。


### C6.4 — 聚合係數 _AGG_* 搬進 SimParams + 以蒙地卡羅做一次校準實驗

**規模** M　|　**前置** D1　|　**文件說** SPEC_V2:697-698「聚合係數（`_AGG_*` 佔位值）一併搬進 SimParams 並以 [INDSR p.21–22] 的方法論做一次校準實驗（蒙地卡羅 30 次…）——**校準依賴 WP-D1 先行**」。

**查證證據**

四個佔位常數仍寫死在裁決層：`core/app/adjudication/adjudicator.py:79-82`（`_AGG_LETH_SCALE=0.02`／`_AGG_MIN_LETH=0.005`／`_AGG_RETURN_FIRE_LETH=0.01`／`_AGG_VARIANCE=0.1`，註解自承「皆為 v0 校準值」），另有 `core/app/adjudication/aggregate.py:22 _AREA_SCALE=100.0`（註解「v0 佔位」）。`core/app/sim_params.py` 內 grep `agg|lanchester|variance` **零命中**——這些值在系統設定頁調不到。前置 WP-D1：`rg -l 'monte_carlo|montecarlo|batch_run' core ops` **零命中**，SPEC_V2:108 第 18 項狀態欄空白 → D1 完全未開工。`adjudicator.py:339-352` 有一整段說明為什麼 `aimed_fraction` 刻意留 1.0：`_AREA_SCALE` 沒有站得住的定義，接上去會讓「倍率方向隨目標大小翻轉」。

**還缺什麼**

1) `core/app/sim_params.py` 新增五個參數（含 `_AREA_SCALE`）+ 系統設定頁欄位 + 引擎讀取端（注意 `bc1b3cf fix(params)` 的前車之鑑：「10 個推演參數在設定頁調得動、引擎完全不讀」）。2) 給 `_AREA_SCALE` 一個有物理意義的定義（adjudicator.py:345-348 已提出方向：取守方滿編戰力，使滿編時 linear≡square），這要動 `core/app/adjudication/aggregate.py:203`。3) 校準實驗本體：需要 WP-D1 的蒙地卡羅批次引擎（30 次/組）+ 歷史交換比對照，結果記 `docs/worklog/`。

**風險**：① **真正的阻塞是 D1 完全不存在**——沒有批次引擎就只能手跑，那正是 SPEC 說「校準依賴 D1 先行」的原因。② 只做「搬進 SimParams」而不校準是可以的、也應該先做，但要小心變成又一個「調得動、引擎不讀」的假接線（此 repo 已犯過，commit bc1b3cf）。③ 動 `_AREA_SCALE` 或 `aimed_fraction` 會改變所有聚合交戰的戰損量級；查證過 golden 不走聚合路徑，所以擋路的是校準不是 golden。④ 校準值若無準則依據就調，訓練用系統會失真——PROGRESS.md:15 的面射擊校準已示範了「量出機制但刻意不動數字」的正確做法。

**使用者價值**：對統裁幾乎看不到差別，這是基礎建設——除非校準做完、營級交戰的交換比落進歷史合理區間，那時參謀才會覺得「大部隊打起來的數字像回事」。在 D1 完成前優先度應為低。


### C8 — 多解析度建模（MRM）：營級單位解聚為連/排、交戰後再聚合，帳目守恆

**規模** XL　|　**前置** C7、C6.2　|　**文件說** SPEC_V2:759-769 標題無 ✅；總表第 15 項（行 105）狀態欄空白，描述「兩種裁決並存但單位粒度固定」。路線圖 :1253「C8 MRM 聚合解聚（依賴 C7 帳目）」。文件這次與現實一致。

**查證證據**

`rg -n 'DISAGGREGATE|REAGGREGATE|disaggregat|reaggregat|解聚|再聚'` 全 repo 命中**只有 SPEC_V2.md 的規格段落本身**，程式碼/契約/DB/前端零命中。令型別方面：`core/app/adjudication/adjudicator.py` 的命令 dataclass 只有 EngageCommand/FireMissionCommand/MissionCommand/FormationCommand 等，沒有任何 MRM 令。前置條件倒是**已經備妥**：單位階層存在（`db/prisma/schema.prisma` TacticalUnit `parentId` / `subUnits @relation("UnitHierarchy")`；ORM `core/app/models/tables.py:165 parent_id`），C7 帳目三卡全數完成（TASKS.md:281 C7.3 ✅），聚合門檻可宣告（C6.2 ✅）。

**還缺什麼**

整張卡從零開始。最小切片：1) 契約：新增 `DISAGGREGATE`/`REAGGREGATE` 令型（`contracts/` 先行，紅線 4）+ 想定開關（自動解聚的觸發距離 X km）。2) DB：子單位要能在推演中被**建立**（目前 TacticalUnit 只在開局由 loader 建），需要 migration 或明確裁示「子單位開局就建好、只是 inactive」——後者風險小得多。3) 引擎：`core/app/engine/` 新增 MRM 子系統——戰力/彈藥/油料/Class I&IX 按比例分帳（**實數→整數用最大餘數法**，決定性）；`_apply_agg_force` 的回寫路徑要能對整個子樹操作。4) 防殭屍物件：母單位解聚後不得再被 ENGAGE/MOVE 令選到，precheck（`core/app/orders/precheck.py`）與 AI context 都要過濾。5) 熱狀態：Redis 熱狀態鍵、h3 佔格、COP 2525 符號隨層級切換（前端 `MapCanvas`）。6) 守恆測試：解聚→交戰→再聚，Σ子單位 personnel/彈藥 ＝ 母單位 ± 戰損（陣亡不復活）。7) AAR 事件鏈。

**風險**：① **這是全 V2.1 最容易做出殭屍物件的一張卡**：JTLS 論文特別點名「防殭屍物件」不是巧合——母單位與子單位同時存在於熱狀態/Redis/COP/AI context/precheck 五個地方，漏掉任一個就會出現「打不死的幽靈營」或「戰損憑空消失」。② 帳目守恆牽涉**四套獨立的資源模型**（strength、per-weapon ammo `ammo_by_weapon`、fuel #84、supply Class I/IX C7.1），每一套的分帳與合併都要各自寫；其中彈藥是 per-EquipmentInstance 的，分帳等於要重新分配裝備實體。③ 實數→整數必須用最大餘數法且決定性，否則 golden replay 直接失效（紅線 1）。④ golden：SPEC 說「重錄（僅新路徑）」——查證後屬實，五份既有 golden 都不會碰到 MRM，但**必須確保未下 DISAGGREGATE 令時位元完全不變**（此 repo 的既有紀律：中性預設守住既有局，見 C7.1/C1 的做法）。⑤ 「自動解聚（進入敵 contact X km 內）」用的是**真值距離還是偵測結果**必須先裁示——C7.3 的整補安全距離用真值且有明確理由（限制自己不是給資訊優勢），但自動解聚是**給予能力**，用真值就等於白給敵情，可能踩紅線 3 的精神。⑥ 依賴一個要先查證的假設：SPEC 說「依賴 C7 帳目」，C7 三卡確實已完成，但 C7.1 的 `supply` 熱狀態鍵是**缺鍵回空 dict** 的中性設計，分帳時「空 dict」與「全 0」的區別要小心處理，否則子單位一生出來就全部斷補。

**使用者價值**：看得見且相當有感：統裁可以把一個裝甲營在接敵前拆成三個連分向突擊、各自機動與交戰，接觸結束後再合回一個營繼續行軍——COP 上的符號會跟著從營級變連級。這是目前完全做不到的事（單位粒度在想定開局時就固定死）。代價是它同時是整份 V2.1 裡最容易做出隱性 bug 的一張卡，建議排在 D 組分析功能之後、且必須有守恆測試先行。


### D1 — 蒙地卡羅批次實驗引擎——同想定換 seed 跑 30–50 次，產出勝率/戰損分布

**規模** XL　|　**前置** D2　|　**文件說** SPEC_V2 §857–877，★★★，總表第 18 項狀態欄空白、路線圖列入 V2.2。無 ✅（這次文件沒說謊）。動機欄宣稱「決定性引擎已是完美地基」。

**查證證據**

（1）零程式：`rg -ni "experiment|monte.?carlo|replication" -g '!*.md'` 全 repo 0 命中；`ls core/app/analysis` → No such file or directory；`contracts/core_api.yaml` 端點清單無 /analysis 或 /experiments（最後一條是 /sessions/{id}/aar/replay/states，行 2492）；`platform/app/pages/` 只有 accounts/armory/index/lobby/login/scenario-editor/scenarios/session/system-settings，無 analysis 頁。（2）地基查證推翻 SPEC 前提：golden replay 是**合成 kernel**，不是生產路徑——`core/tests/replay/harness.py:1-79` 跑的是 `core/tests/replay/scenarios.py` 裡手工組的 Kernel（NoOpSensorSystem/NoOpComms/NoOpLogistics + RngWalkMovement 示範子系統，見 scenarios.py:63-85, 280-300, 420-440）。生產執行路徑是 `core/app/sim_runtime.py:508` 的 `SimManager._run_session`，**約 530 行單一方法**，硬綁 Redis（`make_redis` 512、`RedisHotState` 513、`RedisBroadcaster` 545/650）、一 session 一條 `asyncio.create_task`（447）、由 3 秒 DB 掃描迴圈拉起、牆鐘 `TickPacer` 節奏（826）。**同 seed 位元一致從未在生產路徑上被驗證過**。（3）收場判定是牆鐘輪詢：`core/app/ai_loop/victory.py:90 run_victory_monitor` 迴圈間 `_sleep_or_stop(poll_s)`，最大速率批次下無法用。（4）離線組裝是**可行的**（好消息）：`core/tests/integration/test_scripted_battle.py` 用 SQLite + 注入假件組出真 Kernel（真 Adjudicator/SensorSweepSystem），零 Redis 零 gRPC——但那份接線是測試裡手寫的，與 `_run_session` 各自演化。（5）SPEC 講的 `purpose=ANALYSIS` **在 WargameSession 上不存在**：`rg -n purpose core/app/models/tables.py db/prisma/schema.prisma` 0 命中；`SessionRole.ANALYSIS` 在 `core/app/models/enums.py:207` / `schema.prisma:632`，掛的是 `ExerciseSession.sessionRole`（tables.py:146）。（6）clone 本身健康：`core/app/lobby/service.py` clone_session 複製 WargameSession 全欄 + TacticalUnit + EquipmentInstance + MapFeature + SessionParticipant，守門測試已非恆真（`core/tests/unit/test_clone_completeness.py:81/103/114/201`，commit 9b87275 剛修過）——但**只複製這四張子表**，FirePlan/FirePlanTarget/Message/Request/IntelContact 不複製。

**還缺什麼**

1) 離線無 Redis 執行路徑（核心難點）：把 `sim_runtime.py:508-1040` 的接線抽成可重用工廠（例如 `core/app/engine/assembly.py: build_session_kernel(db, session_id, hot, broadcaster, *, headless: bool)`），讓 `SimManager._run_session` 與批次共用同一份組裝；headless 版用 `InMemoryHotState` + NoOpBroadcaster + `NullMonotonicClock` + `kernel.run(n)` 直跑不經 `run_paced`。2) 收場判定改 per-tick 同步：新增 `victory.evaluate_now(...)` 純同步版，批次每 tick（或每 N tick）呼叫，取代 `run_victory_monitor` 的牆鐘輪詢。3) 新實體：`Experiment` / `ExperimentCase` / `ExperimentRun` 三張表（prisma migrate + models/tables.py + schema_sync_check）；欄位含 base_scenario_id|base_session_id、variations JSON、replications、seeds JSON、status、results JSON。4) 參數掃描展開器 `core/app/analysis/variations.py`：路徑 DSL（`sim_params.*` / `orbat.<unit>.lat` / `equipment_template.<id>.base_stats.*`）→ 笛卡兒積 case 矩陣；套用點必須在 clone 之後、runner 啟動之前。5) `core/app/analysis/runner.py`：case 佇列 + `asyncio.Semaphore(k)` 限流 + 每 case clone → 套 variation → headless 跑 → 收 MOE → 保留/刪除 session。6) 契約 + API：`POST/GET /analysis/experiments`、`GET /analysis/experiments/{id}/results`，WS `EXPERIMENT_PROGRESS`（要進 `contracts/ws_protocol.md` 的 allowlist）。7) 前端新頁 `platform/app/pages/analysis.vue` + 掃描維度表格 + 進度 + 結果表。8) 決定性驗證測試：同 case 同 seed 跑兩次 → 最終 stateHash 位元一致（這條測試本身就是 SPEC 前提的第一次真正查證）。9) `WargameSession.purpose`（或複用 ExerciseSession.sessionRole）標記分析局，讓 lobby 預設不列出 90 個副本。

**風險**：最大的坑：把 `_run_session` 的 530 行接線抽成工廠時，**任何一個順序差異就讓批次量的是另一個系統**——該方法裡有一串帶血的順序約束（`resume_session` 必須在 `seed_combat_state` 之前，見 536-537 行註解；WeatherCache 初值；RNG stream 集合 adjudication/movement/sensors/area_fire/bda/survivability 六條；resolver.enable_lazy_lookup 否則 MSEL 增援打不出子彈）。**golden 抓不到這種漂移**，因為 golden 走的是合成 kernel，與生產接線毫無交集。第二坑：收場判定不改成同步的話，批次每一局都會被 `poll_s` 牆鐘拖住，或反過來在監視器輪詢前就跑完而永遠不收場。第三坑：從活局 clone 當 base 時 FirePlan/Message/Request **靜靜消失**（clone 只複製四張子表）——想定裡有預劃火力計畫的批次會全部打不出火力準備，而且不會報錯。第四坑（紅線 1）：seed 派生必須走 `lobby/service.py::_derive_seed` 與 `DeterministicRNG`，禁 `random`/`uuid4`；case 展開順序必須排序後決定性化，否則同一份實驗設定兩次跑出不同 seed 配對。第五坑：批次跑滿 CPU 會拖慢同機上進行中的演習局（SPEC 驗收條文明列「tick 節奏監測」）——`TickPacer` 的自動降頻會把活局越降越慢，需要 Semaphore 上限 + 活局優先。golden 不需重錄（分析路徑獨立），但**如果為了抽工廠而動到 `sim_runtime` 的組裝順序，活局行為就變了**，那是另一回事。

**使用者價值**：統裁第一次能問「藍軍砲兵 2/4/6 連，勝率是 40%→65% 還是 40%→42%？」並拿到 90 局的分布圖而不是一局的軼事。這是把 MATSO 從「跑一局看結果」變成分析工具的那一張卡，也是 SPEC 自己說的 V2.2 exit（30 seeds × 3 變因分析報告）的唯一入口。附帶價值：它會是決定性保證的第一次真實體檢——現在沒有任何測試證明生產路徑同 seed 同結果。


### D2 — MOE 框架與成本效益指標（MER/DR/KR）+ hit/kill/destroy 對帳修正

**規模** L　|　**前置** 無　|　**文件說** SPEC_V2 §878–896，★★★，總表第 19 項狀態欄空白、路線圖列入 V2.2。無 ✅。SPEC 自陳「AAR 統計連命中率的帳都對不平」。

**查證證據**

（1）零程式：`rg -n "\bMOE\b|\bmoes\b|exchange_ratio|exchangeRatio" -g '!*.md'` 全 repo 0 命中；`rg -n "unit_cost|unitCost"` 0 命中；`rg -n "mission_kill|mission-kill"` 0 命中。（2）成本欄不存在：`EquipmentTemplate` 只有 id/name/category/baseStats 四欄（`core/app/models/tables.py:186-192`）。（3）現有 AAR 統計只有 88 行：`core/app/aar/stats.py` 的 `AarMetrics` = total_events / event_counts / engagements / hits / hit_rate / total_damage / guardrail_blocks / damage_by_faction / max_tick。無分層指標、無成本、無 per-weapon-class。（4）**SPEC 說得太客氣，實際是壞的**：`core/app/adjudication/aggregate.py:93-107` 的 `AGGREGATE_ENGAGEMENT_RESOLVED` 設 `damage_calc = a_loss + b_loss`（雙方戰損相加）而 `target_id = force_b.unit_id`；`core/app/aar/stats.py:74-77` 拿 `e.damage_calc` 整包記到 `faction_of[e.target_id]` 的頭上 → **聚合交戰中攻方自己的損失被算到守方陣營**。而 `aggregate_adjudication_level` 預設 None＝BATTALION（tables.py:96-99），營級是預設路徑，所以現在 AAR 的「各陣營承受戰損」在多數想定裡是錯的、而且方向性地誇大守方。逐側數字 `initiator_loss`/`target_loss` 早就寫在 `ai_decision`（aggregate.py:101-102），**從來沒有人讀**——與 `_area_losses`（stats.py:33-46，已經讀了 AREA_FIRE 的 losses_by_unit）是同一個病的第二例。（5）hit_rate 分母只數個體 `ENGAGEMENT_RESOLVED`（stats.py:79-86），聚合與面射擊完全不進分母。（6）KR/MR 的原料半有半無：`core/app/adjudication/combined.py:199-206` 的 `per_weapon[]` 有 `shots_fired` 與 `expected_hits`——但 `expected_hits` 是**期望值不是計數**（combined.py:188 `expected_hits = shots * p_hit * dispersion`），拿它當「每發命中」會把 KR 算成期望比而非實測比；且 weapon_id→weapon_class 要回查 EquipmentTemplate，事件裡沒有類別。

**還缺什麼**

1) `core/app/analysis/moe.py`（或 `core/app/aar/moe.py`）：受限表達式求值器——**禁 eval**，走 AST allowlist 或與 `app/orders/triggers.py::validate_condition` 同款的 token DSL；原子量 losses(faction, class?) / kills(faction) / shots|hits(weapon_class) / cost(unit|equipment) / ticks_to(condition) / survived(unit_id)；內建範本 exchange_ratio、MER、DR、KR、完全攔截率。2) 修 `core/app/aar/stats.py::compute_metrics`：`ENGAGE_TYPES` 分支改成——聚合事件讀 `ai_decision.initiator_loss` 記到 initiator_id 的陣營、`target_loss` 記到 target_id 的陣營，`damage_calc` 不再整包歸給 target。加守恆測試（雙側入帳總和 == damage_calc）。3) hit_rate 分母語意：分成 `individual_hit_rate` 與 `weapon_shots/weapon_hits`（後者需 combined.py 在 per_weapon 補 `weapon_class` 與實測命中計數，或改記 `hits_counted`）。4) mission-kill vs destroy：在裁決層或統計層定義（效能<30%＝mission kill，personnel/裝備歸零＝destroy）——效能已有 `adjudication/effectiveness.py::effectiveness_pct` 可用，需要一個狀態機與對應事件/衍生量。5) `EquipmentTemplate.unit_cost`（prisma migrate + models/tables.py + `contracts/core_api.yaml` 的 EquipmentTemplate schema + `platform/app/pages/armory.vue` 表單 + `ops/tools/schema_sync_check.py` 過關）；單位級成本＝所屬 EquipmentInstance × quantity 加總。6) `Experiment.moes[]` 欄位（隨 D1 的 migration 一起）。7) API：`GET /sessions/{id}/aar/moe?exprs=...` 或擴充既有 `/aar/stats` 回傳；前端 AAR 頁新增 MOE 區塊，與 D1 結果表共用同一顆計算器。8) 驗收用的近似想定（[INDSR p.21] 雷射案例複刻）放 `scenarios/examples/`。

**風險**：最容易踩的：**想去改 `AGGREGATE_ENGAGEMENT_RESOLVED` 的事件 payload**。那個事件進 ledger hash chain（`core/app/state/ledger.py:83-102`，`detail` 才是不入鏈的欄位），改欄位＝改雜湊＝golden 可能要重錄，而 `aggregate.py:68-81` 的註解已經寫著「無條件加四個 1.0 會改掉每一則事件的序列化內容，等於為了一個恆為 1 的欄位重錄所有 golden」。**安全做法是一行事件都不動**——`initiator_loss`/`target_loss` 已經在裡面，只改 `stats.py` 的讀法，golden 不動、ledger 不動。第二坑：MOE expr 若圖方便用 eval/`simpleeval`，就是踩 SPEC 明寫的「同 DSL 紀律禁 eval」，且 expr 是使用者輸入。第三坑：`unit_cost` 加在 `EquipmentTemplate`（全域表）——與 WP-B6 已記錄的「per-session 覆寫會污染同時進行的其他局」是同一個結構問題，成本一改，歷史局的 MER 會被追溯改掉；要嘛開局快照要嘛接 WP-B4 參數簽證。第四坑：`expected_hits` 是期望值，拿去算 KR 會得到一個看起來很合理但語意錯誤的數字——這正是本 repo 最典型的「測試會綠、實際沒意義」。第五坑：修好對帳後，**既有 AAR 頁面上的數字會變**（守方戰損下降），要有心理準備並在 worklog 說明，不然會被當成回歸。

**使用者價值**：參謀在 AAR 看得到「這一仗的交換比是 1:2.3、雷達摧毀率 60%、每發命中 0.15」而不是只有「誰贏」；成本入模後能回答「這個交換比划不划算」。而且**順手修掉一張現在會騙人的表**——營級交戰下 AAR 的「各陣營承受戰損」目前把攻方的傷亡算在守方頭上，統裁照著它做覆盤會得到相反的結論。這半張卡（對帳修正）就算單獨先做也立刻有價值。


### D3 — 態勢分析圖層（感知/火力涵蓋聯集、分區戰力比熱圖）與自動 FEBA 線

**規模** L　|　**前置** 無　|　**文件說** SPEC_V2 §897–918，★★，總表第 20 項狀態欄空白（現況欄寫「單一單位 viewshed/射界有；聯集與戰力比分區無」——這句是對的）、路線圖列入 V2.2。無 ✅。

**查證證據**

（1）零程式：`rg -ni "sensor_union|sensorUnion|fire_union|force_ratio|forceRatio|feba|sensor_gap|analysis_summary" -g '!*.md'` 在全 repo 只命中 uv.lock 的雜訊，程式碼 0 命中；`contracts/core_api.yaml` 無 `/sessions/{id}/analysis/layers`；前端無分析圖層元件。（2）地基**確實備妥**（這次 SPEC 沒說錯）：逐單位地形裁切射界 `core/app/footprint.py:87 compute_footprint`（逐方位 LOS → 閉合多邊形環），已有生產端點 `POST /sessions/{id}/terrain/footprint`（`core/app/api/map_features.py:244-276`）；terrain 側 `modules/terrain/terrain/los.py:127 get_viewshed` 有 proto（`contracts/proto/matso/terrain/v1/terrain.proto`）且 core 這端有 client（`core/app/plugins/terrain_client.py`）。（3）迷霧一致的資料源也在：`IntelContact` 表（`core/app/models/tables.py:392`）由 `SensorSweepSystem` 每 tick 寫入，而且**確實接上活執行期**（`core/app/sim_runtime.py:763-785`，註解自寫「#97 偵測（取代 NoOp）」）——所以「只用該陣營可見情報算戰力比」有真實資料可算，不必造假。（4）AI 側缺口：`rg -n analysis_summary core/app/ai_loop/` 0 命中，WP-A1 的 context builder（`core/app/ai_loop/context.py`）沒有分析摘要欄位。

**還缺什麼**

1) `core/app/analysis/layers.py`（純函數層）：`sensor_union(units, viewshed_fn)` / `sensor_gaps(aoi_polygon, union)` / `fire_union(units, weapon_ranges, los_fn)` / `force_ratio_grid(own_units, known_contacts, res=7)` / `feba(own_front, enemy_front)`。2) viewshed 聯集快取：`compute_footprint` 是逐方位 gRPC LOS 查詢，需要一層 per-(unit, 位置, tick 區間) 的快取（Redis 或 process 內），否則一個營 30 個單位 × 72 方位 = 2160 次往返。3) 多邊形聯集：repo 目前無 shapely（查 `pyproject.toml` 依賴）——要嘛引入 shapely，要嘛全部改走 h3 res-7 格集聯集（後者與 force_ratio_grid 同一套資料結構，且與既有 h3 依賴一致，建議走這條）。4) API：`GET /sessions/{id}/analysis/layers?as_faction=` + 契約先行（`contracts/core_api.yaml` 新 schema `AnalysisLayersView`）；`as_faction` 的權限與過濾必須複用 `core/app/api/session_scope.py` / `aar.py:29-64` 那組既有的 faction-scope 檢查，不可另寫。5) 前端：圖層小工具新增「分析圖層」組（`platform/app/components/` 下的 COP 圖層元件 + MapCanvas source/layer），30s 或手動刷新。6) AI 接口：`core/app/ai_loop/context.py` 增 `analysis_summary`（文字摘要）——**注意這會改 prompt**。7) 驗收想定：雙連對峙 + 遮蔽山谷（可從 `scenarios/examples/` 既有的擴一個）。

**風險**：第一坑（紅線 3）：`force_ratio_grid` 只要有一處誤用 `hot_state` 的全知單位表而非該陣營的 `IntelContact`，就是「fog of war 的 faction 過濾只能在後端」的直接違反——而且它會**看起來完全正常**（熱圖有畫出來、數字也合理），只是那個數字是上帝視角。必須在函式簽名上就把敵情限制成 contacts（同 `adjudication/obscurants.py::blocks_los` 那種「簽名裡沒有陣營＝結構保證」的做法）。第二坑：效能。`compute_footprint` 走 gRPC 逐方位查 LOS，沒有快取就會把 API 打死；SPEC 自己寫「30s 或手動刷新（計算成本控制）」正是為此，別做成即時圖層。第三坑：FEBA v0 沒有標準答案，驗收條文「落在兩軍之間」不是可測斷言——要先自訂可測判準（例如：FEBA 折線到最前緣友軍與最前緣敵 contact 的距離比落在 [0.3, 0.7]），否則會做出一條「看起來對」但無法回歸測試的線。第四坑：加 `analysis_summary` 到 AI context **會改動 prompt**，而 `ReplayClient` 是按 prompt 雜湊重播的（`ai/matso_ai/inference/client.py:57 prompt_hash`、`core/app/ai_loop/context.py:95` 的註解），一改就讓所有已錄的自主場次 fixture 作廢——要嘛把它做成預設關閉的可選欄位，要嘛承擔重錄。golden replay 本身不受影響（唯讀分析，不進 kernel）。

**使用者價值**：參謀在 COP 上直接看到四件事：我看得到哪裡、哪裡是感知盲區、我打得到哪裡、哪一帶我戰力劣勢，加上一條自動 FLOT/FEBA。今天這些全靠人腦在地圖上腦補。而且它是**本軍認知版**——把它跟白軍的真值版並排，兩者的差距本身就是最好的教學素材（「你以為你優勢的那一帶，其實有兩個你沒偵測到的連」）。附帶：同一份摘要餵給 LLM，AI 讀的是「此帶劣勢」而不是原始單位表。


### D4 — What-if 分支推演（從當前 tick 熱快照複製新局 + 雙欄比較視圖）

**規模** XL　|　**前置** E1、D2　|　**文件說** SPEC_V2:919-934 WP-D4，總表無 ✅。規格說「決定性引擎＋熱狀態快照其實已備」「依賴 E1 的活 session checkpoint（**先做 E1**）」——E1 這部分經查證屬實，是可信的敘述。

**查證證據**

`rg 'branched_from|branch-compare|branchCompare'` 全 repo **只命中 SPEC_V2.md 自己**（:927、:930）。無 `POST /sessions/{id}/branch`。`sessionRole` enum 的 `ANALYSIS` 值**確實存在**（`core/app/models/enums.py`、`db/prisma/migrations/20260731163000_b1_exercise/migration.sql` 加 `sessionRole ENUM('REHEARSAL','MAIN','ANALYSIS')`、`platform/app/composables/useExercises.ts` 有 `ANALYSIS: '分析'` 標籤）——但那是 WP-B1 演習專案帶進來的欄位，**沒有任何程式碼會把它設成 ANALYSIS**，也沒有 branched_from 欄位。現有的只有 `core/app/lobby/service.py:139 clone_session`（`POST /{session_id}/clone`，`api/lobby.py:78`），docstring 自陳「verbatim 複製**當下 DB 狀態**…**無「初始快照」**…開打前複製＝純淨初始局」——它複製 session 參數/單位/裝備/地圖標繪/名冊，**不複製熱狀態、不複製 tick、不複製 RNG 位置、不複製 Order/Ledger/SimCheckpoint**，新局從 tick 0 起跑。**依賴 E1 已查證為真**：`core/app/sim_runtime.py:815` 活局確實有 `checkpointer=CheckpointManager(...)`（含 `extras_provider` 存 RNG 三條 stream + MSEL 記憶）、`checkpoint_interval=sim_params.checkpoint_interval_ticks`；回滾路徑完整——`core/app/api/control.py:81 _request_rollback`（暫停+記請求+要求 runner 重建，不在 API 行程直寫）→ `core/app/state/resume.py:153 apply_pending_rollback` → `checkpoint.rollback`，且本週補上的三個缺口都在：`_restore_unit_rows`（座標/戰力/健康回寫 DB，checkpoint.py:~455）、`_restore_equipment`（彈藥/油料，checkpoint.py:~490）、`_restore_sim_features`（障礙/煙幕/補給點）、`_restore_fire_plan_targets`（commit 63abe43）。**所以 D4 的前置狀態機制是真的可用的，缺的是「把快照灌到另一局」這一步。** MOE 對比表的前置 D2 **完全不存在**（`rg 'moe|MOE|exchange_ratio' core/app` 零命中）。

**還缺什麼**

1. 契約先行：`POST /sessions/{id}/branch`（body: at_tick?, name?）→ 回新 session summary。2. DB migration：`WargameSession` 新增 `branchedFromSessionId` / `branchedFromTick`（走 `prisma migrate`，schema 權威 = `db/prisma/schema.prisma`，紅線 4）。3. `core/app/lobby/service.py` 新增 `branch_session`——**不能直接複用 `clone_session`**：需在 clone 之後額外做（a）取 `CheckpointManager.load_at_tick(src, tick)` 的 units 快照，(b) **依 `old_to_new` 重寫每個 unit id 的 key**（`clone_session:230` 已有這張映射，但熱狀態快照裡的 key 是**來源局的 unit id**，直接灌會全部對不上——這是最容易漏的一步），(c) 用重寫後的 blob 為新局寫一筆 tick=at_tick 的 SimCheckpoint，(d) 設 `session_tick_key(new_id)` 讓 runner 從該 tick 起跑，(e) master_seed 加 branch 鹽（`clone_session:196 _derive_seed` 已有型），(f) 複製 Order（clone 完全沒複製 Order，分支要接續未完成的令）。4. `core/app/sim_runtime.py` 的掃描層要能起一個 `purpose=ANALYSIS` 的高速 runner（最大速率、pace_compression 拉滿）。5. 前端 `platform/app/pages/session/[id]/branch-compare.vue`：同 tick 對齊雙欄 COP + MOE 表。6. **MOE 計算器（WP-D2）必須先做**，否則比較視圖只剩兩張地圖並排。

**風險**：①**unit id 重寫**是這張卡的核心技術風險：熱狀態、Order.payload 內的 target_unit_id、FirePlanTarget、MapFeature.attributes 裡可能都嵌了來源局的 unit id，漏一處就是「分支跑起來但某些單位行為異常」——正是本 repo 最常見的病型（存得進去、跑得起來、實際錯）。②`clone_session` 有一條 AST 守門測試 `test_clone_covers_every_session_column`（`service.py:187` 註解記載「註解攔不住這種漏，所以另有 AST 守門測試」，且該守門測試自己也壞過一次——commit 9b87275「複製推演局掉六個欄位——而守門測試是恆真的」）；branch 走另一條路徑，**必須同樣加守門測試，且要驗證守門測試會紅**。③RNG：ADR 007 已知界線「RNG 只還原到快照當下，快照後消耗的抽樣次數無處可考，最多倒退一個間隔」——分支點與快照點不同 tick 時，分支的起始態嚴格說不等於「當前 tick」，這個誤差要對使用者誠實揭露，別宣稱「完全一致」。④SPEC:138 不變量「分析功能不汙染活演習」——分支 runner 與原局共用 Redis 鍵空間，pause/rollback/tick 鍵必須嚴格以 session_id 隔離（既有 helper 已這麼做，但高速 runner 是新的執行模式）。⑤golden 不動（分析路徑獨立，不改裁決）。⑥刪除進行中的推演會與 runner 搶鎖（commit 79d1516 剛修）——分支局同樣會有 runner，刪除路徑要一併驗。

**使用者價值**：參謀能在演習進行中問「如果我把預備隊投到左翼呢？」——從當前態勢開兩個分支各自快跑，原局不受影響。這是 SPEC 引 IST160 的旗艦分析功能。**但注意**：沒有 D2（MOE 計算器）的話，比較視圖只能並排兩張地圖給人肉眼看，「多了什麼看得見的能力」會大打折扣；建議 D2 先行或至少同批做。


### D5 — 時間維度可行性（deadline_tick / 抵達時刻）與持續力分析（sustainment API）

**規模** L　|　**前置** C7　|　**文件說** SPEC_V2:936-946 WP-D5，總表無 ✅。文件說「MATSO 已有 ETA（移動預覽）與消耗率資料，缺彙整層」——**這句是準確的**，是本次盤點中少數沒有灌水的敘述。

**查證證據**

**deadline 完全不存在**：`rg '\bdeadline' core/app` 命中的全是 gRPC 呼叫逾時（`plugins/terrain_client.py`、`weather_client.py`、`comms_client.py`），沒有一個是任務時限。`core/app/orders/schemas.py:49 MovePayload` 只有 to_h3/mobility_profile/to_lat/to_lng/tempo，**沒有 `deadline_tick`**；`MissionPayload` 同理。`core/app/orders/precheck.py`（931 行）的檢查項全是空間/物理維度（`_precheck_move:530`、`_precheck_engage:547`、`_reachability_check:708`、`_ballistic_trajectory_check:754`、`_range_ammo_checks:868`），**沒有任何時間判斷**；唯一的 eta 是 `precheck.py:916` 把 `resp.eta_ticks` 拼成一句 debug 字串 `f"cost={...}, eta={resp.eta_ticks}"`，**沒有進 PrecheckResult 也沒有回給前端**。**sustainment API 不存在**：`rg 'sustainment' core platform contracts` 零命中；`rg 'analysis' contracts/core_api.yaml` 零命中；`core/app/main.py:99-122` 的 include_router 清單裡沒有 analysis router。**原料齊全**：移動預覽 `core/app/api/movement.py:69 duration_ticks`、`:79 fuel_remaining` 已回傳且前端已顯示（`platform/app/components/cop/UnitsOrderPanel.vue:250-252` 顯示油量）；油料模型 `core/app/movement/fuel.py:75 load_unit_fuel` / `:116 burn_fuel`；補給日耗 `core/app/adjudication/supply.py:85 daily_consumption`；補給距離 `sim_params.resupply_range_km`（`core/app/sim_params.py:69`）。缺的純粹是彙整層與端點。

**還缺什麼**

1. 契約先行 `contracts/core_api.yaml`：`GET /sessions/{id}/analysis/sustainment?as_faction=`（逐單位 fuel/ammo/rations 可撐 tick 數；補給單位涵蓋清單 + 分配後各能撐多久），以及 MOVE/MISSION payload 新增 `deadline_tick?`。2. DB：`Order.payload` 是 JSON，`deadline_tick` 零 migration；但「正式逾期→事件」需要新 event_type（`DEADLINE_MISSED`）→ 必須同步補 `platform/app/composables/useCopFeed.ts` 的 `EVENT_LABELS`（`core/tests/unit/test_event_labels_coverage.py` 會把漏補的擋下來）。3. 後端新模組 `core/app/analysis/sustainment.py`（純函數）+ `core/app/api/analysis.py` 並在 `core/app/main.py` include。4. `core/app/orders/precheck.py` 新增 `_precheck_deadline`：把 `resp.eta_ticks`（現在只進 debug 字串，`precheck.py:916`）真的帶進 `PrecheckResult`，與 `deadline_tick` 比對 → 逾期預測回琥珀警告（**不是 REJECT**，可行性檢查不該擋令）。5. 執行期逾期偵測：`core/app/engine/movement.py` 的 MOVE 完成/中止路徑落 `DEADLINE_MISSED` 事件。6. 前端：`UnitsOrderPanel.vue` 移動預覽加「抵達時刻（模擬時間）」與時限輸入；COP 單位卡加「續戰力 N 小時」徽章。

**風險**：①**「以當前消耗率」這個假設在靜止的單位上會給出無窮大**——不動的車不燒油（`movement/fuel.py:116 burn_fuel` 依距離扣），直接算 remaining/rate 會顯示「續戰力 ∞ 小時」；必須定義 rate 的取樣窗口或用建制標準消耗率。②單位換算已經害過人：本週剛修過「壓制/工事寫死 1 tick=1 分鐘」而 `tick_rate_ms` 是想定可調的（commit d67fe61）；「續戰力 N 小時」是同一類換算，必須走該局 `tick_rate_ms`，寫死必爆。③「抵達時刻（模擬時間）」要用 `world_start_time` + tick 換算，不能用牆鐘（紅線 1）。④迷霧：sustainment 的 `as_faction` 過濾只能在後端（紅線 3）。⑤golden 不動——只要 precheck 新增的檢查在無 deadline_tick 時是 no-op（既有想定都沒這個欄位）。

**使用者價值**：參謀終於能回答兩個最常被問的問題：「趕不趕得及？」（下令當下就看到抵達時刻與逾期預警）與「還能撐多久？」（單位卡上的續戰力徽章 + 補給單位涵蓋誰）。這是 SPEC 引 IST160 說覆蓋面最廣的單一功能，且原料都在，性價比高。


### D7 — 情境化警告與報告分級（規則式 ADVISORY 引擎 + 前端警告中心）

**規模** L　|　**前置** D5　|　**文件說** SPEC_V2:970-976 WP-D7，總表無 ✅。規格明訂「不用 LLM——這層是確定性規則」。與現實相符（就是沒做）。

**查證證據**

`rg 'ADVISORY|advisory' core platform/app contracts db` 全域**零命中**（唯二命中是 `AppToasts.vue:16` 的 `role="alert"` 與 `ops/monitoring/alerts.yml` 提及，皆無關）。`core/app/state/ledger.py` 沒有任何 severity 概念——`LedgerEvent`（`ledger.py:86-107`）只有 event_type/tick/initiator/target/ai_decision/damage_calc/detail。前端 `platform/app/composables/useCopFeed.ts`（306 行）`rg 'severity|WARN|CRITICAL'` **零命中**：`EVENT_LABELS`（`:43` 起）是一張純「型別→中文字串」表，約 46 種事件全部平鋪，沒有分級、沒有靜音、沒有讀/未讀。SimParams（`core/app/sim_params.py:57`）目前 20 餘個參數皆為物理/節奏參數，**無任何告警閾值欄位**。**基礎建設是現成的**：per-faction 受眾機制已存在且可直接用——`core/app/state/broadcaster.py:37 event_audience` 會依 `ai_decision['observer_faction']` 或所涉單位陣營算出 `envelope['factions']`（`:120-122`）。

**還缺什麼**

1. 契約：WS 事件 envelope 新增 `ADVISORY` 型別 + severity 欄位（`contracts/core_api.yaml` 的 WS payload 定義）。2. 新模組 `core/app/advisory/rules.py`（**純同步純函數**，比照 `core/app/adjudication/` 的紀律）實作五條規則 `ammo_below / fuel_below / deadline_risk / support_lost / contact_new`，輸入為熱狀態 + DB 投影，輸出 advisory list。3. Kernel 接線：`core/app/engine/kernel.py` 每 N tick 呼叫（比照 `sensor_interval_ticks` 的節流模式，`sim_params.py:72`），經 `broadcaster` 推播。4. SimParams 新增 5 個閾值欄位（`core/app/sim_params.py:57` 的 dataclass + `_int`/`_positive` 解析 + 系統設定頁 UI）。5. 前端：新 composable `useAdvisories.ts` + COP 浮動小工具「警告中心」（分級圖示、靜音、讀/未讀持久化到 localStorage 或 DB）。6. `useCopFeed.ts` 的 `EVENT_LABELS` 補 ADVISORY（`core/tests/unit/test_event_labels_coverage.py` 會擋）。

**風險**：①**最大的坑是 `contact_new` 的迷霧**：`broadcaster.py:37 event_audience` 對有 `observer_faction` 的事件才只給觀測方；若 advisory 直接把敵方 unit id 放進 `target_id`，`event_audience` 會把**敵我雙方都算成受眾**（`:54-56` 取 initiator/target 兩邊的陣營），等於通知對方「你被發現了」——SENSOR_CONTACT 就是為了這個才特別加 observer_faction 優先規則（`:41-42`）。新規則一律要帶 `observer_faction`。②**golden 其實安全**：`core/tests/replay/harness.py:52` 的 golden 只 hash `hot_state.get_all()` 的 units 子樹，不含 ledger；只要 advisory **不寫進 hot_state**（讀/未讀狀態尤其不能放 hot_state）就零重錄。若把 advisory 落進 ledger 則會改帳本內容但不影響 golden——不過會影響 D6 的統計（`stats.py:total_events` 會被灌水），要在 `read_events` 或 stats 排除。③閾值進 SimParams 而 SimParams 有 B4 參數凍結簽證（`docs/worklog/parameter-seal.md`）——新增欄位要確認不會讓既有已簽證的演習失效。④「規則式、不用 LLM」是紅線 2 的延伸，別為了「聰明一點」把它接到 AI。

**使用者價值**：目前戰況小工具是 46 種事件平鋪的流水帳，指揮官要自己盯著找出「哪支部隊快沒彈了」。做完之後低彈/斷補/時限風險會主動跳出來並分級——是**看得見**的能力，但依賴 D5 提供 deadline 與續戰力數據，單獨做只剩 ammo/fuel 兩條規則、價值折半。


### E5 — 負載測試工具鏈（loadgen）與超載時的 COP 顯示層降載（LOD）

**規模** L　|　**前置** E4　|　**文件說** SPEC_V2 §6 WP-E5（★，無 ✅）；總表第 29 項狀態欄空白，內容欄寫「無工具鏈；TickPacer 只會全域降頻」——這次查證與文件一致，是少數沒有誇大的條目。

**查證證據**

1) `ls ops/tools/` = build_terrain_tiles.sh / cpx_acceptance.py / gen_proto.py / grant_ledger_readonly.sql / live_system_check.py / rerecord_golden.py / schema_sync_check.py / seed_dev_user.py / verify_ledger.py — **沒有 loadgen.py**。2) `rg -l "loadgen|LOD|降載|聚簇|cluster"`（排除 .venv/node_modules）只命中 SPEC_V2.md、SPEC_FULL.md、platform/package-lock.json — **零程式碼**。3) `rg -l "benchmark|飽和" core/tests ops/` = 空 — 沒有任何容量/效能測試。4) TickPacer 確實存在且有降頻：core/app/runtime.py:35-75（backoff_after=3、factor 2、max_slowdown 8），overrun 由 core/app/engine/kernel.py:135 `duration_ns > tick_budget_ms*1e6` 判定（預設 200ms，kernel.py:76）。5) **但 `TickPacer.slowdown`（runtime.py:60-63 標註「觀測用（metrics）」）沒有任何寫入端**：`rg -n "slowdown"` 排掉 runtime.py/README/SPEC 後 0 命中，core/app/metrics.py 只有 tick_duration/tick_overrun，**沒有 slowdown gauge** → 系統正在降頻這件事在 Grafana 上看不見。6) 前端零聚合：`rg -l cluster platform/app` = 空；單位圖層在 platform/app/components/map/MapCanvas.vue 一律逐一畫。

**還缺什麼**

(a) `ops/tools/loadgen.py`：合成 N 單位想定產生器（沿用 ops/tools/live_system_check.py:122 `build_scenario()` 的形狀）＋壓測 runner，輸出單位數 vs tick 時長/overrun 率曲線 CSV；(b) core/app/metrics.py 增 `tick_slowdown` gauge，core/app/runtime.py `run_paced` 每圈寫入（這是 E4 未竟的同一類「定義了沒人寫」）；(c) platform/app/components/map/MapCanvas.vue 依 zoom + 單位數門檻做連→營符號聚簇（純顯示層，引擎不動），並在 COP 上明示「已聚合顯示」。

**風險**：E4 的直方圖有已知缺陷（SPEC_V2 §WP-E4 自承「直方圖累積做了兩次，桶數超過 _count，分位數是錯的」）——拿 /metrics 的 p99 畫容量曲線會得到錯的邊界，loadgen 必須自己量 tick 時長而不是信 exposition。另一個坑：TickPacer 的降頻是**牆鐘降頻**，模擬時間相對真實時間就變慢了，白軍設的時間壓縮倍率會靜默失真且無告警——壓測時容易把「引擎撐得住」與「時間對不上」混為一談。loadgen 不進 golden 路徑，golden 不需重錄。

**使用者價值**：統裁第一次能回答「這台機器這場想定能塞幾個單位」而不是靠猜；超載時 COP 不再是一團互相蓋住的符號。目前降頻對使用者是完全無聲的——做完 (b) 之後至少看得見「系統正在跑不動」。


### F2 — eval 案例從 3 擴到 ≥15 + 補上第四門檻（殘缺情報引用正確率）、迷霧一致性、MISSION 令合規率

**規模** M　|　**前置** F1　|　**文件說** SPEC_V2 §6 WP-F2（★★，無 ✅），總表第 30 項與 F1 併列、狀態欄空白。SPEC 自己承認「殘缺情報引用正確率（run.py 未計的第四門檻）」——查證屬實，而且問題比這句話大。

**查證證據**

1) `ls ai/evals/cases/*.yaml | wc -l` = **3**（intel-degraded-001 / opfor-contradictory-001 / opfor-ihl-001），門檻 ≥15。
2) **case schema 宣告的三個斷言欄位零程式消費端**：`golden_citations` / `require_uncertainty` / `citations_must_exist` 的 `rg -l` 結果全是 ai/evals/case.schema.json、三個 yaml、README.md、EvalCreator.md — **沒有一個 .py**。run.py `run_evals()`（ai/matso_ai/evals/run.py:108-140）只讀 `schema_ref`、`reasoning_min_steps`、`must_not_target`、`max_fabricated_citations` 四個。
3) **更關鍵：CI 那條 gate 結構上不可能變紅**。.github/workflows/ci.yml:41 跑 `uv run python -m matso_ai.evals.run`，而 run.py `main()`（:163-172）的 argparse 只有 `--cases-dir`，**沒有注入 responder 的路徑** → 永遠是 `FallbackResponder`；該 responder（run.py:45-70）的 `orders` 恆空、`cited_documents` 恆空 → IHL 違規率恆 0、捏造引用率恆 0，schema 則是自己組給自己驗。**這條 gate 量的是 jsonschema 套件還會不會動，不是模型品質。**這正是這個 repo 的招牌病：測試綠、gate 綠、實際沒在測。
4) 連「真模型」的手動 workflow 也沒接真模型：.github/workflows/ai-eval-manual.yml 註解自承「真模型 responder 的接線（RoleManager + OpenAICompatibleClient → run_evals responder）屬部署層；端點就緒後於此注入。目前執行 runner」。
5) 迷霧一致性 / MISSION 令合規率：0 例，且沒有對應的量測程式（`category` 只有 IHL_DILEMMA / DEGRADED_INTEL / CONTRADICTORY_INTEL 三類）。

**還缺什麼**

(a) ai/matso_ai/evals/run.py 加 `--responder {fallback,replay,openai}` 並把 RoleManager/OpenAICompatibleClient 真的接上（沒有這一項，下面全部都是裝飾）；(b) 在 `run_evals` 實作 `golden_citations`（引用正確率＝第四門檻）、`require_uncertainty`、`citations_must_exist` 三個斷言；(c) ai/matso_ai/evals/cases.py + ai/evals/case.schema.json 增 `FOG_CONSISTENCY`（AI 有沒有引用它視角看不到的敵情——比對 core/app/ai_loop/world_view.py 給它的 context）與 `MISSION_COMPLIANCE`（A2 任務令下去有沒有照 mission_type 展開）兩類；(d) 12 個新 case yaml；(e) ai-eval-manual.yml 真的傳端點進去。

**風險**：⚠ 使用者的長期現實是**語料與 eval 資料長期不足，這是設計前提不是暫時狀態**——所以這張卡不該規劃成「湊滿 15 例」。務實的切法：把 (a)(b) 的程式面先做完（那不需要語料），案例只補**不依賴語料**的兩類（FOG_CONSISTENCY 與 MISSION_COMPLIANCE 的輸入是想定與 COP context，自己生得出來），依賴語料的 DEGRADED_INTEL/CONTRADICTORY_INTEL 就停在現有 3 例、等使用者供檔。第二個坑：把門檻真的接上以後 **CI 會第一次變紅**，要先跟使用者講清楚那是儀器開始工作而不是退步。第三個坑：絕對不要為了湊數虛構準則語料（使用者明訂紅線）。golden 無涉。

**使用者價值**：使用者看不到差別，這是基礎建設。但它是唯一能回答「AI 有沒有在胡說 / 有沒有偷看它不該知道的敵情」的量尺，也是 F4 的硬門檻。誠實說：優先做 (a)(b) 兩項就好，(d) 的案例量受資料現實限制。


### F4 — MoA（Proposers/Challenger/Aggregator）——三條開工門檻目前全部不成立

**規模** XL　|　**前置** F1、F2、D1　|　**文件說** SPEC_V2 §6 WP-F4「★（V2.2，門檻擋在前）」。這是全 F 群唯一狀態標示誠實的一張。

**查證證據**

門檻逐條查證：
1) 「F1–F3 完成」：F3 ✅（RoleManager/稽核已接活，ai/matso_ai/inference/role_manager.py 有生產呼叫端）；**F1 只有最小切片且 UI/評測/PDF 三處未接**；**F2 未動**。→ 不成立。
2) 「eval ≥15 例全綠」：現有 3 例（`ls ai/evals/cases/*.yaml`），而且如 F2 卡所述，run.py 恆用 FallbackResponder，「全綠」在目前的接線下沒有判定力。→ 不成立，且是雙重不成立。
3) 「單模型基線（WP-D1 批次：單模型 vs 規則 OPFOR 勝率）存在」：`rg -rl "monte|蒙地卡羅|batch_run|MOE" core/ ops/ platform/app` = **0 命中** → D1 蒙地卡羅引擎完全不存在，沒有任何批次跑同想定 N seeds 的工具。→ 不成立。
程式面：ai/matso_ai/roles.py `Role` 只有 6 個（STRATEGIC_PLANNER / OPFOR_COMMANDER / AAR_ANALYST / INTEL_OFFICER / WHITE_CELL_ASSISTANT / FACTION_COMMANDER），沒有 Proposer/Challenger/Aggregator；contracts/ai_output.schema.json 無對應 $def。

**還缺什麼**

**不要開工**。要往前推的話，前置是 D1 的最小版：`ops/tools/batch_run.py`（同想定 × N seeds，走既有 ReplayClient/RecordingClient 路徑，記錄勝負與戰損比），先產出「單模型 vs 規則 OPFOR」的基線數字。MoA 本體才是 roles.py 增三角色 + ai/matso_ai/inference/ 新增 aggregator + ai_output.schema.json 新 $def + decider 改多輪。

**風險**：最大風險就是提早開工：沒有基線，MoA 的增益無法證明，做完也只能說「感覺比較好」。技術上兩個硬坑：(1) MoA 讓每次決策的 LLM 呼叫變 3–5 倍，會直接撞上 O11.4 的固定心跳決策排程與 tick 預算——F3 worklog 已註記「佇列批次未生效、decider 走單發 invoke()」，MoA 會逼著先改決策時序語義；(2) **ReplayClient 按 prompt 雜湊重播**（F3 worklog 明寫），多角色多輪一上，所有已錄的自主場次全部失效必須重錄。

**使用者價值**：現階段對統裁/參謀**零可見價值**——沒有基線就連「有沒有變好」都答不出來。這張卡的正確處置是保持關閉並在 SPEC 標明門檻現況，而不是排期。


### F5 — 訓後評量：想定預埋評估點 + 四型量測引擎 + AAR 評量分頁 + 白軍主觀評分

**規模** XL　|　**前置** B5、C10、D6.1　|　**文件說** SPEC_V2 §6 WP-F5（★★★，無 ✅）；總表第 32 項「訓後評量缺位」狀態欄空白。V2.2 路線圖寫「F5 訓後評量（依賴 B5/C10 事件鏈 + D6 重播）」——**這句依賴描述查證後只成立一半**，見 risk。

**查證證據**

**本體全無**：`rg -rl "assessment_plan|ASSESSMENT_NARRATOR"` 只命中 SPEC_V2.md；`grep -n assessment contracts/scenario.schema.json` = 0；ai/matso_ai/roles.py 六角色無 ASSESSMENT_NARRATOR；無 core/app/assessment/；platform/app/pages/session/[id]/aar.vue 無評量分頁。
**依賴查證（這才是重點）**：
1) **「評量引擎（純函數，讀 Ledger）」這個前提不成立**。Ledger 實際寫入的 event_type 全表（`grep -rhoE 'event_type=.?"[A-Z_]+"' core/app` → 44 種）裡**沒有任何下令、申請、核覆事件**：只有 `ORDER_RESTRICTED_FIRE_OVERRIDE`；沒有 ORDER_ISSUED、沒有 REQUEST_SUBMITTED/DECIDED、沒有 CALL_FOR_FIRE。core/app/c2/service.py（申請-核覆的唯一實作）`rg -n "ledger|LedgerEvent|append"` = **0 命中**。
2) 那些時戳只在關聯表：db/prisma/schema.prisma:337 `Request.requestedAtTick / decidedAtTick`、:415 `Order.issuedAtTick / resolvedAtTick`。
3) ops/tools/cpx_acceptance.py:745 的 `s7_event_chain` 自己就寫明「分兩類講清楚，因為它們不在同一個資料來源：Ledger（/aar/*）… C2 介面（/orders、/requests、/messages）」，並註記「護欄攔截**不在 Ledger**」——本週的 CPX 驗收正好證實了這件事。
→ 結論：`response_time`（遭襲→反應令）與 `event_chain`（call-for-fire 全鏈）**目前做不到「純函數讀 Ledger」**，必須跨源 join，或先補 Ledger 事件。
4) `compliance` 三個違規來源只有兩個成立：FRATRICIDE ✅（core/app/adjudication/fratricide.py，有 Ledger 事件）、RESTRICTED_FIRE override ✅；**「越出戰鬥地境」完全不存在**——`rg 戰鬥地境|boundary` 只命中 contracts/roe.schema.json 那段「§10 G4 提到的『不得越過某線』需要邊界幾何判定與 MOVE 令攔截，**延後至 WP-C10/B5 一併實作**」，而 C10 與 B5 都已標 ✅ 結案卻沒做這件事；**`deadline_tick` 零程式**（`rg -l deadline_tick` 只有 SPEC_V2.md）。
5) D6 前置：D6.1 地圖重播 ✅ 真的在（core/app/aar/replay.py `state_frames/bookmarks` + platform/app/composables/useAarReplay.ts），評量分頁的「時間軸標記跳轉重播」接得上；D6.2/D6.3 未做（SPEC_V2:113）。
6) 可直接照抄的形狀：core/app/aar/missions.py `build_timelines()` 已經是「純函數從事件重建各階段時長」的範例（含 duration_ticks），F5 的 event_chain/response_time 應長成同一個樣子。

**還缺什麼**

1) contracts/scenario.schema.json 增 `assessment_plan: [{id, objective, measure, params, weight}]` + core/app/scenario/ loader；2) **先裁決資料源**（見 risk），再寫 core/app/assessment/engine.py（純同步純函數，四型 measure）；3) prisma migrate 新增 `AssessmentScore`（白軍主觀評分持久化）；4) REST `/api/v1/sessions/{id}/assessment`（報告）與 `/assessment/manual`（白軍補評），走既有 faction/RBAC 中介層；5) ai/matso_ai/roles.py 增 `ASSESSMENT_NARRATOR` + contracts/ai_output.schema.json 新 `assessment_narrative` $def；6) platform/app/pages/session/[id]/aar.vue 新「評量」分頁（目標×席位矩陣、時間軸標記 → 呼叫 useAarReplay 跳轉）；7) 想定旗標 `victory_display=WHITE_CELL_ONLY` + AAR 依旗標隱藏勝負；8) scenarios/examples/ 的 tutorial/armor-breakthrough 加 3 個評估點 + e2e。

**風險**：**第一個決策就會決定要不要重錄 golden**：若照 SPEC 讓評量引擎維持「純函數讀 Ledger」，就得把 ORDER_ISSUED / REQUEST_SUBMITTED / REQUEST_DECIDED 補進 Ledger——事件序列與 hash 立刻變動，`ops/tools/rerecord_golden.py` 必跑，且所有既有錄影一併失效。若為了避開重錄改成「評量引擎讀 DB 關聯表」，代價是評量結果無法在重播中重現、也違反 adjudication/純函數的設計慣例——**這個取捨要先問使用者，不要自己選**。第二個坑：照 SPEC 的驗收寫「3 個評估點（反應時間/火協鏈/違規）」會做到一半才發現 compliance 的兩個來源（戰鬥地境、deadline）**在系統裡根本不存在**，第三個評估點只能退回 FRATRICIDE + RESTRICTED_FIRE override。第三：紅線——分數必須是確定性計算或白軍人工，`ASSESSMENT_NARRATOR` 只能產敘事，schema 上就要禁止它回數字分數（否則就是 AI 裁決事實）。

**使用者價值**：最高。統裁演後第一次拿得到 per-seat 的具體事實——「S3 從遭襲到下反應令花了 14 tick」「火協鏈 5 次申請只有 3 次走完」「越權/誤傷 2 次」——而不是只有一句勝負；白軍可對「計畫可行性/處置至當性」補主觀評分；依 [JCATS-A p.15] 訓練型演習還能把勝負藏起來只給白軍看。這是把系統從「兵推玩具」變成「訓練系統」的那張卡。


### G2 — Tailwind 決策：main.css 未接線，決定移除相依或真正接上

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2.md:1155 表格列 G2，無 ✅（狀態欄空白）。SPEC 建議「移除」。

**查證證據**

(1) 相依存在且**部分接線**：`platform/package.json:24` `@tailwindcss/vite ^4.3.2`、`:34` `tailwindcss ^4.3.2`；`platform/nuxt.config.ts:2` `import tailwindcss from "@tailwindcss/vite"`、`:62` 放進 `vite.plugins`。所以 vite plugin **每次 build 都在跑**（含 @tailwindcss/oxide 原生二進位，package-lock.json:4447 起 12 個平台包）。(2) 但唯一含 `@import "tailwindcss";` 的檔案 `platform/app/assets/css/main.css`（全檔就這一行）**沒有進 `nuxt.config.ts:42` 的 `css: ['maplibre-gl/dist/maplibre-gl.css','primeicons/primeicons.css']`**，也沒有任何 `import`——`rg -n "main.css|assets/css" platform/`（排除 node_modules）只命中 `platform/app/app.vue:19` 的一行註解：「專案未把 assets/css/main.css 掛進 nuxt.config 的 css[]，故全域樣式置此以確保載入」。→ tailwind 一個 utility 都沒生成過。(3) **零使用**：對 `platform/app/` 全部 class 屬性掃 40 個常見 utility（flex/grid/p-N/px-N/gap-N/text-sm/bg-*-N/rounded/items-center/…）只得到 `block` 12 次、`hidden` 1 次，逐條核對全是自訂 scoped class：`armory.vue:709,787,877` 的 `mobility-block`、`scenario-editor.vue:879,883,909` 的 `msel-block`/`victory-block`、`InjectActionForm.vue:324,384,408,421,471,513` 的 `iaf-block`、`cop/MapEditorPanel.vue:167` 的 `:class="{ hidden: … }"`（配 scoped `.hidden`）。**沒有任何一處真正的 tailwind class**。(4) 全站深色基底寫在 `platform/app/app.vue` 的非 scoped `<style>`（`:root{color-scheme:dark}` + html/body/#__nuxt 背景），與 tailwind 無關；主題來自 PrimeVue v4 Aura（nuxt.config.ts:6–30）。

**還缺什麼**

落地「移除」路線：① 刪 `platform/package.json` 的 `tailwindcss` 與 `@tailwindcss/vite` 兩條相依並重跑 `npm install` 更新 `package-lock.json`（oxide 的 12 個平台原生包會一併消失，air-gapped 的離線資產也跟著變小）；② 刪 `platform/nuxt.config.ts:2` 的 import 與 `:62` 的 `tailwindcss()`（`vite.plugins` 陣列若空則整個 `vite` 區塊移除）；③ 刪 `platform/app/assets/css/main.css`（若 `app/assets/css/` 因此為空目錄一併刪）；④ 修 `platform/app/app.vue:19` 那條指向 main.css 的註解（檔案不在了，註解會變誤導）；⑤ 重跑 `cd platform && npm run lint && npm run typecheck && npm test`，並 `cd ops/compose && docker compose up -d --build frontend` 確認 SSR build 仍綠（這是唯一會真的執行 vite plugin 鏈的路徑）。

**風險**：風險極低但有兩個真坑：(1) **不要只跑 `npm run lint`**——tailwind 只在 `nuxt build` 的 vite 階段被引用，本機 lint/typecheck 不會碰到；照 CLAUDE.md 前端一律走 container，必須 `docker compose up -d --build frontend`（單獨 `build` 不換容器）。(2) `package-lock.json` 會大量變動（oxide 跨平台包），diff 很大但屬預期；若 CI 有 lockfile 一致性檢查要一起更新。(3) 若未來想改走「接上並漸進採用」路線，成本反而較高（要把 main.css 加進 `css[]`、確認 tailwind preflight 不會蓋掉 PrimeVue Aura 與 app.vue 的全域樣式）——SPEC 建議移除是對的。golden 完全無關。

**使用者價值**：使用者看不到任何差別，這是純基礎建設。真正的收益是誠實：現在 repo 對外宣稱依賴 tailwind、CI 每次都下載/編譯 oxide 原生二進位、air-gapped 部署要多帶一組資產，而畫面上一個 tailwind class 都沒有。移除後「這個專案用 scoped CSS + PrimeVue」變成程式碼講得出來的事實，下一個開發者不會再誤以為可以直接寫 utility class（現在寫了會靜靜沒效果——這正是本 repo 的招牌病）。


### G6 — i18n 骨架：字串抽 locale 檔（zh-TW 預設），先讓硬編碼停止增生

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2.md:1164 表格列 G6，無 ✅。驗收「新增程式碼可用 t()；存量漸進」。

**查證證據**

(1) **完全沒有任何 i18n 基礎**：`rg -n '\"@nuxtjs/i18n\"|vue-i18n|useI18n|\\$t\\(' --glob '!node_modules' .` 在整個 repo **零命中**；`platform/package.json` 的 modules 清單（nuxt.config.ts:30）也沒有 i18n module。\n(2) **硬編碼規模（實測，非估計）**：`platform/app/` 下 80 個檔含中日韓字元；扣掉 `app/types/api.ts`（353 行，那是契約 description 生成物、不是 UI 文案）後、再濾掉 `//`/`*`/`<!--` 開頭的註解行，仍有 **1901 行**含中文。其中真正是使用者可見文案的：樣板文字節點 `>…中文…<` **526 處**、`label=`/`placeholder=`/`title=`/`header=`/`aria-label=` 屬性 **115 處**、JS 單引號字串字面量含中文 **747 處**（含 `useLabels.ts` 那類對照表）。粗估待抽字串量在 **1200–1400** 條量級。\n(3) **分布高度集中，這是好消息**：`components/map/MapCanvas.vue` 263、`components/cop/UnitsOrderPanel.vue` 181、`composables/useCopFeed.ts` 179、`pages/scenario-editor.vue` 175、`pages/armory.vue` 168、`pages/session/[id]/cop.vue` 162、`pages/session/[id]/white-cell.vue` 126、`composables/useCopOrdering.ts` 126、`components/ExercisePanel.vue` 119、`composables/useUnits.ts` 118、`composables/useWeaponVocab.ts` 110、`composables/useMapEditor.ts` 109。前 12 個檔就占掉一半以上。\n(4) **已有一個半成品的集中點可以當骨架的種子**：`platform/app/composables/useLabels.ts`（100 行含中文）的模組註解明說它存在的理由就是「畫面上一直有裸英文代號漏出去…這些代號分散在各面板各自 `{{ x.status }}` 出來，沒有一個共用的地方可以補——於是每加一個新畫面就漏一次」，內含 `STREAM_STATUS_LABELS` 等對照表。同型的還有 `useUnits.ts:70` 的 `UNIT_LEVEL_LABELS`、`:86` 的 `POSTURE_LABELS`（含 hint 長句）、`useWeaponVocab.ts`。**這些是領域詞彙表不是 UI 文案**，抽 locale 時要不要一起搬是設計決策。\n(5) **e2e 耦合是真的但可控**：`platform/e2e/` 裡靠中文字串定位/斷言的只有 55 處——`getByText/hasText` **2 處**（`fire-plan.spec.ts:69` `getByText('敵我皆受損')`）、`toContainText/toHaveText` **53 處**（如 `fire-plan.spec.ts:57 '攻擊準備射擊'`、`exercise-panel.spec.ts:72 '進入「整備」'`、`:178 '已銷毀 1 局'`）。其餘一律走 `data-testid`。只要 zh-TW locale 逐字保留原文，這 55 條不會紅。\n(6) **建置環境沒有 air-gapped 阻礙**：`platform/Dockerfile:6-7` 是 `npm ci` 從 registry 拉，`platform/node_modules` 在 `.gitignore:10`——加一個 module 不需要離線資產處理（與 bge-m3 模型檔那類問題不同）。

**還缺什麼**

骨架（本卡的實際範圍）：① `platform/package.json` 加 `@nuxtjs/i18n`（Nuxt 4 相容版本要先確認，Nuxt 4 + i18n v10 的相容性是這張卡第一個要驗的東西）並加進 `nuxt.config.ts:30` 的 `modules`；② 建 `platform/i18n/locales/zh-TW.json`（預設，`defaultLocale: 'zh-TW'`，`strategy: 'no_prefix'`——這是唯一語言時的正確設定，否則所有路由會被加前綴而打爛 `platform/e2e/*.spec.ts` 的 `page.goto` 與 `pages/session/[id]/*` 的路由）；③ 建 key 命名規範文件（建議按檔案/面板分 namespace：`cop.*`/`whiteCell.*`/`armory.*`/`scenarioEditor.*`），寫進 `HOW_TO.md §3` 前端規範；④ **先遷一個中等大小的樣板檔證明可行**（建議 `platform/app/components/cop/C2Panel.vue`，79 行中文，體積適中且不在別的 workflow 手上）；⑤ 決定 `useLabels.ts`/`UNIT_LEVEL_LABELS`/`POSTURE_LABELS`/`useWeaponVocab` 這批**領域詞彙表**要不要搬進 locale——建議**不搬**（它們是 enum→中文的對照，屬領域模型不是介面文案，搬進去只會讓 locale 檔混入 500+ 條不會被翻譯的軍語），但要在規範裡寫明這條界線，否則下一個人會亂搬；⑥ 加一條可選的 lint 規則或 CI grep，讓**新增**的樣板文字節點含中文時警告（這才是「讓硬編碼停止增生」的實際手段，沒有這一條卡就只是加了個沒人用的 module）。存量 1200+ 條的遷移**不在本卡**，按 SPEC 是漸進。

**風險**：(1) **`strategy` 設錯會炸掉全部路由**：@nuxtjs/i18n 預設 `prefix_except_default` 會產生 `/zh-TW/...` 路由，`platform/e2e/` 全部 `page.goto('/session/…')` 與 `navigateTo` 會走到不同路徑。單語言必須 `no_prefix`，這是最可能一次踩死的坑。\n(2) **SSR + i18n 的水合**：`platform/app/app.vue:4-7` 有 `hydrated` 旗標，e2e 全靠 `[data-hydrated=true]` 等水合；i18n module 會插入自己的 plugin，若造成 SSR/client 文案不一致會出水合警告甚至內容閃動——必須實跑 container（`docker compose up -d --build frontend`）看，本機 lint/typecheck 抓不到。\n(3) **55 條 e2e 文字斷言**：zh-TW 逐字照抄就不會紅；但只要遷移時「順手改了文案」就會紅一片，且錯誤訊息看起來像功能壞掉。規範裡要明寫「遷移＝搬家不改字」。\n(4) **本 repo 招牌病的高危形態**：很容易做成「module 裝好了、locale 檔建好了、一個 `t()` 呼叫都沒有、下一張卡繼續硬編碼」——那就是標準的 DONE_BUT_BROKEN。所以驗收一定要含「至少一個真實面板已遷移且 e2e 綠」＋「防增生的 lint/grep 閘門存在」，只裝 module 不算完成。\n(5) 另有 workflow 正在改 `platform/app/pages/session/[id]/white-cell.vue` 與 UI 檔，樣板遷移檔要避開。\n(6) golden 完全無關（純前端文案），不碰任何紅線。

**使用者價值**：對統裁/參謀而言**現在看不到差別**，這是基礎建設；誠實地說，在只有 zh-TW 的情況下它對使用者的即時價值是零。它的價值是選擇權：MATSO 是兵推系統，多國聯演或對外展示時「介面能不能換語言」是會被直接問到的問題，而目前答案是「要改 80 個檔的 1200 條硬編碼」。做了骨架之後答案變成「加一個 locale 檔」。次要價值是文案一致性——同一個概念現在在不同面板各寫各的（`useLabels.ts` 的模組註解已經記錄了這個病：「每加一個新畫面就漏一次」），集中之後改一次到處生效。若排優先級，這張卡應排在 G5 之後、且僅在有明確的多語需求時才值得做完整遷移。


### H1 — 多站演習架構（主站權威模擬 + 遠端站 Relay 唯讀複本），V2.2 交付＝ADR＋單機雙行程 PoC

**規模** L　|　**前置** 無　|　**文件說** SPEC_V2 §WP-H1（行 1172–1181）標 ★★「設計先行」，未標 ✅；V2.2 路線圖行 1255 列「H1 多站 ADR+PoC」。無 PROGRESS/TASKS 條目。

**查證證據**

零實作：`rg -in '\brelay\b|master.*site|site.*admin' core/ platform/app/ contracts/` 只命中 `core/tests/unit/test_comms_link_budget.py:63`（無線電中繼測試，與多站無關）；`git log --oneline --all | rg -i 'relay|多站|multi-site'` 只命中 O1.4/CI 兩筆誤配。PROGRESS.md／TASKS.md 全檔無 H1 條目。ADR 目錄最新為 `docs/adr/007-rollback-logical-truncation.md` → SPEC 寫「ADR-007 草案」**編號已被占用，H1 實際要開 ADR-008**。

地基查證（SPEC 那句話對了一半）：
1. ✅ 「事件流本質上就是可訂閱的狀態複製流」成立：`core/app/state/redis_stream.py` 的 `publish_to_stream` 以 Lua 原子指派 seq → RPUSH ring → PUBLISH，ring/channel 皆 per-session（`session:{id}:ring` / `session:{id}:stream`），`RING_CAPACITY = 5000`（`core/app/state/broadcaster.py:26`）。STATE_DIFF 是覆寫式 per-unit 欄位 map，依 seq 套用可重建熱狀態。
2. ✅ **每陣營投影在「生產端（broadcaster）」做，不在 WS 層**：`RedisBroadcaster._envelopes()`（broadcaster.py 尾段）每 tick 發 **N+1 份**信封——每陣營一份 `factions:[F], exclusive:true` 的**已投影**副本（`project_diff` 套可見集＋位置凍結）＋一份 `factions:[]` 的真實副本。這對 Relay 是好消息：投影已在主站完成，Relay 只需轉發＋套 `is_visible`。
3. ❌ **但現有 WS 端點無法把「全流」交給 Relay**：`core/app/stream/faction_filter.py:_faction_visible` 最後一行 `return omniscient and not envelope.get("exclusive", False)` —— 全知身分**收不到**任何 `exclusive:true` 的每陣營副本。Relay 以全知帳號連 `WS /api/v1/sessions/{id}/stream` 只會拿到真實副本＋全域事件。
4. ❌ **而真實副本不足以在 Relay 端自行重投影**：`broadcaster.py` 的 `_INTERNAL_FIELDS`（`REPORT_LAT_KEY/REPORT_LNG_KEY/REPORT_TICK_KEY/MISSION_COUNT_KEY/ARRIVED_TICK_KEY`）被 `public_diff()` 剝掉——那三個正是 `project_position()` 做位置凍結的輸入。Relay 拿真實副本重投影會得到「永不凍結」的錯誤結果。
5. ❌ **SPEC 的「golden replay 證明了事件流重放＝狀態重建」是錯的**：`core/tests/replay/harness.py:1-10` 與 `run_replay()` 的定義是「相同 (master_seed, 想定) **從 tick 0 重跑 Kernel** → 比對 `compute_state_hash`」——它證明的是**重新模擬的確定性**，完全不消費 WS envelope 流。「套用 envelope 流即可重建狀態」在本 repo **零測試覆蓋**。
6. ❌ **沒有 relay/唯讀模式**：`core/app/main.py:60-64` 無條件啟 `SimManager`（僅 `STUB_GATEWAY` 或 `MATSO_DISABLE_SIM=1` 例外）；`core/app/sim_runtime.py:381` 的 `SimManager` 掃 DB 為**每個未封存 session 自動起 Kernel**。第二個 core 實例指向複製 DB＝立刻變成第二個權威模擬器（雙寫熱狀態、雙寫 Ledger）。
7. ❌ 前端只有單一 `apiBase`（`platform/nuxt.config.ts:48`），沒有「讀走 Relay／寫回主站」的雙位址概念。
8. ✅ E3 `/state` 存在可復用（`core/app/api/state.py:75`），但它是**per-observer 從 DB 組**（呼叫 `list_units`/`get_intel`/`list_map_features`/`get_faction_relations`），Relay 站要靠它 bootstrap 就得有 DB 與身分。

**還缺什麼**

1. **ADR-008（非 007，編號已被 rollback 占用）**：站間拓撲、信任邊界、Relay 是否持有 DB、下令回程路徑。
2. **全流訂閱傳輸**：新端點（例如 `WS /api/v1/sessions/{id}/site-stream`）＋站台身分（新的 token 類型，非 `SessionParticipant`），並在 `core/app/stream/faction_filter.py` 之外走一條**不套 `is_visible`**的路徑（絕不可放寬 `is_visible` 本身——那是紅線 3 的唯一閘門）。需在 `core/app/stream/identity.py` 加 `WsIdentity` 之外的 `SiteIdentity`。
3. **Relay 模式旗標**：`core/app/config.py` 加 `instance_role: MASTER|RELAY`，`core/app/main.py:60-64` 依此不啟 `SimManager`；並讓寫入類端點（orders/control/inject/reposition）在 RELAY 下 405 或代理上游。
4. **Relay 端狀態重建器**：新模組（例如 `core/app/relay/replicator.py`）訂閱全流 → 依 seq 套 STATE_DIFF 進本地 `HotStateStore` → 對站內 client 重新以 `is_visible` 過濾轉發；斷線以 ring 續傳、超出範圍走 `/state` RESYNC。
5. **envelope 流可重建狀態的證明**：新增 replay 測試「Kernel 跑 N tick 產生的 envelope 序列，套用後與 `hot_state.get_all()` 逐鍵相同」——這是 H1 的正確性地基，目前不存在。
6. **前端雙位址**：`platform/nuxt.config.ts` 的 `apiBase` 拆成 `readBase`/`writeBase`，或 Relay 提供寫入代理。
7. PoC 收尾：延遲注入測試（站間 RTT）、`ops/compose` 加第二個 core service。

**風險**：（a）**最大的坑是 SPEC 自己的地基敘述**：「golden replay 證明事件流重放＝狀態重建」不成立（golden 是重跑 Kernel），且真實副本被 `_INTERNAL_FIELDS` 剝掉位置凍結輸入 → 「Relay 收全流自行按 faction 過濾」若照字面實作，會做出**永不凍結位置**的 Relay，等於在遠端站洩漏斷聯單位的即時真實座標。正確做法是轉發主站**已投影**的 `exclusive:true` 副本，不是在 Relay 重投影。（b）紅線 3：新增站台傳輸時若在 `is_visible` 加任何「站台旁通」分支，等於開後門；必須另走一條路徑並讓 Relay 對其 client 重跑同一個 `is_visible`。`core/tests/unit/test_stream_audience_truth_table.py` 釘住了現有分支，改動會紅。（c）`SimManager` 自動掃描起 Kernel 是隱形殺手——忘了關就是雙權威模擬，兩邊各自寫 Ledger 與熱狀態，症狀是「Relay 的單位自己會動」。（d）ring 只有 5000 條、每 tick N+1 份，3 陣營 ≈ 1250 tick；1 秒/tick 的想定下站間斷線超過約 20 分鐘就必須全量 RESYNC。（e）golden **不需要重錄**（不碰裁決），這點是好消息。

**使用者價值**：做完 ADR＋PoC 後，統裁與參謀**短期看不到任何差別**——這是純基礎建設。真正的可見能力要到生產硬化後：A 駐地主控演習、B 駐地參謀在本地低延遲 COP 看同一場推演且仍只看得到自軍該看的東西，下令自動回送主站。以 V2.2 的交付定義（ADR＋單機雙行程 PoC）而言，使用者端零可見變化。


### H2 — DIS/HLA 互通評估（RPR-FOM 對映表＋時間管理相容性分析＋單向 DIS PDU 廣播最小切入點），產出＝ADR

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2 §WP-H2（行 1183–1188）標 ★「設計先行」，明說 V2 只做評估卡、產出＝ADR；§8 非目標第 4 條再次限縮「V2 僅 ADR 與單向 DIS 廣播評估，FEDEP 全流程是 V3+」。無 ✅、無 PROGRESS/TASKS 條目。

**查證證據**

零實作、零文件：`rg -in 'HLA|RPR-FOM|DIS PDU|FEDEP|federate'` 全 repo（排除 node_modules/.venv）只命中 SPEC_V2.md 自身與 `DISAGGREGATE`/`disable` 等誤配。`docs/adr/` 只有 001–007，無互通相關。contracts/ 下無任何 DIS/HLA 相關 schema（`contracts/proto/` 只有 terrain/weather/comms 三個內部 gRPC 服務）。

評估所需的既有素材盤點：
- 實體模型：`app/models/tables.py` 的 `TacticalUnit`（faction 為自由字串、`unit_level` 列舉、`attributes` JSON）＋ `EquipmentTemplate`/`EquipmentInstance`。**沒有任何「實體種類編碼」欄位**可直接對映 RPR-FOM 的 EntityType（kind/domain/country/category/…），對映表得從零建。
- **時間管理是這張卡最大的實質障礙**：`core/app/engine/clock.py` 的 `SimTime` 只有 `(tick, sim_time_ms)`，`sim_time_ms` 是「自 tick 0 起算」的相對毫秒——**全系統沒有絕對時間錨點**。唯一接近的是 `core/app/engine/daylight_wiring.py:50 start_minute()`，那只是「一天中的第幾分鐘」（`start_min`），沒有日期。DIS PDU 的 timestamp 與 HLA 的 logical time 對映都需要一個 scenario epoch，目前不存在。
- tick 率可變：`sim_params` 的 `tick_rate_ms` 與 `pace_compression`（`TickPacer`）＋白軍 PAUSE 旗標（`core/app/sim_control.py`）——時間管理分析必須處理「可暫停、可壓縮」這件事。
- 態勢輸出面現成：`RedisBroadcaster` 的 STATE_DIFF 真實副本（`factions:[]`）正好是單向廣播的天然來源，且 `public_diff()` 已剝掉內部欄位。

**還缺什麼**

1. **ADR-009**（若 H1 先落 008）：三節——(a) MATSO 實體模型 ↔ RPR-FOM `BaseEntity/PhysicalEntity/Platform` 對映表（逐欄：`TacticalUnit.id`→EntityIdentifier、`faction`→ForceIdentifier、`unit_level`+裝備→EntityType、`current_lat/lng`→WorldLocation、`strength/personnel_current` 無對應需自訂 FOM 擴充）；(b) 時間管理相容性：tick 制（可暫停/可壓縮/無絕對錨點）vs HLA time-regulating/constrained，直接沿用 [JTLS-F p.1055–1057] 的「受訓者時間感知不被破壞」為驗收準則；(c) 最小切入點＝單向 DIS Entity State PDU 廣播（唯讀、無所有權轉移）。
2. 若要連最小切入點也 PoC：需先補 **scenario 絕對起始時間**（`ExerciseSession`/scenario schema 加 `start_datetime`，DB migration＋contract 變更），否則 PDU timestamp 無從產生。
3. 對映表要標明「MATSO 有但 RPR-FOM 沒有」的欄位（壓制度 `suppression`、通聯狀態 `comms_state`、燃油）與相反方向的缺口，供 V3 決策。

**風險**：（a）這是**純文件卡、零程式碼**，不碰 golden、不碰紅線——風險主要是「寫成無法驗證的空話」。ADR 應以可檢查的對映表（逐欄）與明確的不對映清單收尾。（b）唯一會延伸出程式碼的是「絕對時間錨點」：一旦決定加 `start_datetime`，就會牽動 scenario schema、DB migration、`daylight_wiring` 的 `start_min`（兩者語義重疊，要說清誰是權威），這條若順手做就會超出「純 ADR」的範圍——建議在 ADR 裡列為 H2 的後續卡而不是本卡做掉。（c）別把 `faction` 自由字串當成可直接映到 ForceIdentifier：ADR-006 的 N 陣營關係矩陣是三值對稱關係，RPR-FOM 的 Friendly/Opposing/Neutral 是**相對於觀測者**的，要在 ADR 講明轉換規則（需帶觀測陣營參數）。

**使用者價值**：使用者零可見變化——這是一份決策文件。真正的價值在採購／整合階段：能明確回答「MATSO 能不能跟現有 JTLS/JCATS 或第三方 COP 掛上」以及「掛上的最小代價是什麼」。以 [JCATS-F p.6–7,17] 所言介面標準是國軍運用首要窒礙，這份文件的價值是對外的，不是對統裁的。


### H4 — 災防/民事想定（疏散/收容/交通壅塞/白軍判效）——V2 僅記錄方向，實作待 B/C 群後另立 SPEC_CIVIL

**規模** XL　|　**前置** H1　|　**文件說** SPEC_V2 §WP-H4（行 1196–1201）明說「此處僅記錄方向，待 B/C 群落地後另立 SPEC_CIVIL」——**本卡在 V2 的交付其實就是 SPEC 那段文字本身**。總表第 35 項（行 124-125）★ 遠期，狀態欄空白。V2.2 路線圖（行 1255）**沒有列 H4**。

**查證證據**

零實作：`rg -in '災防|疏散|收容|shelter|evacuat|CBRN|plume|civil'` 全 repo（排除 node_modules/.venv）只命中 SPEC_V2.md 自身與參考文獻檔名。無 `SPEC_CIVIL.md`。無 PROGRESS/TASKS 條目。

**SPEC 的關鍵論斷「災防 CPX 主要用移動/通聯/後勤/MSEL——交戰模組反而非必要」——四個子系統的現況查證（這條決定 H4 是小還是 XL）：**
- ✅ **移動**：`core/app/sim_runtime.py:747` `UnitMovementSystem`，已含地形/坡度調速（#81）、A* 繞路（#82，`path_fn=build_terrain_path_fn()`）、油料消耗（#84）、天氣機動（`weather_for`）、夜間行軍（`light`）。這是全系統最成熟的子系統。
- ✅ **通聯**：`sim_runtime.py:786` `CommsSystem`（取代 NoOp，#33），已接地形 LOS（`gateway`）與天氣（`weather_for`），後果已生效（位置凍結見 `broadcaster.project_diff`）。
- ✅ **後勤**：`sim_runtime.py:797` `ResupplySystem`（#85），實作於 `core/app/engine/logistics.py`——RESUPPLY 令驅動、`RESUPPLY_RANGE_KM=2.0`、撥交油料＋補彈（`_ammo_shortfall`/`_refill_ammo`）。
- ✅ **MSEL**：`sim_runtime.py:803` `trigger_checker=msel_runtime`（WP-B2 接上 tick）。
**⚠ 專案記憶檔 `live-runtime-subsystems.md` 說「sensors/comms/logistics 仍 NoOp」——那份記憶已過時，四個全都接上了。**

**但四個「可用」不等於「可辦災防 CPX」，實質缺口有五處：**
1. **MSEL 動作集是軍事的**：`core/app/scenario/msel_actions.py:54-77` 只支援 `SPAWN_UNITS` / `MODIFY_UNIT` / `MESSAGE` / `PAUSE` / `WEATHER_OVERRIDE`，其餘一律 emit `MSEL_INJECT_UNSUPPORTED`。沒有「道路封閉」「災害範圍擴散」「收容所開設/滿載」等世界效果。
2. **沒有民事實體**：`SPAWN_UNITS` 建的是 `TacticalUnit`（`unit_level=UnitLevel(...)` 軍事編制列舉，`msel_actions.py:161`）。人口、避難所容量、傷患、交通流量沒有任何資料模型。
3. **關係矩陣預設 HOSTILE**：`core/app/factions/relations.py:34` `default: Relation = Relation.HOSTILE`。多機關協同的災防想定若忘了宣告 ALLIED，各單位彼此會被當敵人（可交戰、不共享視圖）——這是 H4 想定作者必踩的坑。
4. **勝負引擎預設「最後存活」**：`core/app/ai_loop/victory.py last_standing_conditions()` 以「其他陣營戰力歸零」為勝。災防場景無交戰 → 永不收場（可接受），但兩個協力機關中一個戰力歸零會誤判勝負。需要以 MOE 式判效（[JTLS-F] 的白軍軟裁決）取代。
5. **交通壅塞無模型**：移動系統的 A*（`modules/terrain/terrain/pathfind.py`）走地形成本，沒有流量/容量概念。

**還缺什麼**

若要從「方向記錄」推進到可跑的災防 CPX：
1. **SPEC_CIVIL.md**（新文件）：民事實體模型、判效指標、白軍軟裁決流程。這才是 H4 的下一個實際交付。
2. **民事實體資料模型**：DB migration 新增人口/避難所/傷患/設施表（或以 `TacticalUnit.attributes` 承載＋新 `unit_level`／新 domain 列舉——後者較快但會污染軍事語義）。
3. **MSEL 動作擴充**：`core/app/scenario/msel_actions.py` 的 `make_applier` 加 `ROAD_CLOSURE` / `HAZARD_SPREAD` / `SHELTER_STATUS` 等；同時要有對應的世界效果消費端（否則就是本 repo 最常見的病：存得進去、沒人讀）。
4. **壅塞模型**：`modules/terrain` 的 pathfind 加容量/流量成本，或新增獨立的交通子系統。
5. **判效取代勝負**：`core/app/ai_loop/victory.py` 之外另立災防 MOE（依賴 V2.2 的 D2 MOE 框架）。
6. **想定範例**：`scenarios/examples/` 加一份災防想定 + msel.yaml。
7. 前端：COP 圖層要能畫災害範圍、避難所、封閉路段（`MapCanvas.vue` 圖層擴充）。

**風險**：（a）**最大的風險是把這張卡誤讀成「快做得完」**——SPEC 那句「交戰模組非必要，移動/通聯/後勤/MSEL 已就緒」查證後**四個子系統確實都活著**，容易讓人以為只差想定檔。實際缺的是整個民事域的資料模型與判效框架，那是新的一個 WP，不是一張卡。（b）動 `TacticalUnit` 承載民事實體會污染軍事語義，並牽動 `platform_count_for`、`_visible_factions`、victory 的 `faction_strength` 加總（民事實體的 strength 是什麼？）——一旦誤入，`last_standing` 勝負判定會被非戰鬥實體污染。（c）關係矩陣 `default=HOSTILE` 是想定作者的隱形陷阱，SPEC_CIVIL 要明訂災防想定必須顯式宣告全 ALLIED/NEUTRAL。（d）新增裁決子系統會**動 golden**（新 RNG stream、新 tick 內順序）→ 需重錄 `core/tests/replay/goldens/*.json`，且依 §9 紀律重錄必須是最後一步、獨立 commit、worklog 先解釋為何輸出變了。（e）依賴 H1 是**軟依賴**：跨機關災防 CPX 現實上是多站的，但單站也能先跑；不要因為等 H1 而卡住。

**使用者價值**：就 V2 定義的範圍（僅記錄方向）而言，**使用者零可見變化**——SPEC 已經寫了那段文字，這張卡在 V2 內已無事可做。真正的可見能力要到 SPEC_CIVIL 落地後：統裁能辦一場「地震/颱風 + 疏散收容 + 道路中斷 + 物資調度」的災防 CPX，參謀在同一套 COP 上調度救災能量、白軍以 MSEL 誘導災情演進並判效。需求端（台灣情境）明確，但這是新 WP 的體量，不是 V2.2 的收尾項。


---

## 已完成（9）


### A3 — G4 no-strike 護欄（欄位匹配 + 資料源）

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:254 標 ✅ 2026-07-29；總表第 3 項 ✅

**查證證據**

逐層查證皆通：①判定端 `guardrails/gateway.py:53` `_STRIKE_ORDER_TYPES = {ENGAGE, FIRE_MISSION, MISSION}`（不只 ENGAGE）；②定位端 `ai_loop/orders_bridge.py:303-320` `UnitTargetLocator.locate` 先查 target_unit_id→熱狀態座標→h3，再退 target_lat/lng，最後 `_mission_objective_latlng` 讀 MISSION params；③資料源**兩條都活的**：想定宣告經 `scenario/loader.py:457 no_strike_zones=loaded.no_strike_zones or None` 落 WargameSession，白軍局中增修經 `orders/no_strike.py:138 _feature_zones()` 讀 `MapFeature.attributes.zone_class`，`load_no_strike_cells` 每次現讀不快取；④前端寫得進去：`platform/app/composables/useMapEditor.ts:241 attrs.zone_class = drawZoneClass.value`；⑤人類側同一份格集：`orders/precheck.py:129 load_no_strike_cells`；⑥`GUARDRAIL_INTERVENTION` 有生產寫入端（`ai_loop/worker.py:212` 註解直指此洞已補）+ AAR 統計端 `aar/stats.py:62`。四個示範想定全部宣告了 no_strike_zones。

**還缺什麼**

（無阻斷性缺口）兩個小硬邊：`_feature_zones` 只查 `geometry_type == "POLYGON"`，白軍畫圓或點加 zone_class 會**靜默失效**（前端 useMapEditor.ts:237 有註解承認，但沒有 UI 阻擋）；`_feature_zones` 刻意不限 owner_faction，敵方可用「令被打回」反推出我方保護區位置（次要情資洩漏，非本卡範圍）。

**風險**：若要補圓形禁射區，`zones_to_cells` 已支援 circle 幾何（no_strike.py:105），缺的只是 MapFeature 那條路——不要在 gateway 加第二套幾何。

**使用者價值**：統裁圈的醫院/古蹟真的擋得住 AI 與人類的射擊令，且攔截次數在 AAR 上看得到（不再恆為 0）。


### B2 — MSEL 排程執行引擎與白軍誘導迴圈

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2:331 標 ✅ 2026-07-31（B2a/b/c）

**查證證據**

三個原本的斷點都補上了：①持久化——`scenario/loader.py:487-490` 建局時把 `loaded.msel` 寫進 WargameSession（註解自承「過去整個漏掉」）；②執行期讀得到——`sim_runtime.py:584 load_session_msel`、:585 建 MselRuntime、:806 `trigger_checker=msel_runtime` 傳進 Kernel；③動作真的改世界——`scenario/msel_actions.py:52 apply()` 分派到 SPAWN_UNITS/MODIFY_UNIT/MESSAGE/PAUSE/WEATHER_OVERRIDE，`spawn_unit_id`（:113）由 entry_id 決定性派生（無 uuid4）。脈絡從熱狀態組不從 DB（session_msel.py 模組說明點名 BL-3 那個坑）。白軍取捨閉環完整：`api/msel.py:37/49/63` GET pending + fire + skip → `state/live_msel.py push_msel_cmd` → sim_runtime.py:884 `drain_msel_cmds` → :886 `apply_msel_cmds`；前端 `platform/app/pages/session/[id]/white-cell.vue` 有 msel 面板、`InjectActionForm.vue`/`useConditionDsl.ts` 齊備。記憶進 checkpoint：sim_runtime.py:821 `"msel": msel_runtime.memory.to_dict()` + :604 `restore_msel_memory`。四個示範想定都有 msel.yaml 且 scenario.yaml `files.msel` 指向它。

**還缺什麼**

①`delay` 未實作（api/msel.py 只有 fire/skip；SPEC_V2:359 要求 skip/delay 都要能記 Ledger 供 AAR 顯示「原定 vs 實際」）。②`MODIFY_UNIT`（msel_actions.py:205-250）只認 `strength`/`lat`/`lng`——白軍軟裁決改不了彈藥、油料、補給水位、壓制度，而規格把它定位成「白軍例外通道」（WP-B4 明說裝備模板被鎖後例外走 MODIFY_UNIT）。③含 MSEL 的 golden 案例仍未新增（SPEC 自承）。

**風險**：擴 MODIFY_UNIT 的欄位要同時雙寫熱狀態與 DB（msel_actions.py:214 的模組說明點名 BL-4 那個坑），彈藥還要寫 EquipmentInstance.currentState（單武器彈藥不寫回 DB 正是本週抓到的缺陷之一）。新增含 MSEL 的 golden 會是第一個真的會被 MselRuntime 改到位元的案例，錄之前先確認 TriggerContext 的記憶有進信封。

**使用者價值**：統裁能把「D+2 紅軍增援」寫進腳本自動發生，並在控制台一鍵扣發或跳過 manual 狀況——CPX 誘導迴圈的心臟。


### C10 — 計畫火力與 call-for-fire 作業鏈（C10.1–C10.5）

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:805 標 ✅ 五張子卡全數結案（2026-07-30/31）；總表第 17 項 ✅

**查證證據**

五條路徑都有生產呼叫端：①面射擊——`sim_runtime.py:675 FireMissionOrderSource(...)`、:714 `FireMissionCommand: AreaFireAdjudicator(...)`；②排程/FirePlan——`sim_runtime.py:852 run_due_fire_missions`（`fires/scheduler.py`）+ `api/fire_plans.py`；③陣位變換 C10.5——`sim_runtime.py:836 run_due_displacements`（`fires/displacement.py`）+ `:577 load_session_survivability`，且 `scenarios/examples/armor-breakthrough/scenario.yaml:99 survivability_move:` 真的有宣告（未宣告 → 停用，survivability.py:94 註解明說）；④觀測判定 + BDA——`engine/fire_wiring.py:29 from app.adjudication.bda import build_bda_event`，:453 `if self._bda_rng is None or verdict is not ObserverVerdict.OBSERVED: return None`（沒有觀測就不發回報，也不發 0，避免假情報）、:455 shooter_faction 為空也不發（避免退回全域廣播把戰果送給挨打方）；⑤火協審批——`indirect_fire_requires_approval` 在 loader.py:467 落地，armor-breakthrough:93 宣告 true；`orders/precheck.py:172 _precheck_fire_mission_no_strike` 讓面射擊同樣受禁射區約束。PROGRESS 記有活系統端到端校準驗證（18 發實彈、有前觀 14.63% vs 錨點 14.8%、無前觀懲罰 2.0 倍散布）。

**還缺什麼**

（無阻斷性缺口）SPEC 提到但未做且不在 C10 五卡範圍內：`ILLUM` 照明彈火力任務（C4a 的未竟項，需要局部短暫的光照覆寫實體）。`survivability_move` 未宣告即停用是刻意的中性預設，四個示範想定只有一個開著——不算缺陷但值得知道。

**風險**：要加 ILLUM 得同時碰 C4 的光照層與 C10 的彈種分派；別把它塞進 SmokeCloud（那是 blocks_los 的布林覆寫，光照是另一個軸）。

**使用者價值**：參謀可以下火力計畫、按時間或呼叫排程落彈、拿到帶迷霧誤差的 BDA 回報，砲兵打完會自動變換陣地——完整的砲兵作業鏈。


### C5 — 通聯後果閉環：位置凍結與敵情粗化

**規模** M　|　**前置** 無　|　**文件說** SPEC_V2:659 標 ✅ 2026-07-30；總表第 12 項 ✅

**查證證據**

四層都查過且都真的在跑：①產出端——`engine/comms.py:252 _position_report` 依 `position_report_interval` 寫 `report_lat/lng/tick`，CommsSystem 取代 NoOp（`sim_runtime.py:786 comms=CommsSystem(`），且 `mesh_states` 真的傳了 obstructed（地形遮蔽，_LOS_CACHE_RES=9 快取）與 attenuation_db（吃 weather 回呼），不是空參數。②廣播投影——`state/broadcaster.py:212/219` 每陣營一份投影 + `stale_since_tick`（恢復通聯時送 null 清標記）。③API 投影——`api/units.py:108 projected.lat/lng/stale_since_tick`。④AI 一致——`ai_loop/worker.py` 註解與程式都在取快照後立刻 `projected_snapshot`，敵情粒度走 `faction_granularity`。⑤前端消費得到——`useLiveState.ts:70` 用 `'stale_since_tick' in p` 判斷（不是 typeof number，正確處理 null）、`cop.vue:324 commsPosture`。IntelService 依 granularity 量化/降級。

**還缺什麼**

（無阻斷性缺口）已知界線（SPEC 自承、已記 backlog）：`IntelGranularity.FROZEN` 目前與 COARSE 投影效果相同——`IntelContact` 沒有觀測者單位欄位，做不到「回報該筆情報的單位斷聯 → 該筆凍結」。要修得先給 IntelContact 加 observer_unit_id（DB migration + 契約）。

**風險**：若要做真 FROZEN，加 observer_unit_id 會影響 sensor sweep 的寫入路徑與 checkpoint 的狀態雜湊；且「哪一個單位看到的」本身是敏感資訊，投影層要確保它不外洩到 GET /intel 的回應裡。

**使用者價值**：斷聯的部隊在自家 COP 上真的凍在最後回報位置（半透明 + 時間戳），統裁的 god view 照樣看得到真實位置——「我的指揮所現在到底知道什麼」變成一個看得見的事實。


### C6.2 — 聚合門檻讀想定 aggregate_adjudication_level（不再寫死 BATTALION）

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:694「should_aggregate 讀想定 aggregate_adjudication_level（現寫死 BATTALION）」；總表第 13 項仍寫「threshold 忽略想定欄位」——**這一句已經過時**。

**查證證據**

四層全部查證一致：契約 `contracts/scenario.schema.json:55`（enum BATTALION/BRIGADE/DIVISION）；DB `db/prisma/schema.prisma:81 aggregateAdjudicationLevel UnitLevel?` + migration `db/prisma/migrations/20260801120000_aggregate_level/migration.sql`；ORM `core/app/models/tables.py:97`；loader 落地 `core/app/scenario/loader.py:256 _agg_level()` + `:472`；開局讀取 `core/app/sim_runtime.py:105-110 _aggregate_level()` 並於 `:709` 注入 `EngagementAdjudicator(aggregate_level=...)`；裁決層消費 `core/app/adjudication/adjudicator.py:198`（`aggregate_level or UnitLevel.BATTALION`）→ `:213 should_aggregate(shooter_unit.unit_level, self._aggregate_level)`；clone 帶過去 `core/app/lobby/service.py:188`；前端 `platform/app/pages/scenario-editor.vue:106-110` + `platform/app/composables/useScenarioEditor.ts:89/237/378`。**有 AST 測試釘住呼叫端真的傳了第二個參數**：`core/tests/unit/test_unit_level_order.py:106-130`（作者自承第一版測試放過了「呼叫端不傳」這個突變）。commit `573dba2 fix(adjudication): aggregate_adjudication_level 接上`。

**還缺什麼**

只剩兩個小殘留（不影響本卡結案）：1) 契約 enum 只有 BATTALION/BRIGADE/DIVISION，**無法把門檻調低到 COMPANY/PLATOON**——`should_aggregate(COMPANY, COMPANY)` 在 `core/tests/unit/test_unit_level_order.py:83` 是 True，引擎支援，但想定寫不出來（要改 contracts/scenario.schema.json + 前端型別 useScenarioEditor.ts:89）。2) 四個出貨想定 `scenarios/examples/*/scenario.yaml` 全都寫 BATTALION（＝`_agg_level` 一律回 None），所以**沒有任何出貨想定在跑非預設門檻**——功能是活的但沒有活體驗證資料。

**風險**：幾乎無風險。若要順手做上面兩個殘留：改契約 enum 屬紅線 4「契約先行」，要先 buf/schema-sync 驗證再實作；把門檻調到 COMPANY 會讓大量連級單位改走 Lanchester 路徑，而 `_AGG_*` 係數（C6.4）尚未校準，戰損量級可能失真。

**使用者價值**：想定作者在劇本編輯器把「聚合裁決層級」設成 BRIGADE，營級單位就會改走逐平台裁決而不是 Lanchester——這個下拉選單過去改了完全沒作用。屬於「本來就該有、只是壞掉」的修復，統裁不會覺得多了功能。


### D6.1 — AAR 地圖重播 + 聚合戰損歸帳（查證既有 ✅）

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:948-962 標「D6.1 ✅ 已完成（2026-07-30）」；SPEC_V2:113 總表第 23 列亦標 D6.1 ✅。**這一張是少數文件與現實相符的**。

**查證證據**

端點真的存在且接線：`core/app/api/aar.py:107` `GET /{session_id}/aar/replay/states`，回「靜態底本 units[] + 逐 tick changes[]」，由 `app/aar/replay.py:state_frames` 產生；`core/app/api/aar.py:122` 註冊進 main（`core/app/main.py:121` include_router(aar_router)）。契約有：`platform/app/types/api.ts:1145` 存在 `/sessions/{id}/aar/replay/states` 路徑（openapi 生成物 → 契約先行確實做了）。前端真的接到視覺：`platform/app/pages/session/[id]/aar.vue:46` useAarReplay(replayStates, scrubTick) → `:147` `<MapCanvas :own-units="unitsAt" :current-tick="scrubTick" :fit-bounds="fitBounds" />`；書籤跳轉 `:202`。真 Playwright 驗證存在：`platform/e2e/aar-replay.spec.ts`（2 條 test），worklog 記載斷言 `queryRenderedFeatures({layers:['units']}).length > 0`（符號真的畫出來而非只有資料）。聚合戰損歸帳單側**在重播路徑**確實修好：`core/app/aar/replay.py:124-134` 明寫「聚合事件的 damage_calc 是雙方損失相加，拿它扣單側等於…」並改為只在無後態欄時才 fallback。紅線 3（fog）也有守：`api/aar.py:148` 名冊依 `_aar_visible_factions` 投影。worklog `docs/worklog/aar-map-replay.md` 內容與程式碼一致（非事後補寫）。

**還缺什麼**

三個已知界線（worklog 自陳，非缺陷但會誤導）：①`api/aar.py:180` `base_health` 恆填 100.0——tick 0 之前已受損的單位在重播起點會顯示滿血；②tick 0 基準位置取「最早一筆有座標的事件」，白軍地圖狀態編輯（`reposition_unit`）不落帳所以拖過的單位起點錯一個步長；③`api/aar.py:107` 整場 frames 一次回傳、無分頁（`aar/events.py:47 read_events` 一次撈全表），長演習會爆記憶體——這條併入 D6.3 一起修較划算。

**風險**：改 base_health 需要一個「部署事件」或 checkpoint tick0 底本，會牽動帳本語意；不要為了它去改 ledger canonical payload（紅線：改 hash chain 就要重錄所有既有局的可驗證性）。

**使用者價值**：統裁在 AAR 拉時間軸就能看到單位當時在哪、剩多少戰力，並用事件書籤跳到關鍵時刻——這已經是看得見的能力，且經真瀏覽器驗過。


### E1 — 活 session checkpoint 與崩潰復原

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:981 標 ✅ 2026-07-29；ADR 007；總表第 25 項 ✅

**查證證據**

五項規格逐條查證：①checkpointer 真的掛上——`sim_runtime.py:815 checkpointer=CheckpointManager(self._factory, extras_provider=lambda: {"rng": ..., "msel": ...})`，間隔 `:824 checkpoint_interval=sim_params.checkpoint_interval_ticks`，Kernel 於 `engine/kernel.py:159 if self._checkpointer is not None and now.tick % self._checkpoint_interval == 0` 落盤；②RNG 序列化——`state/resume.py:210 restore_rng` 逐 stream `rng.set_state(state)`；③內容擴充——熱狀態 + RNG + MSEL 記憶（sim_runtime.py:817-822）；④自動 recover + 前滾——`resume.py:235 resume_session`，關鍵設計正確：**只有熱狀態空（Redis 也沒了）才套快照**（:256 `if hot.get_all(): return`，否則會把進度倒退），`:264 forward_roll(...)` 重放 Ledger 尾段並把續接點推到 `rolled.last_tick + 1`；⑤ROLLBACK 接活——`api/control.py:37 _ACTIONS = {PAUSE, RESUME, ROLLBACK}` → `session_rollback_key` → `resume.py:153 apply_pending_rollback`（在 runner 重建時執行，維持熱狀態單一寫入者）。ADR 007 記錄了「邏輯截斷而非實體刪除」的裁決（保住 hash chain）。

**還缺什麼**

（無阻斷性缺口）一個調校問題值得記：`sim_params.py:112 checkpoint_interval_ticks = 600`。配 `pace_compression = 120.0`（sim_params.py:75）與 tick_rate_ms=60000，600 tick ≈ 300 秒牆鐘，符合規格的「5 分鐘牆鐘」；但**跑不到 600 tick 就結束的局只有 tick 0 那一份快照**，kill -9 等於整局重來。armor-breakthrough 這類迷你 CPX 正落在這個區間。建議改成 min(600, 某個較小的下限) 或改依牆鐘計。已知界線（SPEC 自承）：RNG 只還原到快照當下，最多倒退一個間隔。

**風險**：縮短間隔會增加 DB 寫入（checkpoint blob 走 ADR 002 的儲存），要量一下 tick 預算；改成牆鐘觸發會破壞決定性（快照時機不再是 tick 的函數）——寧可調小 tick 間隔也不要改判準。

**使用者價值**：core 崩潰重啟後進行中的局自動接回去，統裁不用重跑；白軍可以回滾到書籤 tick 重新推演一個決策點。


### E3 — /state 快照端點與 RESYNC 閉環

**規模** S　|　**前置** 無　|　**文件說** SPEC_V2:1016 標 ✅ 2026-07-29；總表第 27 項 ✅

**查證證據**

後端：`core/app/api/state.py:75 @router.get("/{session_id}/state")`，關鍵設計正確——**呼叫既有 handler 而非重寫過濾**（三個端點的迷霧規則本來就不一致，另寫一份必然漂移，而迷霧過濾的漂移就是資安漏洞），契約 `platform/app/types/api.ts:1794` 的說明逐字記錄了這條紀律與 `last_seq` 的用途。前端閉環完整：`platform/app/stores/sessionStream.ts:114 case 'RESYNC_REQUIRED'` → `:61 apiFetch<StateSnapshot>('/sessions/${sessionId}/state...')` → `:63` 先清掉 RESYNC 前累積的舊 patch（否則會顯示回到過去的位置）→ `:128` 只接受 seq 大於 last_seq 的 STATE_DIFF（處理「RESYNC 送出後、快照回來前」抵達的 diff）。`cop.vue:332` 與 `white-cell.vue:344` 都有對應處理。「週期重抓兜底」的 race 已移除。

**還缺什麼**

（無缺口）本卡查證後確實沒有問題。

**風險**：WP-H1 多站演習規劃複用這個端點（SPEC_V2:1180），屆時 `as_faction` 的權限判定要重看一次——目前它信任 participant.role。

**使用者價值**：斷線重連後畫面一次原子重建，不會出現「單位位置閃回舊值」或「有些圖層回來了有些沒有」。


### E4 — 監控落地（/metrics + 儀表板/告警規則）

**規模** S　|　**前置** F3　|　**文件說** SPEC_V2:1029 標 ✅ 2026-07-30（使用者裁示：不加容器）；總表第 28 項狀態欄空白

**查證證據**

端點在：`core/app/main.py:130 @app.get("/metrics")` → `metrics.REGISTRY.render()`。**九個指標全部有生產寫入端**（逐一 rg 過，排除 metrics.py 自身與測試）：tick_duration/tick_overrun/tick_completed/io_latency ← `engine/kernel.py`；ws_fanout ← `state/broadcaster.py`；guardrail_blocked ← `ai_loop/worker.py`；active_sessions（3 處）/ai_workers（4 處）← `sim_runtime.py`；llm_latency ← `ai/matso_ai/inference/role_manager.py:144`。**SPEC_V2:1046 的『未竟：io_latency / ai_workers 定義了但沒有寫入端』已經過時**（兩者都接上了）；注意 `llm_latency` 之所以會有寫入端，是因為 F3 把 RoleManager 接進了活執行期——F3 若退回直連 client，這條指標會安靜歸零。直方圖累積 bug 已修：`metrics.py:70 observe` 對每個 `value <= upper` 的桶 +1（累積值），`:122 render` 明確不再累加，且 `+Inf` 桶 = `_count`。檔案交付在 `ops/monitoring/`（alerts.yml、prometheus-scrape.yml、dashboards/、README.md）。

**還缺什麼**

①`ops/prometheus/` 與 `ops/grafana/` 仍是只有 `.gitkeep` 的空目錄，而真正的檔案在 `ops/monitoring/`——留著會讓下一個人以為監控沒做（這正是總表第 28 項『prometheus/grafana 目錄只有 .gitkeep』那句話的來源，而那句話現在會誤導）。刪掉空目錄或加 README 指路。②儀表板未在真 Grafana 開過（SPEC 自承）。③結構性前提要寫進 ADR：行程內註冊表意味著 runner 一旦被拆出 API 行程，tick 指標會**安靜地**全部歸零（`SimManager` 與 FastAPI 同行程是前提不是巧合）。

**風險**：如果為了規模化把 SimManager 拆成獨立行程（E5 負載測試很可能會逼出這個需求），tick/overrun/io_latency 三組指標會無聲失效——需要一個 CI 守門（例如斷言 `/metrics` 至少有一個非零的 tick 計數）而不是靠註解。

**使用者價值**：維運看得到 tick p99、overrun 率、WS 扇出、LLM 延遲；對統裁而言是間接的（局卡住時查得出是引擎慢還是 LLM 慢）。


---

## 各組整體觀察


### WP-A/B — WP-A/B 未竟項

【文件 vs 現實的差異】

1. **這三張卡的文件狀態難得是準的**——A4/B3/B5.4 在 SPEC_V2 都沒有 ✅，SPEC_V2:441 與 :1231 甚至明寫「B5.4（未做）」。查證結果三張全是真正的 NOT_STARTED，沒有「假 ✅」。反倒是**前三張標 ✅ 的 B5.1/B5.2/B5.3 經查是真的完成且有生產呼叫端**（registry 被 orders/validator 消費、`expend_request` 被 `orders/service.py:89` 呼叫、火協 gate 在 precheck、前端核准單選擇器已補上），與本次任務簡報預期的「✅ 不可信」相反。

2. **但 B5 有一個沒有被任何卡認領的空殼**：`RequestKind.AIR_RECON` 與 `RESUPPLY_VOUCHER` 在 `core/app` 的唯一命中是 `c2/__init__.py:43,45` 的中文標籤。使用者可以送申請、上級可以核覆、配額會扣、狀態會變 APPROVED——**然後什麼都不會發生**（沒有一次性感測掃描，RESUPPLY 也不檢查憑單）。這正是簡報描述的「存得進去、讀得回來、測試全綠、實際沒效果」原型，只是它躲在 B5.2 的 ✅ 底下。規格 SPEC_V2:461-464 明文要求這兩種核准要轉為效果。建議另開一張卡（估 M），優先度高於 B5.4 本身——因為「核准了卻沒效果」比「功能不存在」更誤導操作員。

3. **`Message` / `Request` 完全不在 checkpoint 內**：`rg -n \"Message|Request\" core/app/state/checkpoint.py` 零命中，而同一檔案卻細心快照了 `FirePlanTarget.fire_request_id`（:212、:536，註解還特別說明「不還原的話那發準備射擊再也不會落下」）。所以回滾之後，一張已 EXPENDED 的核准單不會回到 APPROVED，信文匣也不會退回。這是既有缺陷、不在任何卡的範圍內，值得記進 Backlog。

4. **規格對「想定包是 zip」的假設在本 codebase 不成立**（B3）：SPEC_V2:381 寫「`documents/` 目錄位（zip 內）」，但 `core/app/exercise/archive.py:5` 明寫 repo 裡沒有任何 zip/tarfile/gzip 機制，想定實際是 DB `Scenario.packageBlob` + JSON bundle。照規格字面做會做出一個沒有載體的東西——這與 B4 worklog 記載的「規格的寫法在這個 codebase 上不成立」是同一類問題，B3 開卡時要先做同樣的裁決並記進 worklog。

5. **F3 ✅ 成立但涵蓋面比字面窄**（與 A4 相關）：`RoleManager` + `InvocationLogWriter` 確實已接進活執行期（`decider.py:220-275` audit 預設 True → `orchestrator.py:122` → `sim_runtime.py:29`），decider.py:235 的註解「非測試引用是 0」講的是接線**之前**的狀態，現在已不適用。但 `ROLE_REGISTRY` 六個角色裡，**活路徑只用 `FACTION_COMMANDER` 一個**（decider.py:124）；STRATEGIC_PLANNER / INTEL_OFFICER / WHITE_CELL_ASSISTANT / AAR_ANALYST 註冊了卻沒有任何生產呼叫端，system_prompt 還停在 O6.1 的 `_PLACEHOLDER`（roles.py:52）。A4 要新增 RESPONSE_CELL 時會發現「加進 registry」只是最容易的一步，真正的工作是那條「LLM 產散文而非命令」的管線（G1+G2 專用路徑、非 order 的 output schema、tick 內不可同步呼 LLM）——**這條管線 A4 與 B3 共用**，兩張卡應該由同一個人接連做，先做的那張要把管線做對。

6. **A4 的地基比預期完整**：`msel_actions.py:254-295` 的 MESSAGE 注入已經能在 MSEL 觸發時把一封信投進指定 faction/seat 的信文匣並留帳本事件，SPEC_V2:357 也已預留「可掛 WP-A4 的 AI 扮演」。A4 實際只差「body 由 LLM 生成 + ai_generated 標記 + 前端徽章 + AAR 過濾」，不是從零開始。反過來說，AAR 目前**沒有任何 messages 端點**（`api/aar.py` 只有 replay/stats/missions/report/export），「AAR 可過濾出所有 AI 生成訊息」這條驗收要新建查詢面。

7. **優先序建議**（以 user_value ÷ size 計）：B3（統裁完全看得見、目前是整塊空白）> B5 的「AIR_RECON/RESUPPLY 核准無效果」修補（消除誤導）> A4（地基已備、共用 B3 的管線）> B5.4（QoL，且殲敵 REPORT 的資訊洩漏設計比實作難）。

### WP-C — WP-C 交戰引擎收尾

【文件與現實不符之處】
1. **SPEC_V2 總表第 13 項（行 103）的後半段已經過時**：「threshold 忽略想定欄位」在 2026-07-30 的 commit `573dba2` 已修好且四層（契約/DB/後端/前端）全數一致，還有 AST 測試釘住呼叫端。前半段「resolve_multiway_tick 已實作未用」則**仍然準確**。所以 C6 不是一張卡而是 1 完成 / 3 未動的混合體——狀態欄留空反而比標 ✅ 誠實。
2. **C6 的四個子項前置完全不同，綁成一張卡會互相拖住**：C6.1（多方混戰接線）與 C6.3（#48）今天就能開工；C6.4（係數校準）卡在 WP-D1，而 D1 查證後是**零程式碼**（`rg -l 'monte_carlo|batch_run' core ops` 零命中）。建議拆卡。
3. **SPEC 對 C6/C8 標的「golden：重錄」兩張都不完全準確**：`core/tests/replay/goldens/` 只有五份（empty_100 / mission_seize_60 / order_replay_60 / rng_walk_100 / suppression_defense_60），**沒有一份走聚合裁決路徑**——`core/app/adjudication/adjudicator.py:349-351` 已經查證並寫下這件事。C6.1 真正擋路的是 **RNG stream 汙染**（multiway 的抽樣次數隨同格單位數變動，共用 `rngs['adjudication']` 會擾亂所有既有交戰），不是 golden。C6.3（#48）則確實要重錄，因為它改的是 `Target` 形狀與 `cp_per_platform` 分母，會動到逐平台路徑的事件序列化。
4. **本 repo 的招牌病在 C6 範圍內有一個活體樣本值得後續 agent 引以為戒**：`core/app/adjudication/establishment.py` 的模組說明記錄了 `platform_count` 全系統沒有寫入端、預設值 1 變成唯一路徑、而**每一條交戰測試都自己手塞值所以全綠**。#48 要新增的「目標編成組成」欄位結構完全相同（契約→loader→熱狀態→裁決），極可能重演。做 #48 時第一件事應該是先寫一條「從真想定載入的單位，其組成不得落回預設值」的測試。
5. **契約 enum 比引擎窄**：`contracts/scenario.schema.json:55` 的 `aggregate_adjudication_level` 只允許 BATTALION/BRIGADE/DIVISION，但 `should_aggregate` 支援任意 UnitLevel（`core/tests/unit/test_unit_level_order.py:83` 斷言 COMPANY 門檻可用）。想定寫不出「連級以上就聚合」。同時四個出貨想定 `scenarios/examples/*/scenario.yaml` **全部宣告 BATTALION**，等於這條新接的線沒有任何活體資料在走——建議 C6.2 收尾時把 armor-breakthrough 之外的某個想定改成 BRIGADE 做一次活體驗證。
6. **C8 的前置比想像中齊全**：單位階層（`schema.prisma` TacticalUnit.parentId / subUnits）與 C7 帳目三卡都已就位，卡住 C8 的不是前置而是**規模與殭屍物件風險**（母/子單位同時存在於熱狀態、Redis、COP、AI context、precheck 五處）。另有一個 SPEC 沒講清楚、必須先裁示的點：自動解聚的「進入敵 contact X km」用真值還是偵測結果——C7.3 用真值有明確理由（限制自己），但自動解聚是給予能力，用真值等同白給敵情。
7. **未動任何檔案**，本次全程唯讀（rg / sed / grep / git log）。

### WP-D 前半 — WP-D1–D3 分析引擎（前半）

**三張卡全部真的沒開始，這次文件沒說謊。** SPEC_V2 總表第 18/19/20 項狀態欄空白、路線圖也把 D1–D3 列在 V2.2，與程式碼一致（`core/app/analysis/` 不存在，experiment/MOE/force_ratio/feba 等關鍵字在非 .md 檔全 repo 0 命中）。這與 C4/C7/E2/E4/F1/F3 那批「標 ✅ 但實際壞掉」的情況不同——D 群前半是誠實的空白。

**但 SPEC 的三個前提有兩個不成立，這比狀態欄過時更危險：**

1. **「決定性引擎已是完美地基」（D1 動機欄）——只對一半。** 決定性保證只在**合成 kernel** 上被證明過：`core/tests/replay/harness.py` 跑的是 `scenarios.py` 手工組的 Kernel（NoOp 子系統 + RngWalkMovement 示範移動），與生產路徑 `core/app/sim_runtime.py:508 SimManager._run_session`（約 530 行、硬綁 Redis/DB/牆鐘、一 session 一條 asyncio task）沒有任何交集。**「同想定同 seed 位元一致」從來沒有在生產路徑上被驗證過**，而 D1 的驗收條文正是這一條。D1 真正的工作量不在「批次」，在於把那 530 行接線抽成離線可重用的工廠，而且抽的過程中 golden 完全幫不上忙（它測的是另一個 kernel）。

2. **「purpose=ANALYSIS 的複製 session」（不變量 5）——那個欄位不存在。** `WargameSession` 沒有 `purpose` 欄（`rg -n purpose core/app/models/tables.py db/prisma/schema.prisma` 0 命中）。只有 `ExerciseSession.sessionRole` 有 `ANALYSIS` 值（enums.py:207 / schema.prisma:632），那是 WP-B1 演習專案的欄位，掛在關聯表上。D1 要嘛加欄要嘛複用它，但不能假設它已經在。

3. **「AAR 統計連命中率的帳都對不平」（D2 動機欄）——說得太客氣，實際是錯的而不是不全。** `adjudication/aggregate.py:99` 把 `damage_calc` 設成 `a_loss + b_loss`（雙方戰損相加）而 `target_id` 只指守方；`aar/stats.py:74-77` 把整包記到守方陣營頭上。**營級聚合是預設裁決路徑**（`aggregate_adjudication_level` 預設 None＝BATTALION），所以現行 AAR 的「各陣營承受戰損」在多數想定裡方向性地誇大守方損失。逐側數字 `initiator_loss`/`target_loss` 早就寫在事件的 `ai_decision` 裡，**從來沒有人讀**——與同一檔案 `_area_losses`（stats.py:33，讀了 AREA_FIRE 的 `losses_by_unit`）是同一個病的第二例。這半張卡（純改 `stats.py` 的讀法、一行事件不動、golden 不動）成本極低、價值立即，建議從 D 群裡優先切出來單獨做。

**一個好消息：D3 的地基比 SPEC 寫的還好。** SPEC 現況欄寫「單一單位 viewshed/射界有」是對的（`core/app/footprint.py` + `POST /sessions/{id}/terrain/footprint`），但它沒提到**動態偵測已經接活**——`SensorSweepSystem` 在 `sim_runtime.py:763` 取代了 NoOp，per-faction `IntelContact` 有真實資料在寫入。所以 D3 的「只用該陣營可見情報算戰力比」不必等任何前置卡，今天就有資料可算。（順帶更正一條可能存在的舊認知：sensors 已非 NoOp；仍是 NoOp 的只剩 logistics 那條？——`logistics=NoOpLogisticsSystem` 在 runtime 裡仍在，但 C7 的補給是掛 `pre_tick` 走 supply_wiring，不走 kernel 的 logistics 槽。）

**其他文件與現實不符之處：**
- `PROGRESS.md` 檔尾的「下一步建議（給下一個接手的 agent）」整段嚴重過時——還在說「M4 前端待做」「M6 AI Phase 1 需 vLLM 節點」「repo 無 remote/commit」，而上方的任務表已經跑到 WP-D6.1/E4/F3。接手的 agent 若照那段走會做錯方向。TASKS.md 的表格才是可信的。
- `clone_session` 只複製四張子表（TacticalUnit / EquipmentInstance / MapFeature / SessionParticipant）+ WargameSession 全欄。docstring 沒說的是 **FirePlan/FirePlanTarget/Message/Request/IntelContact 不複製**。session 欄位的 AST 守門測試（`test_clone_completeness.py`）現在是真的（commit 9b87275 修過恆真問題），但它只守欄位、不守子表——D1 從活局 clone 當批次基底時，預劃火力計畫會靜靜消失且不報錯。

**建議的實作順序（與 SPEC 編號一致但理由不同）：** D2 的對帳修正（S，可獨立，先修掉會騙人的數字）→ D2 其餘（MOE 計算器 + unit_cost）→ D1（吃 MOE 產分布）；D3 完全獨立、可與前兩者並行，且是三張裡使用者最快看得到差別的一張。

### WP-D 後半 — WP-D4–D7 分析引擎（後半）

## 整體觀察：文件與現實的落差在這一組是「雙向」的

**（一）D6.1 的 ✅ 是真的，而且是全 repo 少見的高品質收卡。** 端點、契約、前端、真 Playwright e2e 四層俱全，worklog 記載與程式碼逐條對得上，還順手修掉 5 個既有錯。這是本次盤點裡唯一一張「文件說做完、實際也做完」的卡。它甚至誠實標注了自己的界線（tick 0 基準位置是近似值）。**建議把 aar-map-replay.md 當成 worklog 的正面範本。**

**（二）D6.2 藏著一個比文件描述嚴重得多的第三型缺陷（DONE_BUT_BROKEN 的教科書案例）。**
SPEC 只寫「命中率**分母**語意修正」，但實際上壞的是**分子**：`aar/stats.py:60` 認 `ai_decision['hit']`，而全 repo 只有單發路徑（`engagement.py:176`）寫這個鍵。建制數 >1 的單位走齊射、持 ≥2 武器系統走聯合兵種，兩條路徑都只寫 `status: HIT/MISS`。**真實推演的絕大多數交戰不計入命中率**，畫面上（`aar.vue:108`）、AAR 講評裡（`narrative.py:57`）、封存記錄裡（`archive.py:171`）看到的都是嚴重偏低甚至為 0 的數字。
而 `test_aar.py:284` 是綠的——因為它用手寫的合成事件，事件形狀跟生產不一樣。**這與本週活體測試抓到的三個缺陷是同一個病灶：測試餵給函式的資料，不是引擎真的會產生的資料。** 建議把「AAR 統計測試必須用真裁決函式產生事件」寫進 HOW_TO §3。
同一支檔案裡，D6.1 明明修好了聚合戰損雙側入帳（`replay.py:124` 有詳細註解），`stats.py:64` 卻原封不動——**同一個 bug 在兩個模組，只修了一個**。`README.md:485` 一直誠實記著這件事，只是沒人動。這張卡是 S size、零 golden 風險、有可見的錯誤數字，**應該排最前面**。

**（三）D4/D5/D7 三張「未做」的標記是準確的，且規格對前置條件的描述罕見地可靠。**
- D4 說「E1 已備」——查證屬實：`sim_runtime.py:815` 真的傳了 checkpointer，rollback 三個缺口（座標/彈藥油料/地圖物件）本週已補齊（commit 63abe43、BL-4）。**回滾現在確實能重建狀態。** 但要注意 ADR 007 自陳的界線：RNG 只還原到快照當下。
- D5 說「已有 ETA 與消耗率資料，缺彙整層」——屬實。`precheck.py:916` 甚至已經算出 `eta_ticks`，只是**拿去拼了一句 debug 字串就丟掉**。這是典型的「值算出來了但沒有消費者」，補起來很快。
- D7 說「規則式、不用 LLM」——現況是連 severity 這個概念都不存在（`useCopFeed.ts` 是純字串對照表）。但 `broadcaster.py:37 event_audience` 的 per-faction 受眾機制是現成的，不用從零長。

**（四）三個跨卡的共同陷阱，請後續 agent 注意：**
1. **golden 其實比想像中安全**。`core/tests/replay/harness.py:52` 的 golden 只 hash `hot_state.get_all()` 的 units 子樹，**不含 ledger**。D6.2/D6.3/D7 都是純讀或純新增事件，零重錄。真正會踩到的是「把新狀態塞進 hot_state」——D7 的讀/未讀狀態尤其不能放那裡。
2. **迷霧漏洞的慣犯是「名冊/靜態資料」而非事件**。D6.1 事件投影做對了、名冊漏了（`api/aar.py:143` 的註解記著這次教訓）。D6.3 新增 units/shots 表、D5 的 sustainment、D7 的 contact_new 都會重蹈覆轍。
3. **單位換算寫死必爆**。本週剛修過「壓制/工事寫死 1 tick=1 分鐘」（commit d67fe61），而 `tick_rate_ms` 是想定可調的。D5 的「續戰力 N 小時」、「抵達時刻」是完全同一類換算。

**建議順序**：D6.2（S，立即，修錯數字）→ D6.3（M，與 D6.1 的分頁欠帳一起）→ D5（L，價值最高且原料齊全）→ D7（L，依賴 D5 才完整）→ D2（未在本組範圍，但 D4 的實質前置）→ D4（XL）。

### WP-E/F — WP-E/F 韌性與 AI 深化

整體觀察（文件 vs 現實）：

1. **F 群的狀態標示是全 SPEC_V2 裡最誠實的一段**——F1 標「🟡 最小切片」、F4 明寫「門檻擋在前」，兩者查證後都屬實。反倒是**未竟清單低估了缺口**：F1 的四項未竟沒提到 (a) `describe_embedder` 的 degraded 旗標沒有任何 API/前端消費端（SPEC 說要「UI 標示」，前端零命中），(b) `evaluate_retrieval` 零生產呼叫端、也沒有任何 QA 對資料，(c) `--dim` 預設 64 vs bge-m3 的 1024 這條**只在真的載模型時才會炸**的執行期陷阱（所有測試都走 HashEmbedder，永遠測不到）。

2. **最嚴重的一條：CI 的 AI eval gate 結構上不可能變紅**。`ai/matso_ai/evals/run.py` 的 `main()` 沒有注入 responder 的參數，永遠用 `FallbackResponder`；而該 responder 的 orders 與 cited_documents 恆空 → IHL 違規率與捏造引用率恆 0。這條 gate 從 O6.6 起就在 ci.yml 裡綠著，量的其實是 jsonschema 套件。同時 case schema 宣告的 `golden_citations` / `require_uncertainty` / `citations_must_exist` 三個斷言欄位**零程式消費端**——案例作者以為自己在斷言的東西，runner 從來沒讀過。這正是本 repo 招牌病的第 N 例（「存得進去、讀得回來、測試全綠、實際沒效果」），而且是在**品質量尺本身**上發病，比壓制/彈藥那三例更危險：壞掉的量尺會讓所有其他卡的「已驗證」都打折。

3. **F5 的依賴描述半真**。路線圖寫「F5 依賴 B5/C10 事件鏈 + D6 重播」，D6.1 重播確實可用；但 **B5/C10 的「事件鏈」不在 Ledger 裡**——申請/核覆/下令的時戳只存在於 `Request.requestedAtTick/decidedAtTick` 與 `Order.issuedAtTick/resolvedAtTick` 兩張關聯表，Ledger 的 44 種 event_type 裡一個下令或審批事件都沒有（只有 `ORDER_RESTRICTED_FIRE_OVERRIDE`）。SPEC_V2 F5 條文寫的「評量引擎（純函數，讀 Ledger）」因此是一個**不成立的既有假設**，而且 `ops/tools/cpx_acceptance.py:745` 的 s7 自己就把這件事寫出來了（「它們不在同一個資料來源」）——文件之間已經互相打臉，只是沒人把它回寫進 SPEC。這個取捨（補 Ledger 事件並重錄 golden vs 讓評量引擎跨源讀 DB）應該由使用者裁示，不該由接手的 agent 順手選一個。

4. **另有兩個「延後到某卡一併實作、而那張卡已結案」的漏接**：contracts/roe.schema.json 明寫戰鬥地境（不得越過某線）「延後至 WP-C10/B5 一併實作」，但 C10 五張、B5 三張都已標 ✅ 結案，boundary 判定完全不存在；`deadline_tick` 同樣只活在 SPEC_V2 裡。F5 的 `compliance` 量測型別因此只做得出 FRATRICIDE 與 RESTRICTED_FIRE override 兩種來源。

5. **E5 是唯一文件沒誇大的一張**（總表第 29 項的描述精準）。但查證中順手發現 E4 的同一個病又犯了一次：`TickPacer.slowdown` 的 docstring 寫「觀測用（metrics）」，而 `rg` 顯示**沒有任何寫入端**——降頻正在發生這件事，在儀表板上完全看不見。這與 SPEC_V2 §WP-E4 自承的 `matso_io_latency_ms` / `matso_ai_workers` 「定義了但沒有寫入端」是同一類，建議做 E5 時一併收掉。

6. **排序建議**：F2 的 (a)(b)（把 responder 注入與三個斷言補上）應該最先做且獨立於語料——它成本 S~M、能立刻讓量尺有判定力，其他所有 AI 側的卡都靠它。F1 的 (1)(4)（dim 修正 + 系統資訊頁顯示降級）是 S。F5 是 XL 但使用者價值最高，前提是先把資料源的取捨問清楚。F4 應保持關閉並把「三條門檻現況」寫回 SPEC，避免下一個 agent 誤以為只差一點。

### WP-G — WP-G 前端工程健全化

**一、SPEC_V2 的 WP-G 表格在我這三張卡上其實是誠實的**——G2/G5/G6 都沒有 ✅，也都確實沒做。不可信的是 G1a/G1b 那兩張標 ✅ 的（不在我範圍，未查證）。但**驗收條件本身有一條是壞的**：G5 寫「`rg \"interface.*View\" app/` 無契約外重複定義」，這條指令現在只抓到 2 筆（`platform/app/pages/session/[id]/autonomy.vue:16,20`），幾乎已經「綠」了——因為 repo 的手寫型別大多不叫 `*View`（叫 `AarStats`/`OwnUnit`/`Contact`/`EditorUnit`/`ScenarioModel`）。**照這條驗收做，改兩個 interface 名字就能宣告 G5 完成，而 9 個檔、40+ 個手寫 API 型別、19 條契約漂移一條都沒動。** 這正是本 repo 招牌病的上游成因：驗收條件是機械的，但機械得不對地方。建議接手 agent 直接把驗收條件改掉，別照抄。

**二、G2 的 SPEC 描述不精確。** SPEC 寫「main.css 未接線（盤點）」，實情是**半接線**：`platform/app/assets/css/main.css` 確實沒進 `nuxt.config.ts:42` 的 `css[]`，但 `@tailwindcss/vite` plugin 在 `nuxt.config.ts:2,62` 是**真的掛著在跑**的。所以現況比「假象相依」更糟一點——每次 `nuxt build` 都在載入 oxide 原生二進位、做一輪掃描、然後產出零位元組的 CSS。移除的影響經逐條核對是**零**：全 `app/` 只有 13 個看起來像 utility 的 class，全部是自訂 scoped class（`mobility-block`/`msel-block`/`iaf-block`/`.hidden`）。這是三張卡裡唯一可以放心速戰速決的。

**三、G5 的真正工作量遠大於 SPEC 表格那一格文字。** SPEC 只提了 useAar/autonomy/system-settings/scenarios 四處，但我查到的缺口是三層：
- 12 條端點**實作有、契約沒有**（`core/tests/unit/test_contract_conformance.py` 的 `_IMPL_ONLY`，我實跑 `uv run pytest` 4 passed 確認清單是準的）；
- 7 條**契約有、實作沒有**的規格殘骸（`_CONTRACT_ONLY`，含躺很久沒實作的 `/sessions/{id}/ledger`）；
- **另有 22 個 operation 在契約裡但沒有 2xx response schema**——這一層 `test_contract_conformance.py` 完全看不到，因為它只比對路徑不比對 schema。openapi-typescript 對這些只會生 `unknown`，於是「路徑在契約裡」給了假的安全感。`pages/scenarios.vue` 手寫 `ScenarioItem[]` 就是因為 `GET /scenarios` 只有 `{ responses: { \"200\": { description: Scenarios } } }`。**建議 G5 順手把 `test_contract_conformance.py` 擴一條「2xx 必須有 content schema」的閘門**，否則補完 12 條端點之後同一個洞會從另一邊繼續漏。

**四、最尖銳的單一發現：`AarReplayStates` 有兩份。** `contracts/core_api.yaml:2492` 定義了它、`platform/app/types/api.ts:1757` 已經生成好了，但 `platform/app/composables/useAar.ts:34` 又手寫一份，並在 `:104` 用 `apiFetch<AarReplayStates>` 綁自己那份。契約先行的紀律在這裡**已經做到一半然後前端沒接**——四層一致性（契約/後端/前端/引擎）裡典型的「前端沒接」。這是 G5 最好的起手點，因為契約側零成本。

**五、condition DSL 的「單一來源」是不存在的，而且文件讓人以為存在。** `README.md:1128` 寫「契約先行：型別來自生成的 types/api.ts；`useConditionDsl.ts` 註明變更前先改後端與 contracts/msel.schema.json」——聽起來像有機制。實際上 `contracts/msel.schema.json` 的 `trigger` 欄位只有 `{\"type\":\"object\",\"required\":[\"type\"]}` 加一句 description 列舉六種 type 名，**沒有任何一種 condition 的欄位被 schema 約束過**。真正的定義活在三個地方：`core/app/scenario/triggers.py`、`core/app/ai_loop/victory.py`、`platform/app/composables/useConditionDsl.ts`（12 個手寫 type）。靠人肉紀律同步，零閘門。這是 G5 裡最有實質風險的一項（想定檔驗證不到的欄位＝白軍寫錯 MSEL 觸發條件時不會報錯，只是不會觸發）。

**六、G6 的規模我做了實測而不是估計**：`platform/app/`（扣 `types/api.ts`）非註解的含中文行 1901 行，其中樣板文字節點 526、UI 屬性 115、JS 字串字面量 747。前 12 個檔占一半以上。e2e 耦合比預期輕（只 55 條文字斷言，其餘全走 `data-testid`），air-gapped 也不是阻礙（`platform/Dockerfile:6-7` 走 `npm ci`，node_modules 不入版控）。真正的陷阱是 `@nuxtjs/i18n` 的 `strategy` 預設會加語言路由前綴，會一次打爛所有 e2e 的 `page.goto`。

**七、優先級建議**：G2（S，收益是誠實與建置成本，可立即做）→ G5（L，是唯一一張能防止未來回歸的卡，且與本 repo 反覆發生的「欄位漂移前端靜默壞掉」直接對應）→ G6（M 骨架 / 存量遷移另計，在沒有明確多語需求前使用者價值為零）。三張卡都與 golden 無關，都不碰紅線 1/2/3；G5 直接落在紅線 4（契約先行）的管轄範圍，順序做反本身就違規。

### WP-H — WP-H 互通與多站

## 一句話結論
WP-H 四張卡**全數未開工**，程式碼與文件在「未開工」這件事上一致——這一組沒有本 repo 典型的「存得進去、沒效果」病。但 **SPEC 對 H1 地基的三句描述有兩句經不起查證**，若照字面實作會做出一個洩漏迷霧的 Relay。

## 文件與現實不符（按嚴重度）

1. **【嚴重】SPEC 行 1176「golden replay 證明了『事件流重放＝狀態重建』」——不成立。**
   `core/tests/replay/harness.py` 的 `run_replay()` 是「相同 (master_seed, 想定) **從 tick 0 重跑 Kernel** → 比對 `compute_state_hash`」，證明的是**重新模擬的確定性**，全程不碰任何 WS envelope。「套用 envelope 流可重建熱狀態」在本 repo **零測試覆蓋**。H1 的第一步應該是補這條證明，而不是假設它已經有了。

2. **【嚴重】SPEC 行 1178–1179「Relay 收加密全流但對 client 仍按 faction 過濾」——照字面做會壞。**
   每陣營投影是在 **broadcaster（生產端）** 做的，不是 WS 層：`RedisBroadcaster._envelopes()` 每 tick 發 N+1 份信封（每陣營一份 `factions:[F], exclusive:true` 的已投影副本 + 一份 `factions:[]` 真實副本）。而 `public_diff()` 用 `_INTERNAL_FIELDS` 把 `REPORT_LAT/LNG/TICK_KEY` 剝掉了——那三個正是位置凍結（`project_position`）的輸入。所以：
   - Relay 若拿真實副本自行重投影 → **永不凍結位置**，遠端站看得到斷聯敵軍的即時真實座標（正是 [MASA-MS] 差距表警告的那件事）。
   - Relay 若想拿已投影副本 → 現有 WS 端點給不了它：`faction_filter._faction_visible` 的 `return omniscient and not envelope.get("exclusive", False)` 讓全知身分**收不到** `exclusive:true` 的每陣營副本。
   **正確設計是「轉發主站已投影的副本」，不是「在 Relay 重投影」。** 這一句必須寫進 ADR，否則後續 agent 一定會照 SPEC 字面走錯。

3. **【中】SPEC 行 1176 說 ADR-007 ——編號已被 `docs/adr/007-rollback-logical-truncation.md` 占用。** H1 要開的是 **ADR-008**，H2 是 009。

4. **【中】專案記憶檔 `live-runtime-subsystems.md`「sim_runtime 只接 movement+engagement；sensors/comms/logistics 仍 NoOp」已過時。** 查 `core/app/sim_runtime.py:747/762/786/797/803`：movement / sensors(#97) / comms(#33) / logistics(#85) / MSEL(WP-B2) **五個全部接上了**。這直接影響 H4 的判斷（SPEC 那句「災防 CPX 用得到的四個子系統」確實都活著）。建議請使用者更新該記憶。

5. **【小，但是紅線 4 的既有違反】`POST /sessions/{id}/units/{uid}/reposition` 不在 `contracts/core_api.yaml`**（`rg reposition contracts/` exit 1），靠 `test_contract_conformance.py:72` 的 `_IMPL_ONLY` 白名單放行。H3 正好順手清掉。

## 對排程的建議

- **H2（純 ADR，M）風險最低、最該先做**——零程式碼、零 golden、零紅線，且產出對「能不能跟現役系統掛上」這個採購級問題直接有用。唯一要小心的是別順手把「scenario 絕對時間錨點」做進去（`SimTime` 只有相對 `sim_time_ms`，`daylight_wiring` 只有 `start_min` 沒有日期）——那是另一張卡。
- **H3（M）是四張裡唯一有使用者可見產出的**，而且地基已在（reposition + `live_position` 命令通道都是生產路徑），順帶清掉一筆契約債與前端「多選移動 = N 次 HTTP」。
- **H1（L，若要 Relay 提供完整唯讀 REST 則 XL）** 前置其實都齊了（E3 `/state` ✅、WP-C5 每陣營投影 ✅、E1 checkpoint ✅），沒有硬阻塞；真正的工作量在「全流訂閱傳輸 + relay 模式旗標 + envelope→狀態重建器 + 那條缺失的正確性證明」。**必踩的坑是 `SimManager` 會自動掃 DB 為每個未封存 session 起 Kernel（`sim_runtime.py:381`，`main.py:60-64` 無條件啟動）**——relay 實例忘了關就是第二個權威模擬器。
- **H4 在 V2 範圍內其實已經「做完」**（SPEC 那段文字就是交付物），V2.2 路線圖也沒列它。實際工作是新開 SPEC_CIVIL 與整個民事域，XL 體量，不該當成一張卡排。


### 複查 ✅ — 回頭複查已標 ✅ 的高風險項

整體觀察（文件與現實不符之處，按嚴重度排）：

1. **C7 是本次盤點最有價值的發現，也是「✅ 不等於能用」的極端案例。** 三張子卡（C7.1/C7.2/C7.3）全標 ✅、單元測試齊備（test_supply.py / test_supply_points.py / test_refit.py）、還做過 mutation check，但整條鏈在任何真實的一局裡**一次都執行不到**：`supply` 熱狀態鍵沒有任何生產寫入端（seed_combat_state 不播、想定不能宣告、MSEL MODIFY_UNIT 只認 strength/lat/lng）、`SUPPLY_POINT` 沒有任何建立路徑（不在前端 kind 清單、不在契約、不在想定 schema，只有測試建得出來）、`repair_per_day` 與 `DAILY_CONSUMPTION` 都預設 0。**測試之所以全綠，正是因為它們自己 put_unit 了 supply**——測試繞過了真正缺的那一層。SPEC 的三個「未竟」註記各自看起來都像小事（「想定不能宣告初值」「補給點不能從想定/UI 建立」「前端不顯示水位」），合起來卻等於整張卡不存在。

2. **WP-C1 的時間尺度 bug 還有一個同型未修的兄弟。** commit d67fe61 修了 `adjudication/suppression.py`（壓制衰減 + 掘壕工時改以分鐘計、依 tick_rate_ms 換算），但 `adjudication/obstacles.py` 的 `breach_time_ticks`（雷區 45 / 斷橋 120）**完全沒有 tick_rate_ms 參數**，連 `drain_engineer_orders` 的簽名裡都沒有。找同型 bug 的通用招式：`rg "1 tick" core/app` 找註解裡宣稱單位的常數，再看它的消費端有沒有拿到 tick_rate_ms。

3. **A1 被 A2 打穿了一個洞。** A1（2026-07-29）把 AI 敵情接上迷霧投影並留了退回開關；A2（2026-07-31）新增的 `LiveMissionPlanner._world_view` 卻直接 import `ground_truth_enemies`，而且不受 `ai_ground_truth` 開關管。更值得注意的是 `decomposer.py` 的模組說明**宣稱**自己拿到的是投影過的 world_view——這種「註解與行為不一致」在 A2 的 worklog 裡被點名過（UnitTargetLocator 那條），結果同一個 session 裡又犯了一次。A2 加的 import 白名單測試守的是分解器自己，守不到呼叫端餵什麼。

4. **SPEC_V2 的「未竟」清單有雙向失真，不能當工作清單用。**
   - 樂觀失真（宣稱做完但沒接）：見上面 1、3。
   - 悲觀失真（宣稱沒做但其實做完了）：C2 的「前端 ENGINEER/FORMATION 下令 UI 未做」「blocks_road 未接路由」「地圖編輯器不能選 obstacle_type」——三項全都做完了；E4 的「io_latency / ai_workers 定義了但沒有寫入端」——兩個都接上了；C4c 的「purge_expired_smoke 未掛 pre_tick」「drift() 未掛進查詢路徑」——都掛上了。總表第 10 項那句「前端下令 UI 未做」也是同一類。
   換句話說：**SPEC 的 ✅ 不可信，SPEC 的 ❌/未竟同樣不可信**；兩邊都要 rg 驗。

5. **「中性預設關掉」是這個 repo 的系統性設計選擇，也是系統性風險。** C7（五層全 0）、C4b（weather_refresh_ticks=0 且想定改不了、只有全域 UI 開得動）、C4a（四個示範想定只有一個宣告 sunrise/sunset）、C2（typed() 濾掉沒有 obstacle_type 的標註，而沒有想定宣告過障礙）、C10.5（survivability_move 未宣告即停用）。這個紀律本身是對的（保護既有局與 golden），但它有個沒被寫下來的副作用：**出貨狀態下這些子系統全部不存在，而且沒有任何訊號告訴使用者**。建議加一條收工檢查：每個標 ✅ 的保真子系統，至少要有一個示範想定把它打開，否則等於沒有活體驗證過。

6. **查得沒問題的五張**：A3（禁射區四層全通，兩個資料源都活的）、B2（MSEL 三個斷點都補上、白軍 fire/skip 閉環完整）、C5（comms 產出→投影→API→前端→AI 五層一致，mesh_states 真的傳了地形遮蔽與天氣衰減）、C10（五條子路徑都有生產呼叫端，且有 18 發實彈的活體校準紀錄）、E3（/state 複用既有 handler 而非重寫過濾，last_seq 的 race 處理正確）。E1 與 E4 也沒有阻斷性缺口，各只有一個調校/整潔問題。

7. **一個給後續 agent 的操作提醒**：這個 repo 有大量識別字含 `-r` 會誤觸 rg 的 `--replace`（例如 `rg -rn "ENGINEER"` 會把輸出裡的 ENGINEER 換成 n，看起來像「這個字串不存在」）。我在 C2 與 F3 兩處差點據此下錯結論。用 `rg -n` 不要用 `rg -rn`。
