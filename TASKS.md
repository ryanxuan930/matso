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
> 關係矩陣 `ALLIED/NEUTRAL/HOSTILE`（對稱、未宣告預設 HOSTILE、White Cell 可局中調整→
> `FACTION_RELATION_CHANGED` 事件）。紅線：敵我判斷一律經 `core/app/factions/` 關係服務，
> 禁止子系統自行 `faction != mine` 判敵。

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O6.7 | 資料模型與契約遷移：prisma `enum Faction`→`String`（migration，ADR 004 流程）+ core `Faction` 降為保留字/驗證 + 契約修漂移（core_api BLUE/RED/WHITE/GREEN → string pattern）+ scenario.schema.json `factions:`/`relations:`/victory_conditions 任意陣營 + 前端型別 | schema-sync 綠；既有 BLUE/RED 測試以「字串實例」照過；未知 faction 於 API 被拒；`WHITE_CELL` 不可入 orbat/矩陣（驗證測試） |
| O6.8 | 關係矩陣服務 `core/app/factions/`（載入/查詢/局中調整→Ledger 事件）+ 整合：intel sweep 配對依關係（ALLIED 不成 contact）、ENGAGE 預檢拒 ALLIED/NEUTRAL、G4 攔 friendly-fire/攻中立、WS audience | 三方矩陣單元測試（含預設 HOSTILE、對稱性、宣戰/停火事件重播）；「藍打盟軍/中立 → 422/G4 攔」測試；黃軍觀測者同時偵測藍與紅 |
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

## O10 部署接線（M0–M9 功能已完成；本群組為「接真實執行期」；權威清單見 **docs/DEPLOYMENT.md**）

> 全部接點皆為**注入式介面（程式碼已備）**——部署即接線，不改核心邏輯。依 A→B→E→D→C→F→G 順序。
> 先做 O10.1 即可跑 **AI_OFF 傳統兵推**（不需 AI 節點）。

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O10.1 | Kernel 真實裝配（event_sink=LedgerWriter、hot_state=RedisHotState、broadcaster=RedisBroadcaster、tick_source=SimClock、wall_clock=PerfCounterClock；terrain/weather/comms gRPC client；聚合分流接 resolve_multiway_tick；DB 走 to_thread）+ 想定開局（lobby create_session→create_session_from_scenario） | 真 compose 跑一場 AI_OFF 傳統兵推：開局→移動→偵測→交戰→戰損入 Ledger→AAR 可讀；tick p99 達標 |
| O10.2 | AI 節點部署（vLLM `OPENAI_BASE_URL`、bge-m3、Qdrant 服務；RecordingClient 錄 fixtures→CI ReplayClient；真模型 eval 手動 workflow 跑 §19.4 四門檻） | AI_FULL 下 OPFOR 產令經護欄；eval 四門檻達標；air-gapped 內網可跑 |
| O10.3 | AI 迴路↔kernel（run_opfor_turn 接活：事件驅動→護欄→物理預檢→pending；intervention→Ledger）+ `WargameSession.aiMode` 欄位 migration + resolve/require_ai_enabled 接端點 | O3.6 想定 AI_BARE 下紅軍自主應對；AI_OFF 迴路不啟動（傳統兵推回歸）；護欄不可 bypass |
| O10.4 | 想定/白軍執行期（relations 熱狀態 + set_relation→Ledger；MSEL 掛 kernel check_triggers；victory 判勝負；SESSION_CONTROL 消費→rollback recover；ENGAGE 目標改真 intel contacts；前端 faction 顏色由 scenario 注入） | White Cell 宣戰/停火即時生效並可重播；MSEL 條件觸發注入；rollback 復原正確；移除 STUB units affordance |
| O10.5 | 安全補完（refresh token 撤銷/rotation + migration [C5]；建局角色 gate [C8]；管理 audit log） | refresh 撤銷後不可換發；非授權角色不可開演習；每角色×端點矩陣仍綠 |
| O10.6 | OCR/資產 & 觀測性（tesseract/PaddleOCR 模型檔 env 注入；GRASS r.viewshed release 對照 ≥98%；Prometheus/Grafana §20.3 指標 + 告警；CI node24 升級 + 覆蓋率工具） | 掃描 PDF OCR 進 staging；Grafana 推演健康儀表板；TICK_OVERRUN/plugin DOWN/AI 逾時率告警 |

## O11 自主推演（規格：**SPEC_AUTONOMY.md**；落實並延伸 O10.3 AI 迴路↔kernel + O10.4 victory；多陣營 AI 自主對抗）

> 給定想定 + 各陣營目標 → 每個 AI 陣營一條 async 決策 worker（固定心跳），讀霧化 COP → LLM 產令 → 護欄 G1–G6 → 落 VALIDATED → 確定性引擎執行 → 每週期判勝負 → 自動收場 + AAR。**N 陣營**（示範雙陣營，架構支援多陣營）；單模型角色/人格切換。紅線：AI 只產令不裁決物理、不寫熱狀態、護欄無 bypass、霧化只在後端、決定性走 ReplayClient。

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| O11.1 | Faction COP context builder（`core/app/ai_loop/context.py`；霧化快照→prompt） | A 陣營 context 不含未偵測敵；可序列化餵 prompt |
| O11.2 | 陣營泛化 + `LlmFactionDecider`（接 #54 Ollama；core 容器裝 matso_ai+httpx） | 藍/紅各得結構正確 orders；AI_BARE 引用空；單模型 adapter 切換=0 |
| O11.3 | 護欄 G3 feasibility（包 run_precheck）+ 指令橋接 VALIDATED（OrderService.submit） | 不可行/越權令被 G3 剔除記 GUARDRAIL_INTERVENTION；合法令成 VALIDATED |
| O11.4 | Kernel 決策排程器（每 AI 陣營 async 固定心跳 worker；非 pre_tick） | 多 AI session tick 不被 LLM 拖慢；AI 令非同步到位並執行（**第一個可展示里程碑**） |
| O11.5 | 勝負引擎綁定 + 自動收場 + AAR（triggers.py DSL 評估 victory_conditions） | 達成條件自動終局出 AAR；時限判平/守方勝；結果入 Ledger |
| O11.6 | 決定性重播（RecordingClient 錄／ReplayClient 重播接線） | 同想定+同錄音→同結局；現有 golden 6 綠不變 |
| O11.7 | 前端自主主控台（設定：陣營指派/人格/目標/AI 模式；觀戰：COP+事件+AI 軌跡+護欄+目標進度；結果：勝負橫幅+AAR） | 一鍵起多 AI 自主推演並觀戰到收場；Playwright smoke |
| O11.8 | 韌性收尾（LLM 逾時 fallback HOLD、指令速率上限、runaway 守衛、per-worker 觀測） | LLM 斷線時 sim 續跑；不產生無界指令 |

---

## 移動真實化（規格：**SPEC_MOVEMENT.md**；擴充 SPEC_FULL §4.3/§5.3 + O3.4；使用者回報）

> 現況＝三套不一致移動模型（預覽/閘門/執行）：執行走**直線固定 40 km/h**、不分機動載具、不看地形、正常行軍零耗損。目標＝機動能力（機械化 vs 徒步）× 地形（類別/坡度/道路/涉水/障礙）× 行軍耗損的真實化，並收斂為單一速度模型與路由。**每 Phase 皆改變移動決定性輸出 → golden replay 重錄（預期）**。紅線：AI 只選目的地/節奏、不裁決物理；移動隨機走 `DeterministicRNG(stream="movement")`；契約先行。

| 任務 | 內容 | 驗收重點 |
|------|------|----------|
| #80 (Phase A) | Seed mobility_class/速度到 EquipmentTemplate；`UnitMobilityResolver` 導出 per-unit profile+速度；執行器改讀 per-unit step_km（取代固定 40）；開啟行軍耗損（距離×地形難度×tempo）；AI 導出 profile（去硬寫 FOOT）+ context 加速度/單回合可達 + decider 指示 | 機械化 vs 徒步同距離 ETA 明顯不同（固定 seed）；長程行軍產生 MOVE_ATTRITION（非強穿）；AI MOVE 用導出 profile；預覽與執行速度一致；**golden 6 重錄綠** |
| #81 (Phase B) | `v_eff` 逐 tick 依地形類別+坡度（mobility_matrix.step_cost）+ weather modifier 調變；不可通行段→停邊界+MOVE_BLOCKED；預覽 estimate_route 採同一速度模型（分段 ETA/耗損） | 同路線穿森林/山地/濕地/上坡明顯慢於開闊/平地（固定 seed 係數比較）；進不可通行地形停邊界+事件；預覽分段 ETA＝執行；**golden 6 重錄綠** |
| #82 (Phase C) ✅路由核心 | 執行改走 get_path A* 路徑（繞開河/山/不可通行）；預覽/執行同一路由；**任意點位起終點**（非 hex 中心 → 首/末部分格幾何段，不吸附格心、停精確終點，SPEC §2.3）；不可達/超出格網→退回直線不誤拒 | ✅ 單位繞開河/山而非直穿；✅ **任意 lat/lng 起終點正確（停精確終點）**；✅ 預覽路徑＝執行路徑；✅ golden 6 未破。worklog: movement-phase-c.md |
| #83 道路網／土地利用 ingestion（OSM） | **#82 未竟項**（資料已備：`/Volumes/M200/Maps/{taiwan.osm.pbf,taiwan_drive.graphml}`，容器已掛載於 `/data`）：terrain `terrain_class` 目前只由坡度+高程導出（URBAN/FOREST 需 OSM 土地利用）；`mobility_matrix` 無 `ROAD` class；`MATSO_ROAD_GRAPH_PATH`（taiwan_drive.graphml）標「尚未使用」。需 OSM PBF 匯入 → 土地利用分類 + 道路網 → 新增 ROAD 成本/加速 | 沿既成道路移動明顯快於越野；森林/市區由土地利用（非坡度）正確分類 |
| #85 補給（加油）✅ | `ResupplySystem` 取代 NoOp logistics：補給車（LOGISTICS capacity.FUEL）2km 內對同陣營目標每 tick 撥交；超距等待不失敗；載運油存 `currentState.cargo_fuel`（惰性滿載）。AI 可下 RESUPPLY。**無契約變更**（協定/OrderType/schema 早已具備） | ✅ 拋錨→補給→重下 MOVE 可再動（e2e）；✅ 拒補他軍/非補給單位；✅ golden 6 未破。**未做：彈藥/水糧/電池撥交**。worklog: logistics-resupply-and-ui.md |
| #86 移動真實化前端顯示 ✅ | 契約補 `MovementPreviewView` 6 欄（後端已回、契約漏宣告）；COP 預覽顯示機動 profile+實際速度、地形繞路、不可通行警告、油料不足警示；單位卡活油料列（0＝拋錨紅字） | ✅ preview API 回全欄；✅ COP 渲染無 console error；✅ 前端 lint/typecheck 綠 |
| #84 油料消耗 ✅ | `EquipmentInstance.currentState.fuel`（惰性滿油，免 migration）；每 tick 夾距離→依實際位移扣油→寫回；油盡 `MOVE_HALTED_FUEL` 停駛（重下令才再動）；徒步不受限。預覽改用真油耗；AI 得知「剩餘行程 N km」 | ✅ 續航 340–480km（MBT/IFV/SP/MLRS）；✅ 油盡拋錨+事件；✅ golden 6 未破。**未做：補給加油（logistics 仍 NoOp）、前端油量顯示**。worklog: movement-fuel.md |
| #88 hex 格網覆蓋擴大／on-demand ✅ | `HexGridCache` 可注入 `HexGridBuilder`：預建 bbox 外的 cell **當場由 DTED 算**並記憶化（未注入 builder → 維持舊 None 語義）；`TerrainService` 於 DTED+快取皆備時自動注入 | ✅ 實測預建外（高雄）取得真地形；✅ 台南→高雄 A* reachable 15 hops（原不可達）；✅ golden 6 未破。**未做：eta_ticks 真實化（刻意；core 為權威 ETA，避免重建第二套速度模型）**。worklog: hexgrid-ondemand.md |

---

## COP 視角／符號／設定／顯示（2026-07-28 使用者回報 7 項）

> **盤點結論（動工前先查，避免重工）**：其中 4 項的後端／機制**早已存在**，缺的是接線或前端，
> 故工作量差距很大（#96 半天、#93 需先做參數清冊）。紅線照舊：**fog 過濾只在後端**（#90 的
> 視角切換必須走後端 `as_faction`，不可前端過濾）；契約先行；一次一張卡。

| 任務 | 現況盤點 | 內容 | 驗收重點 |
|------|----------|------|----------|
| #98 陣營關係矩陣持久化（#91 的前置）✅ | `FactionRelations` 早已寫好、loader 也會建，**但建完就丟**——`WargameSession` 無欄位存。故 sweep 拿不到關係（盟軍互相偵測）、`orchestrator.py:159` 寫死全 HOSTILE（**AI 會打盟軍**）、前端無從得知（`cop.vue` 硬寫 HOSTILE） | `WargameSession.factionRelations Json?`（**可為 NULL＝未宣告＝全 HOSTILE，既有局零遷移**）；`to_triples`/`relations_from_triples`（寬容解析）；`session_store.load_session_relations` 單一入口；loader 開局寫入、clone 複製；sim_runtime 注入 sweep、orchestrator 改讀 | ✅ 遷移前備份 + 逐項核對（3/44/50 筆數不變、既有局皆 NULL）；✅ 宣告 BLUE↔YELLOW 為 ALLIED 後，兩方**互不成為 contact**，RED 仍看得到雙方；✅ 12 新測試、pytest 1079、schema-sync 141 欄。worklog: faction-relations-persistence.md |
| #97 感測器接線（#90/#91 的前置） | **偵測程式碼全都寫好了**：`intel/sensor.py`（SensorProfile/detect_probability/fidelity）、`intel/sweep.py`（H3 k-ring 掃描、關係矩陣過濾、確定性）、`intel/store.py`（per-faction upsert）、`intel/sensor_system.py`（Kernel 接線層）皆已存在且有單元測試。**但 `sim_runtime.py:225` 仍是 `NoOpSensorSystem()`** → `IntelContact` 實測 0 筆。後果：一般陣營指揮官 COP **完全看不到敵人**；白軍則全部當友軍顯示 | 比照 #33 CommsSystem 取代 NoOp 的做法：新增 `engine/sensor_wiring.py`（`SensorResolver` 由裝備導出 SensorProfile + **內建基本目視**讓既有 session 免 seed 即可運作；`make_detect_env` 接地形 LOS + 天氣）→ sim_runtime 換上 `SensorSweepSystem`；RNG 走 `DeterministicRNG(seed, "sensors")` | 跑一局後 `IntelContact` 有資料且**分陣營**；`GET /intel` 各陣營所見不同；敵單位進入感測範圍才出現、超出後保留最後已知位置；**golden 6 不受影響**（golden 自己的 scenarios 用 NoOp，不走 sim_runtime） |
| #90 COP 視角切換（全知者可套各陣營迷霧）✅ | 同左 | COP 加視角下拉（僅全知可見）：全局／各陣營；units+intel 皆帶 `as_faction`。**COP 原本根本沒呼叫 `/intel`**——敵情是拿 `/units` 反推的，故一般陣營角色恆為空、白軍等於用 ground truth；本卡改為一律取後端 fog 過濾後的 contacts。契約先行補上從未宣告的 `/intel` + `ContactView`。 | ✅ 下拉四項（全局＋BLUE/RED/YELLOW）且切換後不縮；✅ 切 RED → 單位清單只剩 RED 13，後端實收 `as_faction=RED`（units 與 intel 皆是）；✅ 地圖實測 **13 own + 22 contact**（＝RED 自有單位＋其偵測所得）；✅ 越權由既有 `test_commander_cannot_view_other_faction` 擋住。worklog: cop-viewpoint.md |
| #91 友軍/敵軍 2525 affiliation 依關係矩陣 ✅ | 同左（前端符號機制與後端矩陣都已有，缺「觀測者對該陣營的關係」這個輸入） | 契約先行 +`/sessions/{id}/relations`（**只回以觀測者為中心的一列**，不洩漏第三方結盟）；cop.vue `relationOf`/`isFriendly` → contacts 帶真關係、友軍列入 `realAsOwn`、**友軍不可被鎖為交戰目標**。**連帶補上盟軍共享視圖**：sweep 一直假定「盟軍經共享視圖非偵測」但 units 從來是嚴格等值過濾，#98 後盟軍會既不在 units 也不在 contacts＝互相隱形。 | ✅ BLUE 視角實測地圖圖徵 own/BLUE aff=F ×13、**own/YELLOW aff=F ×10（盟軍）**、contact/RED aff=H ×13；✅ `/relations?as_faction=BLUE` 回 {BLUE:ALLIED, RED:HOSTILE, YELLOW:ALLIED}；✅ 未宣告關係的局維持只見己方（既有局零行為變更，有測試釘住）；✅ pytest 1084、golden 6 未破。worklog: friendly-enemy-symbology.md |
| #92 地圖標註陣營歸屬與視角過濾 ✅ | 同左（後端欄位與可見性過濾早已完整） | 契約 +`as_faction` 於 listMapFeatures；後端視角過濾（僅全知，一般角色→403）；前端載入帶視角、**繪製時 owner_faction＝當前視角**（否則白軍替某軍畫的東西會落共同層而全體可見）、標註列加歸屬徽章（共同／陣營色點+代號） | ✅ API 實測：全局見 COMMON+BLUE-OP+RED-OP、BLUE 視角無 RED-OP、RED 視角無 BLUE-OP；✅ 測試交叉驗證「白軍 BLUE 視角所見＝BLUE 帳號登入所見」；✅ 瀏覽器徽章與切換收斂皆正確；✅ pytest 1086、golden 6 未破。worklog: map-feature-ownership.md |
| #93 全域參數集中於系統設定 ✅P1（P2/P3 待續） | 同左 | **先產清冊** `docs/PARAMS.md`：分四層 H 熱更新／R 重啟該局／C 冷啟動／**P 需改程式**（P 層才是兵推行為那群，成本在讀取端改寫）。P1 把 P 層核心子集改成可讀設定：新 `sim_params.py`（frozen dataclass，**預設＝原常數**、壞值逐欄退預設）、契約補上**原本完全不在契約裡**的 `/system/config`、執行端於 runner 啟動讀一次（**進行中的局不受影響**）、**預覽端讀同一份**（否則預覽與實跑再度分歧）、設定頁「推演參數」區塊 | ✅ 14 測試（預設等同原常數、壞值逐欄退、往返一致）；✅ pytest 1110、**golden 6 未破**；✅ 容器實測 foot_xc 12→預覽 10.9km/h、改回 5→4.5km/h（比值符合），壞值 -999 退回 4000；✅ 驗完重設回預設。**未做**：P2 韌性/串流、P3 env 逐項標註需重啟、R 層尚未入 UI。worklog: sim-params.md |
| #94 單位圖標上方顯示血量 ✅ | 同左 | 圖標上方血條（**canvas 生成 ImageData，免 glyphs**——同鎖頭徽章紀律，純離線/air-gapped 仍畫得出來；text-field 需 glyphs，無 tileUrl 時整層不出現）。以 5% 為桶（21 張圖而非 101）。**順帶修既有 bug**：白軍被登記為 `WHITE_CELL` 參與者時 `observerFaction` 取到保留字，導致沒有任何單位算我方 →「單位」恆為 0、地圖只剩敵情。 | ✅ 實測 36/36 單位有血條；注入 37/62/8/0 → 桶 35/60/10/0 正確；✅ 修正後表頭由「單位 0」變「單位 36」 |
| #95 攻擊時繪製武器軌跡 ✅ | 同左 | 由**後端已裁決**的 ENGAGEMENT_RESOLVED 驅動；**座標不從事件帶**——`build_event_envelope` 不設 faction 受眾標籤，交戰事件目前是廣播給所有陣營，夾帶座標等於洩漏全場交戰位置。改由前端從已合法可見的圖徵解析端點，解析不到就不畫。HIT 亮橘實線／MISS 灰藍虛線／REJECTED 不畫；4 秒淡出。**零後端改動＝零 golden/迷霧風險**。一併修全局視角單位重複渲染（#90 引入：own 36 + contact 70 疊在一起） | ✅ 四種注入：HIT 畫(0.95)、MISS 畫(0.5)、目標不可見不畫、REJECTED 不畫；✅ 淡出 0.95→0.58→0.11→消失；✅ 截圖可見橘色軌跡與血條。**排查紀錄**：z7 下 34m 的軌跡因小於一像素被向量切圖丟棄（非 bug，z15 正常）。worklog: weapon-tracks.md |
| #96 地圖編輯器線條粗細 ✅ | 同左 | `attributes.width`（Json 欄位 → 免 migration）；`mapfeat-line` 由寫死 2 改為 `['get','width']`；繪製與編輯面板各加線寬滑桿（0.5–12）；`featureLineWidth` 缺值退預設 2 → 既有標註維持原樣 | ✅ 圖層運算式確認為 `["get","width"]`；✅ 建 width=8 的線，圖徵屬性帶 width=8 |
| #99 地圖物件整形（控制點編輯）✅ | 現況只能**整點拖曳點特徵**（#11 B2）與**繞質心旋轉**（#26）：線/面畫錯一個頂點就得刪掉重畫。後端 `PATCH …/map-features/{fid}` 早已收 `geometry`（欄位型別 `Any`）→ **零後端／零契約改動** | 選取線/面時顯示控制點：拖頂點改形狀、拖中點插入新頂點、拖圖形本體整體平移、右鍵控制點刪除（線≥2/面≥3 才准）。幾何純函數集中於 `useMapFeatures`；拖曳中本地預覽、放開才 PATCH，失敗一律重載回權威幾何。**順手修好 #11 B2 自始無效的點拖曳**：`featLayers` 在 onMounted 就 `filter(getLayer)`，但圖層要到 `map.on('load')` 才加 → 過濾成空陣列，那兩層的 mousedown 從沒註冊過 | ✅ 拖頂點只動該點、拖中點插在正確索引、拖本體同位移（皆以 GET 回讀伺服器幾何驗證）；✅ 首尾重合的環（`genCircle`）控制點 4 顆／4 相異位置，不「裂開」；✅ 刪點下限保護（線 2/面 3）+ toast；✅ 點特徵拖曳修復後座標落在放手處。worklog: feature-reshape.md |
| #100 README 全系統文件 + SPEC_V2 差距分析 ✅ | 使用者提供 6 份兵推文獻（JCATS×3/NATO IST-160/MITRE JTLS 聯邦/MASA multi-site/INDSR 特刊全本），要求全系統解剖 + v2 開發藍圖（交 Opus 5 續開發） | workflow 13 agents（6 碼庫 mapper + 7 PDF reader）→ README.md（1556 行，逐檔用途與關係）+ SPEC_V2.md（900 行：35 項差距總表、WP-A~H 工作包、V2.0–2.2 路線圖、Non-Goals、agent 執行守則） | ✅ 兩檔完成；盤點重大發現（AI 敵情 ground truth／G4 空轉／TriggerChecker NoOp／fixed 旗標 roundtrip bug）皆入 SPEC_V2 差距表。worklog: docs-readme-specv2.md |

| WP-A1 AI 敵情接真實情報 ✅ | SPEC_V2 §6 WP-A1 | AI 改走 IntelContact 投影（複用 IntelService）+ 盟軍共享視圖 + recent_events + ai_ground_truth 退回開關 | ✅ 實測 RED 見 22/真實 23、YELLOW 25/26；pytest 1116。worklog: ai-fog-honesty.md |
| WP-A3 G4 禁射區護欄修復 ✅ | SPEC_V2 §6 WP-A3 | 三斷點（欄位不匹配/無資料源/**攔截從未落帳**）；契約+DB+幾何→h3+TargetLocator+人類 override+白軍 UI+想定 loader | ✅ 20 測試、pytest 1136、golden 6 未破；容器實測攔截成功。worklog: g4-no-strike.md |
| WP-G1a cop.vue 拆分：狀態與面板層 ✅ | SPEC_V2 §WP-G 表 G1a | 六個 composable + 三個面板元件；子元件以 `reactive(composable)` 單一 prop 收狀態（非 40 個 prop+emit）；順帶修好**一直空轉的** `npm run typecheck`（`vue-tsc --noEmit` 不跟隨 project references） | ✅ cop.vue 4419→2181；lint/typecheck/build 綠；e2e 與拆分前逐條相同（各 4 failed / 14 passed）；多 agent 等價性稽核確認並修掉 5 個回歸。worklog: cop-decomposition.md |
| WP-G1b cop.vue 拆分：版面層 ✅ | SPEC_V2 §WP-G 表 G1b | 九個版面/面板元件 + 六個 composable；`CopWidget` 把六個小工具共用的 Teleport/FloatingWidget 樣板收成一處（原本重複六次）；順帶清掉 81 行死 CSS（#22 併入圖層小工具後遺留的 `.linewidth-btn`/`.modal*`） | ✅ cop.vue 2181→951（全程 4419→951，−78%）；lint/typecheck/build 綠；`.env`-free worktree e2e 前後比對**逐條相同**（4 failed / 14→16 passed，多的兩條是新增的）；**新增 2 支 e2e**（ctxmenu/coords，原本零覆蓋）；12 塊等價性稽核 + 孤兒掃描 0 回歸。**MapCanvas props 收斂評估後不做**（理由見 worklog）。worklog: cop-decomposition.md |
| WP-C5 通聯後果閉環：位置凍結與敵情粗化 ✅ | SPEC_V2 §6 WP-C5；SPEC_FULL §6.2 | `position_report_*` 與 `intel_granularity` 從「定義了沒人用」接到投影層：CommsSystem 落 `report_lat/lng/tick`（ONLINE 每 interval／DEGRADED ×3／OFFLINE 凍結），`/units`＋STATE_DIFF＋AI context 三路共用同一組純函數；`/intel` 依陣營通聯姿態量化到 h3 res-6、fidelity 上限 DETECTED | ✅ 45 新測試、pytest 1289、golden 6 未破。**順帶修紅線 3 違反**：STATE_DIFF 過去無任何陣營受眾＝敵軍即時座標廣播給所有 client → 改每陣營投影 + `exclusive` 受眾語義。另修 `TacticalUnit.comms_status` 播種後從未被寫、`cop.vue` 的 `currentTick` 寫死 100。worklog: comms-consequences.md |
| WP-E3 /state 原子快照與 RESYNC 閉環 ✅ | SPEC_V2 §6 WP-E3 | 契約補完（StateSnapshotView）+ 端點**複用既有 handler**（過濾一致性由構造保證）+ `/intel` 對齊 + 前端原子重建與 last_seq 去重 | ✅ 12 測試（4 組「快照 == 各端點」參數化）、pytest 1244、golden 6 未破；容器 /openapi.json 實測。worklog: state-snapshot.md |
| WP-B6 想定資產補齊 ✅ | SPEC_V2 §6 WP-B6 | roe.schema + 載入 + **兩個生效點**（裁決層逐武器篩／precheck 早退）；`overrides/` 機動覆寫（值物件注入，禁改可通行性）；匯出無損化（`fixed`/`description`/`display_name`，**前端編輯器過去會靜默刪掉禁射區**）；orbat `equipment`；官方想定補到三個 | ✅ 48 新測試、pytest 1232、golden 6 未破。三想定無損＋位元一致 roundtrip 綠。順帶修 `tutorial-platoon` 用了不存在的 condition type `eliminate`（整局不判勝負）並把 DSL 驗證提前到載入時。worklog: scenario-assets.md |
| WP-E1 活 session checkpoint 與崩潰復原 ✅ | SPEC_V2 §6 WP-E1 | 活局掛 checkpointer（間隔進 SimParams）+ RNG 狀態序列化 + 快照信封 v2（units/rng/orders）+ 重啟自動復原與前滾投影 + ROLLBACK 接活 + `GET /checkpoints`。**規格未列的四個斷點**：SimClock 每次重啟歸零、seed_combat_state 覆寫座標、tick 0 快照覆蓋、`load_latest` 同 ledgerSeq 未定序（實測發現） | ✅ 42 單元 + 2 整合；pytest 1183、golden 6 未破。容器實測 kill -9 後自動復原且雜湊一致、活回滾 7800→7451。規格要求的「Ledger 實體截斷」改為邏輯截斷（ADR 007）。worklog: live-checkpoint.md |

---

## 附錄：任務中斷與續作（額度用完時的保命機制）

1. **worklog 是即時的**：每完成一個實質步驟（一個檔案、一個測試通過、一個決策）就更新 `docs/worklog/O<id>.md`，不是收工才寫。
2. **「中斷續作指引」段落永遠保持最新**：任何時刻被砍掉，下一個 agent 讀該段就能接續。
3. **commit 節奏**：任務內每到一個綠燈點（測試通過的完整小步）就 commit（`wip(scope): O1.2 hash chain 完成，verify CLI 未做`）。任務完成時 squash 與否由使用者決定，預設保留。
4. 接續中斷任務的指令就是再說一次「開發 O1.2」——agent 會從 worklog 的中斷續作指引接手，**不要重做已完成步驟**。
