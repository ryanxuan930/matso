# MATSO 任務板（TASKS）

> **用法**：對 AI Agent 說「開發 O1.1」，Agent 必須：
> 1. 依 [CLAUDE.md](CLAUDE.md) 的開工流程行事（讀 PROGRESS.md → 讀本檔對應任務 → 讀「規格」欄列出的 SPEC_FULL/HOW_TO 章節）。
> 2. 建立/接續 `docs/worklog/O<id>.md` 工作日誌（格式：[docs/worklog/_TEMPLATE.md](docs/worklog/_TEMPLATE.md)），**邊做邊寫**。
> 3. 完成後跑「驗收」欄全部指令，更新 PROGRESS.md 任務板，git commit（訊息含任務編號，如 `feat(core): O1.1 SimClock + DeterministicRNG`）。
>
> **編號規則**：`O<里程碑>.<序號>` ≡ HOW_TO.md §5 的 `M<里程碑>-<序號>`（同一張卡的兩種編號）。
> 任務內容以本檔為準；HOW_TO §5 為摘要。**M0（O0.x）已全部完成**，見 PROGRESS.md。
>
> **依賴**：O1 → O2 → O3 → {O4, O5 可平行} → O6 → O7 → O8。**O9（文檔轉換，SPEC_INGEST.md）獨立可平行**，只餵資料不被依賴。跨里程碑不可跳做，里程碑內依各任務標注的 `[deps]`。

---

## O1 模擬骨幹（Simulation Kernel）

### O1.1 SimClock + DeterministicRNG
- **目標**：模擬時間與隨機數的唯一來源，P4 可重現性的地基。
- **規格**：SPEC_FULL §3.1–3.2、HOW_TO §4.1。
- **產出**：
  - `core/app/engine/clock.py`：`SimClock`——tick 為 int，`now()` 回傳 `SimTime(tick, sim_time_ms)`；`advance()` 只能由 Kernel 呼叫。時間壓縮比例屬 Kernel 排程層，不屬 SimClock。
  - `core/app/engine/rng.py`：`DeterministicRNG(master_seed, stream_id)`——numpy `PCG64`；stream_id 字串以 SHA-256 折疊成子種子；不同 stream 的 generator 完全獨立。提供 `random()`, `uniform(a,b)`, `choice(seq)`。
  - core 加依賴 `numpy`（`cd core` 改 pyproject 後 root `uv sync`）。
- **驗收**：
  - 測試：同 (seed, stream) 產生相同序列；不同 stream 互不影響（先抽 A 再抽 B ＝ 只抽 B 的結果不變）；`grep -rn "datetime.now\|time.time()" core/app/engine/` 無結果。
  - `uv run pytest core/tests/unit -q` 綠；`uv run mypy`、`uv run ruff check .` 綠。

### O1.2 Event Ledger writer + hash chain　[deps: 無（DB 已就緒）]
- **目標**：不可變事件帳本寫入器與竄改偵測。
- **規格**：SPEC_FULL §15.3。
- **產出**：
  - `core/app/state/ledger.py`：`LedgerWriter`——`append(events)`：seq 單調發號（per session）、`selfHash = SHA256(prevHash ‖ canonical_json(payload))`、批次寫入 `TacticalEventLog`。禁止提供 update/delete 方法。
  - `ops/tools/verify_ledger.py`：CLI `--session <id>`，重算整條 hash chain，回報第一個斷點。
  - `ops/tools/grant_ledger_readonly.sql`：對 app 帳號 revoke UPDATE/DELETE on TacticalEventLog 的 grant 腳本（附使用說明註解）。
- **驗收**：整合測試（連 compose 的 MariaDB:3307）：寫入 100 事件 → verify 通過；手動 UPDATE 一筆後 verify 必須抓到。`canonical_json` 需有「鍵序不同、輸出相同」的單元測試。

### O1.3 Kernel tick loop　[deps: O1.1, O1.2]
- **目標**：SPEC_FULL §3.3 虛擬碼的骨架實作（movement/sensors/comms/logistics 先接 no-op stub 介面）。
- **規格**：SPEC_FULL §2.3、§3.3。
- **產出**：`core/app/engine/kernel.py`——tick 迴圈、pending order queue 的 drain、`TICK_OVERRUN` 事件（tick 超過預算時寫入 Ledger 並降頻）、各子系統的 Protocol 介面（`MovementSystem`, `SensorSystem`, ...）。
- **驗收**：單元測試以 fake 子系統驗證呼叫順序；模擬一個慢子系統 → 觸發 `TICK_OVERRUN`；tick 預算可由 config 注入。

### O1.4 Redis 熱狀態 + single-writer　[deps: O1.3]
- **目標**：單位熱狀態進 Redis，Kernel 是唯一寫入者，並產生 per-tick diff。
- **規格**：SPEC_FULL §3.4、§16.2（STATE_DIFF payload）。
- **產出**：`core/app/state/hot_state.py`（key: `session:{id}:unit:{id}`）、diff 計算器（只含變動欄位）、`core/app/state/broadcaster.py` stub（介面先定，WS 實作在 O4.3 對接）。
- **驗收**：整合測試連 compose Redis：寫入→讀回 roundtrip；改 3 個欄位 → diff 恰含 3 欄。

### O1.5 Checkpoint / rollback / 崩潰復原　[deps: O1.4]
- **目標**：zstd 快照、任意檢查點回滾、重啟後由 checkpoint+ledger 重建。
- **規格**：SPEC_FULL §3.4、§18（RPO=0 / RTO≤5min）；**先解 ADR 002**（stateBlob >16MB 策略）並寫入 docs/adr/002。
- **產出**：`core/app/state/checkpoint.py`、rollback 邏輯（rollback 本身寫入 `ROLLBACK` 事件）、復原程序 `recover(session_id)`。core 加依賴 `zstandard`。
- **驗收**：整合測試：跑 N ticks → kill 狀態（清 Redis）→ recover → 狀態 hash 與 kill 前一致。

### O1.6 Golden replay harness　[deps: O1.5]
- **目標**：SPEC_FULL §19.1 的重播驗證機制 + CI 接入。
- **產出**：`core/tests/replay/harness.py`（重跑想定、比對 `stateHash`）、`ops/tools/rerecord_golden.py`、第一個 golden：空想定跑 100 ticks。移除 `test_golden_placeholder.py`。
- **驗收**：`uv run pytest core/tests/replay -m golden` 以真 golden 通過；改動任一裁決常數（手動實驗）會使 hash 比對失敗。
- **範圍註記（O1.7/R10）**：Phase 1 驗證「合成想定 + seed 決定性」；SPEC §3.2 字面的「讀 Ledger 指令序列重播」需 orders 存在，**列入 O3.1 驗收**。

### O1.7 M0–M1 code review 修復（2026-07-19 完成）
- **內容**：修復 review 發現 R1–R10 + r11–r18（清單見 PROGRESS.md backlog、worklog docs/worklog/O1.7.md）。
- 重點：rollback×ledger×recover 三連 bug（ledgerSeq 錨定 + 較晚 checkpoint 刪除 + writer tip 衝突自復原）、CI 整合測試真跑 + coverage gate、TickPacer 自動降頻、detail 診斷欄（不入 hash）、Redis 批次化 + to_thread、errors.py、測試鷹架 dedup。

---

## O2 地理引擎（Terrain Module）

> 前置：使用者提供 `TW_ALL.tiff` 放至 `modules/terrain/data/`（不入 git）。沒有檔案時各任務用測試夾具（小型合成 GeoTIFF，工具產生、入 git、<1MB）開發，真檔到位後跑 benchmark。

### O2.1 DTED 載入與高程查詢
- **規格**：SPEC_FULL §4.1、§4.3。依賴加入 `rasterio`, `numpy`（terrain package；GDAL 由 rasterio wheel 內帶）。
- **產出**：`modules/terrain/terrain/dted.py`——memory-mapped 載入、`get_elevation(lat,lng) -> (elevation_m, water)`（nodata→water）、冷啟動 <30s。合成夾具產生器 `modules/terrain/tests/make_fixture.py`。
- **驗收**：單元測試用合成夾具驗證已知點高程；benchmark 測試（pytest-benchmark 或手寫計時）p99 < 5ms 標記為真檔限定。

### O2.2 Hex grid 預計算　[deps: O2.1]
- **規格**：SPEC_FULL §4.2。依賴 `h3`, `pyarrow`。
- **產出**：離線預計算 CLI（H3 res 7–9 cell 屬性 → parquet 快取）、`get_cell_batch(h3_list)`。terrain_class 先以坡度+高程規則推導。
- **驗收**：夾具區域全 cell 計算正確性抽查；parquet 快取命中後查詢 p99 < 20ms。

### O2.3 LOS / Viewshed　[deps: O2.1]
- **規格**：SPEC_FULL §4.3（30m 取樣、4/3 等效地球半徑、AGL）。
- **產出**：`check_los(observer, target)`、`get_viewshed(observer, radius)`。
- **驗收**：property tests（自己看自己=true、遮蔽單調性）；與 GRASS `r.viewshed` 對照 ≥98%（100 抽樣點；GRASS 以 docker 跑，腳本放 `modules/terrain/tests/grass_compare/`，CI 可 skip、release 前必跑）。

### O2.4 A* 路徑　[deps: O2.2]
- **規格**：SPEC_FULL §4.3；成本表 `contracts/mobility_matrix.json`（含 slope_penalty 公式）。**不要用 h3 內建距離做 heuristic 以外的用途**（HOW_TO §8）。
- **產出**：`get_path(from_h3, to_h3, mobility_profile)`。
- **驗收**：property test：回傳路徑成本 ≤ 任一鄰接替代路徑；不可達回 `reachable=false`；BOAT 不能走陸、WHEELED 不能進 WATER/-1 地形。

### O2.5 Terrain 插件化　[deps: O2.1–O2.4]
- **規格**：SPEC_FULL §16.3、§17；契約 `contracts/proto/matso/terrain/v1/terrain.proto` 與 `plugin_base.proto`。
- **產出**：先實作 `modules/_sdk/`（`MatsoPlugin` base：gRPC server、manifest、health、註冊、graceful shutdown + 測試 harness）→ terrain 套上 SDK → Core 端 client（`core/app/plugins/terrain_client.py`，含 circuit breaker 與「Terrain DOWN → Session PAUSE」預案）→ compose 加 terrain 服務。proto codegen 進 build（buf generate，產物不入 git）。
- **驗收**：`_sdk` harness 整合測試；compose 全 stack `--wait` 綠；kill terrain 容器 → Core 於 30s 內標記 DOWN 並 PAUSE session（整合測試）。

---

## O3 裁決核心（先讀 SPEC_FULL §7 全文）

### O3.1 Order pipeline
- **規格**：SPEC_FULL §2.3（八步生命週期）、§16.1；`Order` 表已存在。
- **產出**：`core/app/orders/`——REST `POST /sessions/{id}/orders`（契約先補完 `contracts/core_api.yaml` 的 request/response schema）、validator、同步物理預檢（呼叫 terrain client，p99<50ms）、狀態機（PENDING→…→COMPLETED/REJECTED/CANCELLED 全轉移 + 非法轉移防護）。
- **驗收**：schemathesis 對已實作端點通過；狀態機 property test；預檢失敗回 422 + error code。**加：ledger 指令序列重播想定接入 golden harness**（補完 SPEC §3.2 字面保證，O1.6 範圍註記 / O1.7/R10）。

### O3.2 交戰裁決　[deps: O3.1]
- **規格**：SPEC_FULL §7.1；武器資料 schema `contracts/weaponeering.schema.json`。
- **產出**：`core/app/adjudication/engagement.py`（**純同步純函數**，輸入 `EnvSnapshot`，不做 RPC——HOW_TO §4.2 的五步開發模式照做）+ 種子武器資料（3 種 KINETIC 模板）。
- **驗收**：Hypothesis property tests：距離↑→P_hit 單調不增、係數=1 退化為 base、彈藥=0 必 REJECTED；覆蓋率 ≥95%（HOW_TO §3 對 adjudication 的要求）。

### O3.3 偵測與 intel store　[deps: O3.1]
- **規格**：SPEC_FULL §7.2；`IntelContact` 表。**faction-scope 是後端責任**。
- **產出**：sensor sweep（H3 k-ring 預過濾）、DETECTED→CLASSIFIED→IDENTIFIED 升級邏輯、`core/app/intel/store.py`、faction-scoped 查詢 API。
- **驗收**：k-ring 過濾正確性測試；**RED token 查詢永遠拿不到 BLUE ground truth 的 contract test**（這條測試從此進 CI 常駐）。

### O3.4 移動執行　[deps: O2.4, O3.1]
- **產出**：MOVE order → terrain path → 逐 tick 推進 + 油料消耗 stub（接 O5 後換真表）。
- **驗收**：整合測試：下 MOVE 令 → N ticks 後位置=路徑終點；路徑中斷（地形事件）→ 單位停在斷點 + 事件入帳。

### O3.5 聚合裁決（Lanchester）　[deps: O3.2]
- **規格**：SPEC_FULL §7.1 末段；切換閾值由 scenario 設定（`aggregate_adjudication_level`）。
- **驗收**：能量守恆式 property test（雙方總戰損 ≤ 初始戰力）；同 seed 同結果。

### O3.6 腳本對戰驗收（M3 的 DoD）　[deps: O3.1–O3.4]
- **產出**：`core/tests/integration/test_scripted_battle.py`——純 API 驅動：藍軍移動→紅軍偵測到→交戰→戰損入帳→雙方 intel 視圖各自正確。
- **驗收**：此測試綠 = O3 里程碑完成。

---

## O4 前端 COP（可與 O5 平行；先讀 SPEC_FULL §13）

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O4.1 | 認證 + lobby（login/JWT/refresh；後端 auth 端點也在此卡實作，Argon2id+JWT） | Playwright：登入→lobby；錯誤密碼被拒；token refresh 流程 |
| O4.2 | 地圖基座（MapLibre + 離線 tile server 進 compose + hillshade + H3 hex 層） | 地圖可平移縮放；hex 層開關；離線（斷網）可用 |
| O4.3 | WS stream + Pinia store（`useSessionStream`：HELLO/last_seq 補償/RESYNC；後端 WS 端點同卡實作，含 ring buffer 與背壓斷線） | 斷線重連補齊事件的整合測試；慢 client 被斷線而非塞爆 |
| O4.4 | 單位渲染 + fog of war（milsymbol atlas、intel 三級渲染、OFFLINE 虛影） | 500 單位 ≥30 FPS（Playwright + FPS 量測腳本） |
| O4.5 | 下令 UX（指令面板、precheck 顯示、pending/歷史） | 下 MOVE/ENGAGE 令全流程可用 |
| O4.6 | E2E 煙霧測試（Playwright：登入→建局→下令→看到裁決事件）並進 CI | CI e2e job 綠 |

（O4.x 逐卡開工時：先在 worklog 寫 UI 結構計畫；元件放 `platform/app/components/<區域>/`；API 型別一律由 `contracts/core_api.yaml` 生成，禁手寫。）

## O5 環境模組（可與 O4 平行）

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O5.1 | Weather module 骨架（套 `_sdk`；SYNTHETIC 模式：腳本關鍵影格插值；輸出過 `weather_payload.schema.json` 驗證） | schema 驗證測試；插值正確性 |
| O5.2 | CWA LIVE 模式（API 拉取、格網化、stale 降級 + 30min 告警） | 斷網→stale=true；恢復→自動回 LIVE |
| O5.3 | 天氣效果整合（`EnvSnapshot` 納入天氣係數；命中/移動/UAV 受影響） | 整合測試：同一交戰在暴雨 vs 晴天結果分佈可觀測地不同（固定 seed 比較係數） |
| O5.4 | Comms 模組（鏈路預算、networkx mesh 連通、ONLINE/DEGRADED/OFFLINE 的指令延遲/凍結後果） | SPEC_FULL §6.2 表格逐條有測試 |

## O6 AI Phase 1（先讀 SPEC_FULL §9–10 全文；紅線：AI 永不裁決物理、護欄不可 bypass）

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O6.1 | vLLM client + RoleManager（OpenAI-compatible、LoRA 熱切換、角色批次佇列、OPFOR 優先、`AIInvocationLog` 全記錄） | 無 vLLM 時以錄放 mock 測試；佇列優先權測試 |
| O6.2 | **AI 運作模式（§9.0）**＋Guardrail Gateway G1–G6（`guardrail_profiles.yaml`；攔截=Ledger 事件；G5 模式感知：`AI_BARE`/空庫時非空引用=捏造） | 覆蓋率 ≥95%；每個 G 有至少一個「必攔」案例；`AI_OFF` 下 AI 端點回 `AI_DISABLED`；模式切換測試 |
| O6.3 | RAG 管線（入庫 CLI、Qdrant collections **含 doctrine_general**、bge-m3、引用查核 API 供 G5）；**空語料是常態**：空庫回空結果+`index_empty`，上游降級 `AI_BARE` 不失敗；只吃 markdown（PDF 走 SPEC_INGEST/O9） | 入庫→檢索→引用查核 roundtrip；**空庫降級測試**（0 語料下 AI 呼叫仍成功且引用為空） |
| O6.4 | 五角色 prompts（**依模式適配**：`AI_BARE` 版不含引用要求）+ output schemas + eval runner；eval cases 盡力而為（語料/軍方資料未到位前可少量或缺） | `matso_ai.evals.run` 全綠；schema 通過率 ≥98%；**案例庫空時 schema-only + `EVAL_CORPUS_EMPTY` 警告**（§19.4 條件式 gate） |
| O6.5 | OPFOR 自主迴路（事件驅動→產令→護欄→物理預檢→pending）；**尊重 ai_mode**（`AI_OFF` 不啟動；`AI_BARE` 無引用） | O3.6 想定中紅軍無人操作仍合理應對（錄放 mock 下可重現）；`AI_OFF` 時紅軍完全由人操作（傳統兵推回歸測試） |
| O6.6 | eval gate 進 CI（SPEC_FULL §19.4 四門檻，**條件式**） | CI eval job 綠（錄放 mock；案例空→schema-only+警告；真模型 eval 為手動觸發 workflow） |

## O6+ 多陣營（N-faction + 關係矩陣；SPEC_FULL §12.1、ADR 006；**O7.1 依賴 O6.7**）

> 設計定案 2026-07-21：faction 由封閉 enum 改為想定定義字串 id（`WHITE_CELL` 保留字）；
> 關係矩陣 `ALLIED/NEUTRAL/HOSTILE`（對稱、未宣告預設 NEUTRAL、White Cell 可局中調整→
> `FACTION_RELATION_CHANGED` 事件）。紅線：敵我判斷一律經 `core/app/factions/` 關係服務，
> 禁止子系統自行 `faction != mine` 判敵。

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O6.7 | 資料模型與契約遷移：prisma `enum Faction`→`String`（migration，ADR 004 流程）+ core `Faction` 降為保留字/驗證 + 契約修漂移（core_api BLUE/RED/WHITE/GREEN → string pattern）+ scenario.schema.json `factions:`/`relations:`/victory_conditions 任意陣營 + 前端型別 | schema-sync 綠；既有 BLUE/RED 測試以「字串實例」照過；未知 faction 於 API 被拒；`WHITE_CELL` 不可入 orbat/矩陣（驗證測試） |
| O6.8 | 關係矩陣服務 `core/app/factions/`（載入/查詢/局中調整→Ledger 事件）+ 整合：intel sweep 配對依關係（ALLIED 不成 contact）、ENGAGE 預檢拒 ALLIED/NEUTRAL、G4 攔 friendly-fire/攻中立、WS audience | 三方矩陣單元測試（含預設 NEUTRAL、對稱性、宣戰/停火事件重播）；「藍打盟軍/中立 → 422/G4 攔」測試；黃軍觀測者同時偵測藍與紅 |
| O6.9 | 聚合裁決泛化：`resolve_aggregate_tick(force_a, force_b)` 中性化 + 多方 HOSTILE 配對逐一裁決（確定性排序）+ 事件欄 `initiator_loss/target_loss` + **golden replay 重錄** | 三方混戰聚合測試（A-B 敵對、B-C 敵對、A-C 中立 → 只裁 2 組配對）；同 seed 決定性；golden 綠 |
| O6.10 | 前端多陣營：SIDC affiliation 由關係推導（own/ALLIED=F、NEUTRAL=N、HOSTILE=H）+ faction 顏色（scenario 定義）+ lobby/建局 faction 選擇 N 方 + 三方 E2E | Playwright：三方想定下黃軍視角同時見紅藍 contact 且視覺可區分；smoke 全鏈路仍綠 |

## O7 想定與白軍

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O7.1 | Scenario schema 補完 + loader（精確錯誤路徑）+ 官方想定 #1 tutorial-platoon（**[deps: O6.7]**——factions/relations 為 scenario 權威，§12.1） | 壞檔案的錯誤訊息含精確路徑；想定可載入開局；factions/relations 驗證（未知陣營/保留字/非法關係→精確錯誤） |
| O7.2 | MSEL 觸發引擎（時間/條件觸發 DSL、ad-hoc inject API） | 條件觸發整合測試；inject 權限限 White Cell |
| O7.3 | 想定編輯器（ORBAT 樹、地圖佈署、控制措施、MSEL 時間軸、匯入匯出） | 編輯→匯出→重新載入 roundtrip |
| O7.4 | 白軍控制台（時間控制、視角切換、注入、AI 監控、護欄事件流、rollback UI） | 全知/單方視角切換正確 |
| O7.5 | RBAC 完整化（SPEC_FULL §12 全角色 + faction-scope 中介層） | contract test：每個角色×每個端點的存取矩陣 |

## O8 AAR

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O8.1 | 重播服務（Ledger→前端時間軸流式重建、書籤） | 任一 tick 的重建狀態與 checkpoint 一致 |
| O8.2 | 統計儀表板（SPEC_FULL §14.2 指標預計算 job + 圖表） | 指標數字與 Ledger 手算抽查一致 |
| O8.3 | AI 敘事報告（AAR_ANALYST；段落引用 event id 可點擊跳轉） | 引用的 event id 100% 存在（自動查核） |
| O8.4 | 匯出（PDF + JSON/CSV + 匿名化選項） | 匿名化後無使用者名/單位真名 |

## O9 文檔轉換子系統（規格：**SPEC_INGEST.md**；獨立於 M6，可平行；語料到位前不阻塞 O6）

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O9.1 | Ingest P1：文字 PDF → staging markdown（PyMuPDF 抽取、章節偵測、~512 token 分節、錨點自動編、front-matter 骨架）+ `promote` CLI（格式校驗 + 強制 reviewer → corpus/） | 合成 PDF fixture roundtrip；promote 拒收壞 front-matter/重覆錨點；未 promote 內容入庫 CLI 不可見 |
| O9.2 | Ingest P2：OCR fallback（本機 tesseract/PaddleOCR，模型檔 env 注入 + 缺失降級「僅文字層」）+ 節級信心分級 | 掃描頁 fixture → 產出含 confidence；低信心節進報告；斷網可跑（air-gapped） |
| O9.3 | Ingest P3：表格轉換 + 告警註記、`report` 彙總、與 O6.3 入庫串接端到端 | inbox→staging→promote→ingest→檢索命中 全鏈路測試 |

---

## 附錄：任務中斷與續作（額度用完時的保命機制）

1. **worklog 是即時的**：每完成一個實質步驟（一個檔案、一個測試通過、一個決策）就更新 `docs/worklog/O<id>.md`，不是收工才寫。
2. **「中斷續作指引」段落永遠保持最新**：任何時刻被砍掉，下一個 agent 讀該段就能接續。
3. **commit 節奏**：任務內每到一個綠燈點（測試通過的完整小步）就 commit（`wip(scope): O1.2 hash chain 完成，verify CLI 未做`）。任務完成時 squash 與否由使用者決定，預設保留。
4. 接續中斷任務的指令就是再說一次「開發 O1.2」——agent 會從 worklog 的中斷續作指引接手，**不要重做已完成步驟**。
