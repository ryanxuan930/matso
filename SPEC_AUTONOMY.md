# MATSO — 系統擴充規格（SPEC_AUTONOMY）
# 自主推演：AI 控制各陣營自動對抗（Autonomous Faction-vs-Faction Simulation）

> 本文件擴充 [SPEC_FULL.md](SPEC_FULL.md) §9（AI 決策）與 §10（護欄），為「給定想定 + 各陣營目標，交由 AI 自主控制各陣營、自動推演至分出勝負」的權威設計。
> 語言/關鍵字慣例同 SPEC_FULL：正體中文敘述、程式識別字/API 欄位一律英文；MUST/SHOULD/MAY 依 RFC 2119。
> 對應任務板：**新里程碑 O11（O11.1–O11.8）**，見 §10。工程規範見 [HOW_TO.md](HOW_TO.md)。

---

## 0. 背景與問題陳述

目前系統可讓**人**在活執行期（O10.1 sim runtime）下令（MOVE/ENGAGE），由確定性引擎裁決、STATE_DIFF 廣播、AAR 記錄。AI 子系統（角色、護欄、決策迴路、RAG）**已建置且測試綠**，但**尚未接入活 Kernel**：

- `sim_runtime._run_session` 沒有 AI 決策 worker；`TriggerChecker` 為 NoOp。
- `run_opfor_turn`（[core/app/ai_loop/opfor.py](core/app/ai_loop/opfor.py)）依賴注入的 `OpforDecider` 抽象，但**無人在活期把真 decider（RoleManager + Ollama client）接上**。
- AI 產出的 `orders` 為 dict 清單「待落為 pending」——**沒有橋接到 `OrderService.submit()`** 使其成為 sim 會執行的 VALIDATED 指令。
- 場景已有 `victory_conditions` 欄與 `core/app/scenario/triggers.py`（條件 DSL 骨架），但**未在活 Kernel 每 tick 評估、自動判勝負收場**。

**目標界定**：使用者給定 (a) 初始想定（劇本，含各陣營編制、關係矩陣、地形/天氣）與 (b) 各陣營目標/勝負條件，選擇「自主推演」→ 系統為**每個 AI 陣營**起一條決策迴路，週期性讀取該陣營視角的 COP、由 LLM 產令、過護欄、落單、由確定性引擎執行；每週期評估勝負；分出勝負或到時限即自動收場並產 AAR。使用者全程觀戰，不需下令。

**首版範圍（本 SPEC 主線，依使用者拍板）**：
- **N 陣營自主對抗**（≥2 陣營皆 AI 控制；示範用雙陣營，但**架構 MUST 支援多陣營**——每陣營一條 decision worker，敵我由 `FactionRelations` 矩陣界定）。單一 LLM 模型以**角色/人格切換**服務所有陣營。
- **固定心跳**決策節奏（每 N 模擬秒一次），先不做事件觸發。
- 人對 AI（human-vs-AI 混合）為**第二階段**；事件觸發器為**第三階段**（見 §9）。

**與既有任務板的關係**：本里程碑 O11 **落實並延伸** TASKS.md 既有的 [O10.3](TASKS.md)（AI 迴路↔kernel 接活）與 [O10.4](TASKS.md)（victory 判勝負）兩張部署接線卡——把「事件驅動 OPFOR」泛化為「**多陣營固定心跳自主對抗**」，並補齊 context builder / 指令橋接 / 前端主控台。O11 完成即視為 O10.3 + O10.4 之 AI/victory 部分達交。

---

## 1. 設計原則與紅線（沿用 SPEC_FULL，不可違反）

1. **AI 永不裁決物理**：命中/毀傷/射程/可達/可見仍是 `core/app/adjudication/` 純同步純函數。AI（LLM）**僅得決定戰術意圖（MOVE 去哪、ENGAGE 誰、fire_policy）**，一律經確定性引擎執行。
2. **AI 不寫熱狀態（single-writer）**：AI worker **只產生指令**，經 `OrderService.submit()` 成 VALIDATED，由 **Kernel（唯一寫者）** 於自己的 tick 執行。AI 絕不觸碰 hot state（呼應 #52 教訓：外部寫 Redis 會被 sim 鏡像快取忽略）。
3. **護欄無 bypass（紅線 3）**：每一張 AI 指令 MUST 過 Guardrail Gateway G1–G6。Gateway 沒有跳過參數；嚴格度只由 profile 調。
4. **霧化只在後端**：AI 拿到的 COP context MUST 已 faction-filtered（見 §3.2）；AI 不得看到該陣營未偵測到的敵情（防全知作弊）。
5. **決定性可重播**：LLM 非決定性，但 `ReplayClient`（[ai/matso_ai/inference/client.py](ai/matso_ai/inference/client.py)）以 prompt 雜湊重播已錄回應。自主場次的 golden/CI 走 ReplayClient；**現有 golden replay（6 綠）不得因本功能改變**（AI 路徑條件化啟用，見 §6）。
6. **契約先行**：改 `contracts/` → 驗證 → 再實作；DB 變更只走 `prisma migrate`。
7. **一次一張卡**：O11.1→O11.8 循序；範圍外問題進 PROGRESS.md Backlog。

---

## 2. 架構盤點：已有 vs 缺口（本規格的核心價值）

> 動工前先認清：**AI 大腦已建好，缺的是把它接到活體上的神經與骨架。** 多數工作是「接線 + 泛化 + 綁定」，而非「從零建 AI」。

### 2.1 已存在（可直接複用，勿重造）

| 元件 | 位置 | 用途 |
|---|---|---|
| **OpenAI 相容 client** | `ai/matso_ai/inference/client.py` `OpenAICompatibleClient` | POST `{base}/v1/chat/completions`（接 Ollama） |
| **決定性重播** | 同上 `ReplayClient` / `RecordingClient` | 按 prompt 雜湊重播；錄一次供 CI/golden（**§6 決定性方案已現成**） |
| **RoleManager** | `ai/matso_ai/inference/role_manager.py` | 角色分組批次、priority、adapter 熱切換攤銷、AIInvocationLog；**單模型角色切換已內建**（無 LoRA → 全角色 override 單一 adapter，切換成本歸零） |
| **角色註冊 + prompt** | `ai/matso_ai/roles.py`、`prompts.py` | RoleConfig（system_prompt / output_schema_ref）、`build_system_prompt(role, mode)` 模式感知引用條款 |
| **決策迴路** | `core/app/ai_loop/opfor.py` `run_opfor_turn` | decide → G1–G6 → 重試≤2 → doctrine fallback（空令）；回 `AiTurnResult(orders, findings)` |
| **護欄 G1–G6** | `core/app/guardrails/gateway.py` | G1 schema · G2 CoT · G3 物理可行性（注入 `OrderFeasibilityChecker`）· G4 IHL/ROE（no-strike → 硬阻擋+升白軍）· G5 引用（模式感知）· G6 量化加嚴 |
| **AI 模式閘門** | `core/app/guardrails/modes.py` | `resolve_ai_mode` / `require_ai_enabled`（AI_OFF 拒啟動） |
| **調用日誌** | `ai/matso_ai/inference/invocation_log.py` | 每次 LLM 呼叫入 AIInvocationLog（可追溯、供錄放） |
| **落單 pipeline** | `core/app/orders/service.py` `OrderService.submit()` | validate → precheck → PENDING→VALIDATED；**AI 指令的落地入口** |
| **物理預檢** | `core/app/orders/precheck.py` `run_precheck` | MOVE 可達 / ENGAGE LOS·射程·彈藥（含 #49 聯合兵種）；**可包成 G3 的 `OrderFeasibilityChecker`** |
| **條件 DSL 骨架** | `core/app/scenario/triggers.py` | O7.2 觸發/條件求值（與 victory / MSEL 共用） |
| **場景 victory 欄** | `contracts/scenario.schema.json` `victory_conditions[]` | per-faction `{faction, condition}` |
| **陣營關係矩陣** | `core/app/factions.py` `FactionRelations` | HOSTILE/FRIENDLY/NEUTRAL（決定誰可交戰、誰的情報共享） |
| **AI 模式存 DB** | `SystemConfiguration.integration_config["ai"]`（#54） | base_url / model / mode（本功能讀此設定接 Ollama） |

### 2.2 缺口（本 SPEC 要補）

1. **Faction COP context builder**——把某陣營視角的活狀態（己方單位/彈藥/位置、已偵測敵情、地形/目標、近期事件、任務文字）壓成 `run_faction_turn` 的 `context` dict。**不存在**（§3.2）。
2. **陣營泛化**——現況 OPFOR-only（role `OPFOR_COMMANDER` → `opfor_decision`）。雙 AI 需**每陣營一個指揮官人格**皆產出「orders」schema（藍軍現用 `coa_recommendation` 只出 COA 非直接令）。以「單一 commander 角色 + 場景注入的陣營人格」服務 N 陣營，維持單模型零切換（§4）。
3. **具體 decider 膠合**——把 RoleManager + OpenAICompatibleClient + `build_system_prompt` 包成實作 `OpforDecider` 協定的 `LlmFactionDecider`，讀 #54 的 Ollama 設定（§4.2）。含**部署現實**：core 容器目前無 `httpx` 亦未裝 `matso_ai`（§8.3）。
4. **指令橋接（orders bridge）**——把 `AiTurnResult.orders`（tactical_order dict）映成 `OrderRequest`，以 AI 陣營指揮官為 issuer 呼叫 `OrderService.submit()` 落成 VALIDATED（§3.4）。
5. **G3 feasibility 接線**——把 `run_precheck` 包成 `OrderFeasibilityChecker` 注入護欄 G3（原註「O6.5 注入」，本卡補上）（§3.3）。
6. **Kernel 決策排程器**——`sim_runtime` 為每個 AI 陣營起**獨立 async decision worker**（固定心跳，**非 pre_tick**，避免 LLM 15s 阻塞 tick）（§3.1）。
7. **勝負引擎綁定**——每決策週期以 `triggers.py` DSL 評估 `victory_conditions` → 判勝負/時限 → 自動停 session → 產 AAR（§5）。
8. **前端自主主控台**——設定（陣營指派 AI/人格/目標/AI 模式）+ 觀戰（COP + 事件流 + AI 推理軌跡 + 護欄判決 + 目標進度）+ 結果（勝負橫幅 + AAR）（§7 P-G）。

---

## 3. 自主決策迴路（核心設計）

### 3.1 時序解耦（載入軸的第二定調）

LLM 一次補全 ≈ 15s（實測 gemma 12b），Kernel tick = 1s。**AI 決策 MUST 不在 tick 路徑（pre_tick）上**——否則卡死模擬。

```
Kernel tick 迴圈（1s）           每陣營 decision worker（async，獨立）
──────────────────────         ──────────────────────────────────
drain VALIDATED 指令  ←────┐    while running:
執行裁決（確定性）          │      snapshot ← faction COP（唯讀拷貝，瞬時）
STATE_DIFF 廣播            │      decision ← run_faction_turn(snapshot)   # ~15s LLM
更新 SimClock              └────  submit VALIDATED orders（經 OrderService）
（不被 LLM 拖慢）                 await 下次心跳（模擬秒對齊）
```

- worker 讀 COP 時只取**瞬時快照拷貝**、不持鎖跑 LLM。
- LLM 慢/失敗/逾時 → doctrine fallback（空令＝HOLD），worker 續跑下一週期，不阻塞 tick。
- 每陣營一條 worker；單一 LLM 模型序列化服務（RoleManager 佇列），但各 worker 的落單非同步到位。
- **心跳節奏**：以模擬時間定義（每 `decision_interval_s` 模擬秒），非牆鐘——保決定性、與 SimClock 對齊。首版固定值（場景可設）。

### 3.2 Faction COP context builder（O11.1）

新 `core/app/ai_loop/context.py`：`build_faction_context(hot, faction, scenario, relations, recent_events) -> dict`。

- **己方**：本陣營每單位 id / 型別 / 位置(h3) / 狀態 / `ammo_by_weapon` / strength。
- **敵情**：**僅**本陣營已偵測到的敵方單位（複用 `stream/faction_filter.py` 的霧化語意；未偵測者不得入 context）。
- **態勢**：地形/目標區（objective hexes）、關係矩陣（誰 HOSTILE）、近期關鍵事件摘要（交戰/被擊/移動完成）。
- **任務**：本陣營目標文字（來自場景，供指揮官人格對齊意圖）。
- 輸出為**緊湊、可序列化**的 dict → `build_system_prompt` 的 user prompt。純讀、零寫、可單元測。

**紅線檢核**：A 陣營 context 不得含 B 陣營未偵測單位（測試斷言）。

### 3.3 護欄 G3 feasibility 接線（O11.3）

包 `run_precheck` 成 `OrderFeasibilityChecker`：`is_feasible(order) -> (bool, reason)`。注入 `run_faction_turn` 的 gateway，使 G3 逐條剔除物理不可行的 AI 令（MOVE 不可達 / ENGAGE 無 LOS·超射程·無彈）。**這是「AI 不繞過物理」的護欄側落實**——即使 LLM 幻想一個打不到的目標，G3 也會剔除。

### 3.4 指令橋接：AiTurnResult → VALIDATED（O11.3）

`AiTurnResult.orders`（tactical_order dict：`unit_id / order_type / target_h3 / weapon_template_id? / fire_policy?`）→ 映成 `OrderRequest` → `OrderService.submit(session_id, req, issuer_id=<AI 陣營指揮官 principal>)`。

- 走**與人類完全相同**的入口：再過一次 validate + precheck（雙保險），落成 VALIDATED（不可行則 REJECTED，記事件）。
- issuer 為每陣營一個系統 principal（如 `ai:blue-commander`），供 AAR/稽核追溯是 AI 下的令。
- 落單後 Kernel 照常 drain 執行——**裁決路徑零改動**。

### 3.5 護欄干預 → 事件

G1–G6 攔截的 finding 經 `intervention_events()` 轉 `GUARDRAIL_INTERVENTION` Ledger 事件（已存在），前端觀戰面板可顯示「AI 想做 X，被護欄 Gn 擋下，原因 …」。

---

## 4. 陣營泛化與單模型角色切換（O11.2）

### 4.1 一個指揮官角色服務 N 陣營

現況 `OPFOR_COMMANDER` 綁紅軍。首版**不新增每陣營 LoRA**（只有一顆 Ollama）。做法：

- 保留單一「commander」角色機制與 `opfor_decision` 輸出 schema（泛用：`intent / orders[] / ihl_self_check`，非紅軍專屬）。
- **陣營差異由場景注入**：`build_faction_context` 帶入該陣營名稱、準則傾向、目標，附進 system/user prompt，使同一模型以不同**人格**分別為藍/紅產令。
- `run_opfor_turn` → 泛化命名 `run_faction_turn(faction, ...)`（保留舊名為 thin alias，避免動到既有測試）。
- adapter 維持單一（Ollama 單模型）→ RoleManager 切換成本 0；雙陣營請求由佇列序列化。

### 4.2 具體 decider（`LlmFactionDecider`）

實作 `OpforDecider` 協定：`decide(context, *, feedback) -> dict`：

1. 讀 #54 設定（`SystemConfiguration.integration_config["ai"]`：base_url / model / mode）。
2. 以 `build_system_prompt(commander_role, mode)` + `build_faction_context(...)` 組 messages。
3. 經 RoleManager → `OpenAICompatibleClient.complete()` 打 Ollama；`feedback`（上輪護欄回饋）附入重試 prompt。
4. 解析 LLM 回傳 JSON 文字 → dict（解析失敗交由 G1 schema 擋下 → 重試）。

模式：AI_OFF → worker 不啟動（`require_ai_enabled` 拋錯，該陣營維持無自動令）。AI_BARE → 引用必空（G5）。AI_FULL → 可引用（RAG 現況多為空，G5 按空庫語義）。

---

## 5. 目標與勝負（O11.5）

### 5.1 想定目標（機器可讀）

複用 `contracts/scenario.schema.json` 的 `victory_conditions[]`（`{faction, condition}`）。`condition` 由 `core/app/scenario/triggers.py` 的 DSL 表達，首版支援：

- `DESTROY_UNIT`（殲滅指定敵單位/達戰損比）
- `SEIZE_HEX`（本陣營單位進駐目標 hex / objective 區）
- `HOLD_UNTIL`（守住某區至時限）
- `TIME_LIMIT`（到時限依殘存/佔領判定，或判平）

（超出首版者記 PROGRESS Backlog。）

### 5.2 勝負評估與自動收場

Kernel 每 `decision_interval`（或每 N tick）以 DSL 對活 hot state 評估各陣營 `victory_conditions`：

- 任一陣營達成 → 宣告該陣營勝 → **自動停止 session**（sim runtime 收斂）→ 觸發 AAR 生成。
- 到 `TIME_LIMIT` 未分勝負 → 依規則判平/守方勝。
- 勝負結果入 Ledger（`SESSION_CONCLUDED` 事件，含勝方/條件/tick），前端顯示橫幅。

**紅線**：勝負由**確定性 DSL 對物理狀態**求值，**非** LLM 裁定（LLM 只產令，不判勝負）。

---

## 6. 決定性與重播（O11.6）——方案已現成

- **錄製**：使用者本機 Ollama 跑一場 → `RecordingClient` 包 `OpenAICompatibleClient`，把每次 (prompt→回應) 寫成 fixture（`ai/matso_ai/inference/` 的錄放格式）。
- **重播**：CI / air-gapped / golden 用 `ReplayClient.from_dir()` 按 prompt 雜湊重播——**零網路、零 GPU、位元一致**。
- **golden 保護**：自主 AI 為**獨立場次類型**；現有 6 條 golden replay **不含 AI**、路徑不變（AI worker 只在「自主模式 session」啟動）。新增自主場次的決定性由「固定 seed（SimClock/DeterministicRNG）+ ReplayClient 錄音」共同保證：同想定 + 同錄音 → 同結局。
- LLM 呼叫的非決定性診斷（latency 等）只入 side log，**不入被 hash 的 Ledger 狀態**（R8 教訓）。

---

## 7. 分階段實作計畫

> 每階段可獨立驗收、可 commit（訊息含 O11.x）。**golden 影響**欄標「無」＝不碰 golden 路徑。

### **P-A｜O11.1 Faction COP context builder**（S・golden 無）
- **交付**：`core/app/ai_loop/context.py` `build_faction_context`；單元測（霧化正確、序列化）。
- **驗收**：A 陣營快照不含未偵測敵；context 可餵 prompt。

### **P-B｜O11.2 陣營泛化 + LlmFactionDecider**（M・golden 無）
- **交付**：`run_opfor_turn`→`run_faction_turn`（含 alias）；`LlmFactionDecider`（接 #54 Ollama 設定 + RoleManager + client）；場景注入陣營人格。
- **驗收**：mock/Replay client 下，藍紅各得結構正確 orders；AI_BARE 引用空、AI_FULL 帶引用；單模型 adapter 切換數=0。

### **P-C｜O11.3 護欄 G3 接線 + 指令橋接**（M・golden 無）
- **交付**：`run_precheck`→`OrderFeasibilityChecker` 注入 G3；`AiTurnResult.orders`→`OrderRequest`→`OrderService.submit` 橋接（issuer=AI 指揮官 principal）。
- **驗收**：幻想的不可行令被 G3 剔除；合法令成 VALIDATED；越權/幻覺（打未偵測敵、無彈）被護欄擋並記 `GUARDRAIL_INTERVENTION`。

### **P-D｜O11.4 Kernel 決策排程器（接活）**（L・**核心整合**・golden 無）
- **交付**：`sim_runtime._run_session` 為每 AI 陣營起 async decision worker（固定心跳，非 pre_tick）；`NoOpTriggerChecker`→固定週期觸發；LLM 逾時 fallback HOLD。
- **驗收**：雙 AI 全 AI session，tick 節奏不被 LLM 拖慢；AI 令非同步到位並執行；單位真的動/交戰。**（此為第一個可展示里程碑：活體 AI 對抗）**

### **P-E｜O11.5 勝負引擎綁定 + 自動收場 + AAR**（M・golden 無）
- **交付**：`triggers.py` DSL 支援 §5.1 條件；Kernel 每週期評估 `victory_conditions`；`SESSION_CONCLUDED` 事件 + 自動停 + AAR 觸發。（可能需 scenario schema 微調 → prisma migrate。）
- **驗收**：達成條件即自動終局出 AAR；時限到判平/守方勝；結果入 Ledger。

### **P-F｜O11.6 決定性重播（錄放接線）**（M・**保護 golden**）
- **交付**：自主場次以 `RecordingClient` 錄、`ReplayClient` 重播的接線 + 一組錄音 fixture；CI 對「自主 replay 場次」驗逐 tick 一致；斷言現有 golden 6 綠不變。
- **驗收**：同想定 + 同錄音 → 同結局；golden 不動。

### **P-G｜O11.7 前端自主主控台**（M・golden 無）
- **交付**：新 `platform/app/pages/…`：設定（選想定、逐陣營指派 AI/人格/目標、選 AI 模式）+ 觀戰（COP + 事件流 + AI 推理軌跡 + 護欄判決 + 目標進度）+ 結果（勝負橫幅 + AAR）。複用 cop.vue 的 stream/圖層。
- **驗收**：能一鍵起一場雙 AI 自主推演並觀戰到收場；Playwright smoke。

### **P-H｜O11.8 韌性收尾**（S–M・golden 無）
- **交付**：LLM 逾時/失敗 fallback、AI 指令速率上限（防洗版）、runaway 迴圈守衛、per-worker 日誌與觀測、成本/延遲面板。
- **驗收**：LLM 斷線時 sim 續跑（陣營 HOLD）；不產生無界指令。

### 最小可行縱切（先證明會動）
**P-A → P-B → P-C → P-D 極簡版**：目標只判「殲滅對方」（P-E 完整勝負延後）、固定心跳、Replay 或真 Ollama 皆可。跑通「AI 自己下令、單位動起來打起來」後，再補 P-E/P-F/P-G/P-H。

### 後續階段（本 SPEC 記方向，不在首版）
- **第二階段：人對 AI**——`faction → {human | AI}` 混合；P-G 設定頁已預留指派介面；需處理人類回合與 AI 心跳並存。
- **第三階段：事件觸發器**——以 `TriggerChecker`（接觸/目標受威脅/被擊）補固定心跳，降延遲、增擬真。

---

## 8. 契約與 schema 變更

### 8.1 contracts（先行）
- `contracts/ai_output.schema.json`：確認 `opfor_decision`（`intent/orders/ihl_self_check`）泛用於任一陣營；`tactical_order` 已含 `fire_policy`（P3 完成）。必要時新增中性別名 `commander_decision`（不破既有）。
- `contracts/scenario.schema.json`：`victory_conditions[].condition` 依 §5.1 補 DSL 型別定義（O7.2 骨架擴充）。
- `contracts/core_api.yaml`：自主推演的 session 建立/觀戰端點（如 `POST /sessions/{id}/autonomy` 指派陣營控制 + 起停）。

### 8.2 DB（僅在確有需要時，走 prisma migrate）
- 若 `victory_conditions` / 陣營控制指派（human/AI + 人格 + 目標）要持久化到 session，於 `Scenario`/`Session` 加 JSON 欄（權威＝`db/prisma/schema.prisma`，附 migration）。優先**複用既有 JSON 欄**（如 scenario 既有 config）避免 migration。

### 8.3 部署現實（P0 必決）
- **core 容器目前無 `httpx`、未裝 `matso_ai`**（`ai/` 是獨立套件；#54 的 test-llm 故意用 stdlib `urllib`）。活期 decider 要打 Ollama，二選一：
  - **(A) 推薦**：core 容器映像加裝 `matso_ai` + `httpx`（複用 client/role_manager/replay 全套），rebuild core。
  - (B) 於 core 寫薄 stdlib OpenAI client（如 #54），只重用 core 側 `run_faction_turn`/護欄，不載入 `matso_ai`。
- 決策點：(A) 複用最大但容器變重且引入 httpx；(B) 輕但 client/錄放邏輯重複。**已拍板 (A)**（使用者定案；錄放/角色管理價值高，不宜重造）——P-B 起 core 容器加裝 `matso_ai` + `httpx` 並 rebuild。

---

## 9. 開放決策與風險

| # | 項目 | 現況/建議 |
|---|---|---|
| D1 | core 容器是否裝 matso_ai+httpx | **已定 (A) 裝**（§8.3，使用者拍板） |
| D2 | 決策心跳長度 | 場景可設；建議首版 30–60 模擬秒/次（LLM 15s，留餘裕） |
| D3 | victory DSL 首版條件集 | §5.1 四類；更多記 Backlog |
| D4 | 陣營人格來源 | 場景注入（doctrine 傾向 + 目標文字）；免每陣營 LoRA |
| R1 | LLM 15s 延遲 | §3.1 async worker 解耦，已納入設計 |
| R2 | LLM 幻覺越權令 | G1–G6 + G3 precheck + 指令再驗（§3.3/3.4）；雙保險 |
| R3 | golden 污染 | AI 路徑條件化 + ReplayClient（§6）；斷言 6 綠不變 |
| R4 | AI 洗版無界指令 | P-H 速率上限 + runaway 守衛 |
| R5 | 單模型雙陣營「人格洩漏」 | 各陣營獨立 context + prompt；佇列序列化；驗證產令不含對方視角 |

---

## 10. 任務板對照（新里程碑 O11）

| 卡 | 名稱 | 階段 | 規格 |
|---|---|---|---|
| O11.1 | Faction COP context builder | P-A | §3.2 |
| O11.2 | 陣營泛化 + LlmFactionDecider（接 Ollama） | P-B | §4 |
| O11.3 | 護欄 G3 feasibility + 指令橋接 VALIDATED | P-C | §3.3/3.4 |
| O11.4 | Kernel 決策排程器（async 固定心跳 worker） | P-D | §3.1 |
| O11.5 | 勝負引擎綁定 + 自動收場 + AAR | P-E | §5 |
| O11.6 | 決定性重播（RecordingClient/ReplayClient 接線） | P-F | §6 |
| O11.7 | 前端自主主控台（設定/觀戰/結果） | P-G | §7 |
| O11.8 | 韌性收尾（逾時/速率/守衛/觀測） | P-H | §7 |

> 落地時把上表加入 [TASKS.md](TASKS.md)（O11 里程碑），並於 [PROGRESS.md](PROGRESS.md) 記「下一步」。動工前依 CLAUDE.md 強制流程：建 `docs/worklog/O11.1.md` 等。
