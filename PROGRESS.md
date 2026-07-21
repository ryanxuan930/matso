# MATSO 進度帳本

> 跨 session / 跨 AI Agent 的唯一進度事實。維護規則見 [HOW_TO.md](HOW_TO.md) §7。

## 目前狀態摘要（3 行內，最新在上）

- 2026-07-22：**M8（AAR）達成——O8.1–O8.4 全數完成**。全由不可變 Event Ledger 推導（§14）：O8.1 **重播服務**（時間軸 frames + 書籤 + 任一 tick 狀態重建，讀權威後態→與 checkpoint 一致）；O8.2 **統計儀表板**（交戰數/命中率/總戰損/護欄攔截/damage_by_faction）；O8.3 **AI 敘事**（fallback 敘事 + **引用查核**：cited seq 100% 存在，捏造被抓）；O8.4 **匯出** JSON/CSV + **匿名化**（單位真名→UNIT-N、去 CoT）。`core/app/aar/` + `/aar/{replay,stats,report,export}` 端點（限參與者/ANALYST/全知）+ AAR 儀表板頁。**689 passed**（AAR 13）、mypy 155、前端 build 綠。真 AAR_ANALYST 模型接線屬部署層。branch `feat/o8-aar`。
- 2026-07-22：**M7（O7 想定與白軍）達成——O7.1–O7.5 全數完成**。O7.3 想定編輯器（匯出/匯入 roundtrip，Python dump+load 等價測試 + 前端編輯頁）；O7.4 **白軍控制台**（`?as_faction` 視角切換[限全知]、時間控制 `/control` PAUSE/RESUME/ROLLBACK[限 White Cell]、注入、事件流）；O7.5 **RBAC 完整化**——**7 角色×4 端點存取矩陣 contract test** + 收口 intel 端點安全漏洞（原**未認證 + 信任 client faction**→改認證+主體推導+越權 403）+ 統一 is_white_cell/is_omniscient。**676 passed**、mypy 148、前端 lint/typecheck/build 綠。O7 分支疊在 main（含 M6+多陣營）上未推。剩餘掛帳：refresh 撤銷（C5）、建局角色 gate（C8）、kernel↔MSEL/control/relations 部署接線。下一步 **M8 AAR**。
- 2026-07-21：**O7.1 + O7.2 完成（想定管理起步）**。O7.1 scenario schema 補完（factions/relations/orbat/victory 任意陣營）+ **loader**（JSON Schema + 語意驗證、**精確錯誤路徑**、建 FactionRelations、`create_session_from_scenario` 開局建 session/units）+ 官方想定 **tutorial-platoon**。O7.2 **MSEL 觸發引擎**（condition DSL：time/faction_eliminated/strength_below/unit_in_region/all/any，與 victory 共用；`MselEngine` 邊緣觸發 fire-once 實作 TriggerChecker）+ **ad-hoc inject 端點**（`POST /sessions/{id}/inject`，限 White Cell）。**638 passed**、mypy 146。M6+多陣營已合併 main（PR #5，CI 綠）。O7.1/O7.2 分支疊在 main 上未推。**kernel 綁 MselEngine/victory + relations 熱狀態 → O7.4/部署層**。
- 2026-07-21：**N 方對抗 + 關係矩陣全數實作（O6.7–O6.10）**。faction 由 enum→想定定義字串 id（O6.7，含契約漂移修正 + migration）；**FactionRelations 關係矩陣**（O6.8，ALLIED/NEUTRAL/HOSTILE、對稱、未宣告預設 HOSTILE、局中調整→FACTION_RELATION_CHANGED 事件）為敵我判斷單一權威——整合進 intel sweep（ALLIED 不成 contact）、ENGAGE 預檢（只能打 HOSTILE，否則 ORDER_ROE_VIOLATION）；聚合裁決泛化（O6.9，(force_a,force_b) + N 方 HOSTILE 配對逐一裁決，golden 免重錄）；前端 affiliation 由關係推導 + faction 顏色（O6.10）。**驗收：黃軍同時見紅藍、盟軍互不偵、BLUE 打己方被 ROE 攔、三方混戰合圍者掉血最多**。617 backend passed、mypy 142、18 E2E 綠。分支 feat/o6.7..o6.10 疊在 feat/o6-ai-modes-spec 上。剩：kernel↔多方接線、scenario 宣告 factions/relations（O7.1）、White Cell 調關係（O7.4）。
- 2026-07-21：**多陣營設計定案（ADR 006 + SPEC §12.1 + TASKS O6.7–O6.10）**。盤點結論：偵測/fog-of-war/faction-scoped API/單位級裁決**已 N 方友善**；寫死兩軍的 blocker＝victory_conditions enum[BLUE,RED]、聚合裁決 blue/red 參數與事件欄、封閉 Faction enum（且 core_api 契約漂移 WHITE/GREEN）、無敵友關係模型、前端二元敵我。設計：**faction=想定定義字串 id**（WHITE_CELL 保留字）+ **關係矩陣 ALLIED/NEUTRAL/HOSTILE**（對稱、未宣告預設 HOSTILE、White Cell 局中調整→FACTION_RELATION_CHANGED 事件）；敵我判斷收斂單一關係服務（新紅線）。O7.1 依賴 O6.7。聚合泛化需 golden 重錄（O6.9）。
- 2026-07-21：**M6 AI Phase 1 全數完成（O6.1–O6.6）**——**RAG/eval 皆空下仍完整可運作**。鏈路：AI 模式（§9.0 AI_OFF 傳統兵推/AI_BARE 無 RAG/AI_FULL）→ client+RoleManager（O6.1）→ Guardrail Gateway G1–G6（O6.2，core、不可 bypass、G5 模式感知、攔截入 Ledger）→ RAG 空庫降級（O6.3，Qdrant+doctrine_general）→ prompts+eval runner（O6.4，§19.4）→ OPFOR 迴路 decider→護欄→物理預檢→fallback（O6.5，無繞過物理特權）→ eval 進 CI（O6.6，空庫 schema-only 綠 + 真模型手動 workflow）。全套 589 passed、mypy 140、guardrails 98%。分支 feat/o6.2..o6.6 疊在 feat/o6-ai-modes-spec 上，未推。**部署層待接**：真 vLLM/bge-m3/Qdrant 服務、kernel↔迴路、WargameSession.aiMode 欄（O7.4）。
- 2026-07-21：**O6 設計調整（資料現實）+ code review 修復**。(1) **SPEC_FULL §9.0 新增 AI 運作模式**：`AI_OFF`（傳統兵推，預設）/`AI_BARE`（無 RAG，模型自身判斷，引用必空）/`AI_FULL`；空語料自動降級、G5 模式感知、§19.4 條件式 eval gate（案例空→schema-only+警告）。(2) **RAG collections +`doctrine_general`**（可得來源為 FM/CNAS/DTIC 等無法歸紅藍的公開文獻；紅/藍可長期為空）。(3) **SPEC_INGEST.md 新規格**：文檔轉換子系統（PDF/掃描→OCR→staging→人工審核 promote→corpus），TASKS 新增 **O9.1–O9.3**（獨立可平行）。(4) O6.2–O6.6 卡片內容依模式感知更新。(5) **CODE_REVIEW.md**（M4/O6.1 審查）14 項已修（C1 跨陣營取消、C2 WS 訂閱間隙、C3 seq 原子化 Lua…），C5/C8 歸 O7.5；544 passed 全綠已推送（`0ac48cd`）。
- 2026-07-21：**O6.1 完成（M6 AI Phase 1 起點）**。branch `feat/o6.1-role-manager`（從 main）。AI 推論地基（SPEC §9.1）：**OpenAI-compatible client**（httpx；`OPENAI_BASE_URL`/`OPENAI_API_KEY`/`MATSO_LLM_MODEL` 全 env 注入；本機 vLLM 指向即可，air-gapped/CI 用 **ReplayClient 錄放 mock** 免 GPU/網路）+ **RoleManager**（5 角色註冊表、**priority 分組佇列 OPFOR 最高**、adapter 熱切換攤銷、注入時鐘量 latency）+ **InvocationLogWriter**（AIInvocationLog 全記錄，注入 sessionmaker、no-op 當無 DB）。程式碼落 `ai/matso_ai/`（workspace 已安裝）。新增 core/app/py.typed（讓 ai 跨包 import 於 mypy strict 解析）。13 測試（佇列優先/FIFO/批次攤銷=2/注入時鐘/錄放 roundtrip/MockTransport 離線/log 落地）。**535 passed**（+13）、mypy 122 clean、ruff、schema-sync 綠。**未接真 vLLM/未產 fixtures**（部署層）。worklog: docs/worklog/O6.1.md。
- 2026-07-21：**O4.6 完成 → M4（COP 前端）里程碑達成**。branch `feat/o4.6-e2e-ci`（從 main）。E2E 煙霧測試 + 進 CI：cop 頁接 **useSessionStream**（O4.3 WS）+ 戰況事件面板；後端下令成功（STUB_GATEWAY）發 EVENT 到 Redis stream（publish_event）。**Playwright smoke：登入 → COP（WS live）→ 下 ENGAGE 令 → precheck 可行 → 收到 ENGAGEMENT_RESOLVED 事件（經 WS）**。新增 **CI e2e job**（redis service + chromium + playwright）。修 WS 短 TTL 競態（連線前 refreshAccessToken）。全 18 E2E 綠；522 passed。worklog: docs/worklog/O4.6.md。
- 2026-07-21：**O4.5 完成**。branch `feat/o4.5-order-ux`（從 main）。下令 UX（SPEC §13.4）：後端 orders 端點改**由 token 推導 issuer**（SPEC §12 不信任前端，非參與者 403）+ GET /orders 列表 + **GET /units**（faction-scoped，下令對象）+ E2E `_StubGateway`（STUB_GATEWAY=1，precheck 可行，讓下令全流程無 terrain 亦可 E2E）。前端 useOrders + cop 指令面板：選單位 → **MOVE 點地圖設目標 / ENGAGE 選目標** → **precheck 顯示** → pending/歷史 + 取消。**Playwright 3 order E2E**（下 MOVE 令全流程：選單位→點地圖→precheck 可行→pending→取消；ENGAGE），全 17 E2E 綠。521 passed。worklog: docs/worklog/O4.5.md。
- 2026-07-21：**O4.4 完成**。branch `feat/o4.4-units-fog`（從 main）。COP 單位渲染 + fog of war（SPEC §13.2/§13.3）：milsymbol 生成 MIL-STD-2525 符號（文字烤入 icon 免 glyphs，離線）→ MapLibre symbol 層（資料驅動 icon-image/opacity、icon 依 SIDC 去重快取）。**Fog of war**：己方友軍符號、**OFFLINE 虛影**（最後位置 + 經過時間 + 淡化 0.4）；敵方 contact 依情報等級 DETECTED（未知 U）→ CLASSIFIED（疑敵 S）→ IDENTIFIED（敵 H + 番號），時效遞減透明度。cop 頁 ?units=N 合成 + 三級 contact demo。**Playwright 4 綠（含 500 單位 render FPS headless=122，≥30 達標）**，全 14 E2E 綠。除 milsymbol ESM/addImage/tiling 三 bug。worklog: docs/worklog/O4.4.md。
- 2026-07-21：**O4.3 完成**。branch `feat/o4.3-ws-stream`（從 main）。即時串流（SPEC §16.2、ws_protocol.md）：後端 WS 端點 `WS /sessions/{id}/stream?token=`——token 認證 + **faction-scope 過濾**（非參與者且非全知→拒）+ HELLO/last_seq **範圍檢查補償**（O1.7/R7：缺口/倒退→RESYNC_REQUIRED，否則從 ring backfill）+ **背壓斷線**（BoundedSender 上限 1000，慢 client 溢出→close 4408）。前端 useSessionStream Pinia store（HELLO 帶 last_seq、RESYNC→GET /state、指數退避重連）。純模組（backfill/faction/sender/identity）100%；25 測試（含 fakeredis WS 端到端：重連補齊/RESYNC/auth/faction）。515 passed / cov 97.33%。worklog: docs/worklog/O4.3.md。
- 2026-07-21：**O4.2 完成**。branch `feat/o4.2-map-base`（從 main）。COP 地圖基座（SPEC §13.2）：MapLibre GL 地圖元件（ClientOnly + maplibre 動態 import 避 SSR）+ H3 六角網格層（h3-js 客戶端計算，視野 bbox→cell，開關）+ 經緯網格 + hillshade 層（tileUrl 有設時）+ 圖層開關 + COP 頁（/session/:id/cop，lobby 可點入）。**離線第一**：無 tile server / 無網路仍以背景+經緯網格+hex 渲染（air-gapped）。tileserver-gl 進 compose（profile "tiles"，env .mbtiles 注入、預設不啟動、真瓦片在 M200）。**Playwright 5 綠**（初始化置中台灣 / 離線 hex 仍算 / hex 開關 / 平移縮放 / lobby→cop），全 10 E2E 綠。除 SSR/水合/viewport 三個環境特有 bug。worklog: docs/worklog/O4.2.md。
- 2026-07-21：**O4.1 完成（M4 前端起點）**。branch `feat/o4.1-auth-lobby`（**從 main 開，與 M5 平行**）。全端認證 + lobby：後端 Argon2id 密碼 + JWT（access 短效/refresh，注入時鐘→決定性測試、type 檢查、帳號列舉防護）+ /auth/login|refresh|logout|me + GET/POST /sessions（角色/參與過濾）+ CORS + get_current_user bearer 依賴；契約先行 core_api.yaml。前端 Nuxt 4 + Pinia：login/lobby 頁 + auth store + 路由守衛 + useApi（bearer + 401 自動 refresh）+ 由契約生成型別。**Playwright E2E 5 綠**（登入→lobby、錯誤密碼被拒、access 過期自動 refresh）。除 3 個真 bug：埠衝突（改 8100/3100）、cookie-ref 競態（refs 記憶於 nuxtApp）、水合競態（data-hydrated 標記）。後端 393 passed；auth/lobby 近 100%。worklog: docs/worklog/O4.1.md。
- 2026-07-21：**O5.4 完成 → M5 里程碑達成**。branch `feat/o5.4-comms`（stack 於 o5.3）。Comms/EW 模組（`modules/comms/`，套 _sdk）：鏈路預算 `margin=tx+gains−FSPL−obstruction−weather−jamming`（§6.1）+ margin→ONLINE/DEGRADED/OFFLINE（6/0 dB）+ **networkx mesh 兩級子圖連通至指揮錨點、孤島→OFFLINE**。Core `app/comms` 強制 **§6.2 三列戰術後果**（ONLINE 即時 / DEGRADED 指令延遲 N ticks+回報降頻+敵情粒度下降 / OFFLINE 拒收+COP 位置凍結）+ CommsClient（非硬依賴→全 ONLINE 降級）。契約先行 comms.proto。46 測試（§6.2 逐列 + mesh + 端到端真插件 gRPC）；comms 核心 100%。453 passed；docker 容器 healthy。worklog: docs/worklog/O5.4.md。
- 2026-07-21：**O5.3 完成**。branch `feat/o5.3-weather-integration`（stack 於 o5.2）。天氣效果整合：Core `app/weather`（CellEffects/WeatherState + 效果→env weather_modifier 映射）+ WeatherClient（gRPC→WeatherState；非硬依賴，不可達→CLEAR 降級）。**驗收：暴雨 vs 晴天同交戰 p_hit=晴×0.4、200 次命中數差>30；偵測/聚合亦可觀測不同**。13 測試；app/weather + weather_client 100%。407 passed。worklog: docs/worklog/O5.3.md。
- 2026-07-21：**O5.2 完成**。branch `feat/o5.2-weather-live`（stack 於 o5.1）。Weather LIVE 模式：CwaHttpSource 拉 CWA → 最近測站格網化 → LIVE payload；**斷網→stale=true（保留最後有效值）、恢復→自動回 LIVE**、stale>30min 告警；stale→插件 DEGRADED（SPEC §16.3）。WeatherProvider 介面統一 SYNTHETIC/LIVE。24 測試（狀態機/格網化/CWA 解析）。394 passed / cov 97.33%。worklog: docs/worklog/O5.2.md。
- 2026-07-21：**O5.1 完成**。branch `feat/o5.1-weather-skeleton`（**從 main 開**——M1–M3 已 fast-forward 合併回 main）。Weather 模組骨架套 _sdk：SYNTHETIC 關鍵影格插值（線性 + 風向最短角）→ effects 映射 → 符合 weather_payload.schema.json 的格網化效果係數。weather.proto（契約先行）+ WeatherPlugin/Service + compose 服務（50052）。23 測試（插值/effects/schema/harness）；weather 近 100%。379 passed。worklog: docs/worklog/O5.1.md。
- 2026-07-21：**M0–M3 已合併回 main + CI 全綠**（default branch 改為 main；修復 3 個 CI 問題：benchmark 效能測試 CI 排除、terrain Dockerfile libexpat1、core Dockerfile gen_proto）。
- 2026-07-21：**O3.6 完成 → M3 里程碑達成**。branch `feat/o3.6-scripted-battle`。腳本對戰 DoD：真 Kernel 組裝 + 純 API 驅動，把 O3.1–O3.5 全接起來——藍軍移動 → 紅軍偵測到 → 交戰 → 紅血 100→60 + ENGAGEMENT_RESOLVED 入 Ledger → 雙方 intel 視圖各自成立（fog of war 隔離）。新增 kernel 接線 EngageOrderSource/EngagementAdjudicator/SensorSweepSystem。DoD 測試常駐 CI（本地 SQLite + 注入假件）。360 passed。worklog: docs/worklog/O3.6.md。
- 2026-07-20：**O3.5 完成**。branch `feat/o3.5-lanchester`。聚合裁決 `resolve_aggregate_tick`（SPEC §7.1 末段，純同步純函數）：營級以上用隨機化 Lanchester（square/linear 混合 × 隨機化）逐 tick 遞減雙方戰力，**戰損夾 [0,當前戰力] → 能量守恆**。should_aggregate（閾值 = scenario aggregate_adjudication_level）。9 property 測試（能量守恆 Hypothesis、同 seed 同結果、強者勝、湮滅夾 0）；aggregate.py 100%。355 passed。worklog: docs/worklog/O3.5.md。
- 2026-07-20：**O3.4 完成**。branch `feat/o3.4-movement`。移動執行 MovementSystem（step=admit+advance）：MOVE order（VALIDATED）→ terrain path → 逐 tick 推進單位位置（hot_state, single-writer）+ 油料 stub；抵達→COMPLETED、地形中斷→停斷點+MOVE_INTERRUPTED、油盡→HALTED_FUEL。DbOrderStore（狀態機轉移，from_h3 由 DB 座標推導）+ TerrainClientPlanner。**驗收整合測試通過**（下 MOVE 令→N ticks→位置=終點+order COMPLETED）。13 測試、movement ~100%。346 passed。worklog: docs/worklog/O3.4.md。
- 2026-07-20：**O3.3 完成**。branch `feat/o3.3-intel-sensor`。偵測 + per-faction intel store（SPEC §7.2/§13.3）：sensor sweep（**H3 k-ring 空間預過濾 O(N²)→近線性**，與暴力全配對等價驗證）、DETECTED→CLASSIFIED→IDENTIFIED 分級、IntelStore（faction-scoped 強制）、ContactView 去識別化投影、GET /intel?faction=。**RED 拿不到 BLUE ground truth contract test 進 CI 常駐**。29 測試、intel 模組 100% 覆蓋。333 passed。worklog: docs/worklog/O3.3.md。
- 2026-07-20：**O3.2 完成**。branch `feat/o3.2-engagement`。交戰裁決 `resolve_engagement`（SPEC §7.1，**純同步純函數、AI 永不裁決物理**）：合法性→P_hit（乘法係數，夾 [0,1]）→ 確定性擲骰（注入 RNG）→ 傷害 → ENGAGEMENT_RESOLVED 事件。資料驅動（WeaponProfile ← baseStats，對 weaponeering.schema.json 驗證）+ 3 種 KINETIC 種子模板。23 測試（Hypothesis property：距離↑P_hit 不增、係數=1 退化、彈藥=0 REJECTED）；adjudication 覆蓋率 98%。305 passed。worklog: docs/worklog/O3.2.md。
- 2026-07-20：**O3.1 完成（M3 起點）**。branch `feat/o3.1-order-pipeline`。Order pipeline：`POST/DELETE /sessions/{id}/orders`、狀態機（唯一權威 + 非法轉移防護）、validator（單位/權限/語法）、物理預檢（PhysicsGateway 注入，MOVE 可達/ENGAGE LOS，不可行 422 REJECTED、terrain down 503）、統一錯誤格式。契約先行（core_api.yaml Order schema + error code enum）。**ledger 指令序列重播 golden（R10）**。36 測試（含 schemathesis 契約 fuzz）。281 passed / cov 96.31%。worklog: docs/worklog/O3.1.md。
- 2026-07-20：**O2.5 完成 → M2 里程碑達成**。branch `feat/o2.5-terrain-plugin`。Terrain 插件化：`modules/_sdk`（MatsoPlugin base + gRPC server 樣板 + graceful shutdown + 測試 harness）、terrain 套 SDK（TerrainService 5 RPC 委派純函數 + 進入點 + Dockerfile）、Core client（斷路器 + 健檢監視器 3-strikes → DOWN → PAUSE session）、compose terrain 服務。**離線 codegen（grpcio-tools，ADR 005，產物不入 git）**。244 passed / cov 96.55% / mypy(46)。真子行程 smoke + realdata 通過。worklog: docs/worklog/O2.5.md。
- 2026-07-20：**O2.4 完成**。branch `feat/o2.4-astar-path`。A* 路徑 `get_path(cache,from,to,profile)`——成本以 `contracts/mobility_matrix.json` 契約公式（`-1=不可通行`）、admissible heuristic（保證最佳）、確定性破同分。查詢只吃 HexGridCache（parquet，不需硬碟）。22 測試（Dijkstra 交叉驗證最佳性 + 繞行/牆/水陸 + realdata）。206 passed / coverage 96.81%。worklog: docs/worklog/O2.4.md。
- 2026-07-19：**O2.3 完成**。branch `feat/o2.3-los-viewshed`。check_los（大圓取樣 + 4/3 等效地球半徑曲率 + AGL；全線最小餘隙 + 遮蔽點）、get_viewshed（radius 內 h3 cell）。效能關鍵：DtedMap.line_sampler 整線 bbox 一次讀入記憶體 → check_los p99<20ms、viewshed p99<200ms（真檔驗證）。玉山遮蔽測試通過。GRASS 對照骨架（release-gated）。184 passed / coverage 96.81%。worklog: docs/worklog/O2.3.md。
- 2026-07-19：O2.2（hex grid+parquet 快取）、O2.1（DTED+路徑注入）、O1.7（review 修復）完成。
- 2026-07-18：M1 里程碑達成。下一步：**O2.4**（A* 路徑）。

## 任務板

| 卡號 | 狀態 | 認領者 | PR | 備註 |
|------|------|--------|----|------|
| M0-1 | DONE | Claude (2026-07-18) | — | uv workspace（root venv, ADR 001）；ruff+mypy strict+pytest；pre-commit 已 install；前端 eslint(@nuxt/eslint)+vue-tsc |
| M0-2 | DONE | Claude (2026-07-18) | — | buf STANDARD 通過（proto 移至 contracts/proto/matso/*/v1/）；JSON Schema metaschema ✓；OpenAPI 3.1 ✓ |
| M0-3 | DONE | Claude (2026-07-18) | — | compose：mariadb(3307)/redis/qdrant/core/frontend 全 healthy；`up -d --wait` exit 0 |
| M0-4 | DONE | Claude (2026-07-18) | — | init migration 已套用（db/prisma/migrations/20260718025607_init）；schema_sync_check.py：15 tables/118 columns 一致 |
| M0-5 | DONE | Claude (2026-07-18) | — | .github/workflows/ci.yml：python/frontend/contracts/schema-sync/integration 五 job（**未在真 GitHub 跑過**，首次 push 後要盯） |
| O1.1 (M1-1) | DONE | Opus 4.8 (2026-07-18) | branch feat/o1.1-simclock-rng | SimClock + DeterministicRNG；23 測試綠；numpy 2.5.1 |
| O1.2 (M1-2) | DONE | Opus 4.8 (2026-07-18) | branch feat/o1.2-ledger-hashchain (stacked) | LedgerWriter append-only + hash chain；verify_ledger CLI；grant SQL；config/db 基礎；45 passed（2 整合） |
| O1.3 (M1-3) | DONE | Opus 4.8 (2026-07-18) | branch feat/o1.3-kernel-tick-loop (stacked) | Kernel tick loop + 子系統 Protocol/stub + TICK_OVERRUN（注入式牆鐘）；14 測試 |
| O1.4 (M1-4) | DONE | Opus 4.8 (2026-07-18) | branch feat/o1.4-hot-state-diff (stacked) | Redis 熱狀態 single-writer + compute_diff + RedisBroadcaster（ring buffer 5000）+ Kernel drain/broadcast；20 測試（6 Redis 整合） |
| O1.5 (M1-5) | DONE | Opus 4.8 (2026-07-18) | branch feat/o1.5-checkpoint-recovery (stacked) | ADR 002 + zstd checkpoint + recover + rollback（ROLLBACK 事件）+ Kernel 每 N ticks；20 測試（4 崩潰復原整合） |
| O1.6 (M1-6) | DONE | Opus 4.8 (2026-07-18) | branch feat/o1.6-golden-replay (stacked) | **M1 達成**。golden replay harness + 2 goldens + drift 偵測 + rerecord 工具；4 golden 測試 |
| O1.7 | DONE | Fable 5 (2026-07-19) | branch feat/o1.7-review-fixes (stacked) | code review 全數修復：ledgerSeq 錨定 + rollback 修正、TickPacer 降頻、detail 欄（migration）、CI 整合真跑 + coverage 96.77%、errors.py、Redis 批次化；130 單元 + 16 整合測試 |
| O2.1 (M2-1) | DONE | Fable 5 (2026-07-19) | branch feat/o2.1-dted (stacked) | DtedMap + `MATSO_DTED_PATH` 注入 + 合成夾具；真檔 realdata SLA 已驗證通過 |
| O2.2 (M2-2) | DONE | Opus 4.8 (2026-07-19) | branch feat/o2.2-hexgrid (stacked) | H3 hex grid 預計算→parquet 快取（查詢不需硬碟）+ classify_terrain + CLI；17 測試 + 真檔驗證（玉山 3947m） |
| O2.3 (M2-3) | DONE | Opus 4.8 (2026-07-19) | branch feat/o2.3-los-viewshed (stacked) | check_los（大圓+4/3曲率+AGL）+ get_viewshed；line_sampler 一次讀入記憶體達 p99；17 測試（含玉山遮蔽真檔）+ GRASS 對照骨架 |
| O2.4 (M2-4) | DONE | Opus 4.8 (2026-07-20) | branch feat/o2.4-astar-path (stacked) | A* `get_path`：契約成本公式 + admissible heuristic（最佳）+ 確定性；22 測試（Dijkstra 交叉驗證 + 繞行/牆/水陸 + realdata）；查詢不需硬碟 |
| O2.5 (M2-5) | DONE | Opus 4.8 (2026-07-20) | branch feat/o2.5-terrain-plugin (stacked) | **M2 達成**。_sdk（MatsoPlugin + gRPC 樣板 + harness）+ terrain 插件（5 RPC + Dockerfile）+ Core client（斷路器 + 健檢 3-strikes→DOWN→PAUSE）+ compose；離線 codegen（ADR 005）；38 新測試 |
| O3.1 (M3-1) | DONE | Opus 4.8 (2026-07-20) | branch feat/o3.1-order-pipeline (stacked) | Order pipeline：REST POST/DELETE + 狀態機 + validator + 物理預檢（gateway 注入）+ 統一錯誤；契約先行；ledger 指令序列重播 golden（R10）；36 測試（含 schemathesis） |
| O3.2 (M3-2) | DONE | Opus 4.8 (2026-07-20) | branch feat/o3.2-engagement (stacked) | 交戰裁決 resolve_engagement（純同步純函數，§7.1 五步）+ WeaponProfile（資料驅動）+ 3 種 KINETIC 種子；Hypothesis property（單調/退化/彈藥0）；adjudication cov 98% |
| O3.3 (M3-3) | DONE | Opus 4.8 (2026-07-20) | branch feat/o3.3-intel-sensor (stacked) | 偵測 + per-faction intel store：sensor sweep（H3 k-ring 預過濾，vs 暴力等價）+ DETECTED→CLASSIFIED→IDENTIFIED + faction-scoped 查詢/去識別化；RED≠BLUE ground truth contract test（CI 常駐）；intel 100% |
| O3.4 (M3-4) | DONE | Opus 4.8 (2026-07-20) | branch feat/o3.4-movement (stacked) | 移動執行 MovementSystem（admit+advance）：MOVE→path→逐 tick 推進 + 油料 stub；抵達/地形中斷/油盡；DbOrderStore 狀態機轉移 + TerrainClientPlanner；驗收整合測試通過；13 測試 |
| O3.5 (M3-5) | DONE | Opus 4.8 (2026-07-20) | branch feat/o3.5-lanchester (stacked) | 聚合裁決 resolve_aggregate_tick（隨機化 Lanchester，純同步純函數）+ should_aggregate（閾值）；能量守恆 property（Hypothesis）+ 同 seed 同結果；aggregate.py 100% |
| O3.6 (M3-6) | DONE | Opus 4.8 (2026-07-21) | branch feat/o3.6-scripted-battle (stacked) | **M3 達成**。腳本對戰 DoD：真 Kernel + 純 API 驅動全流程（移動→偵測→交戰→戰損入帳→intel 各自成立）；kernel 接線 EngageOrderSource/EngagementAdjudicator/SensorSweepSystem；DoD 常駐 CI |
| O4.1 (M4-1) | DONE | Opus 4.8 (2026-07-21) | branch feat/o4.1-auth-lobby (從 main) | 認證 + lobby：後端 Argon2id + JWT（access/refresh，注入時鐘）+ /auth/* + /sessions（角色過濾）+ CORS；前端 Nuxt+Pinia login/lobby + 路由守衛 + useApi（自動 refresh）+ 契約生成型別；**Playwright 5 綠**（登入→lobby/錯誤密碼/refresh）；56 後端測試 |
| O4.2 (M4-2) | DONE | Opus 4.8 (2026-07-21) | branch feat/o4.2-map-base (從 main) | COP 地圖基座：MapLibre GL 元件（ClientOnly + 動態 import）+ H3 hex 層（h3-js 客戶端計算，開關）+ 經緯網格 + hillshade（tileUrl 有設）+ cop 頁；**離線第一**（無 tile server 仍渲染，air-gapped）；tileserver-gl 進 compose（profile "tiles"）；Playwright 5 綠 |
| O4.3 (M4-3) | DONE | Opus 4.8 (2026-07-21) | branch feat/o4.3-ws-stream (從 main) | WS stream 端點（token 認證 + faction 過濾 + HELLO/last_seq **範圍檢查補償** R7 + 背壓斷線 4408）+ useSessionStream Pinia store（RESYNC→GET state/指數退避重連）；純模組 100% + fakeredis WS 端到端；25 測試 |
| O4.4 (M4-4) | DONE | Opus 4.8 (2026-07-21) | branch feat/o4.4-units-fog (從 main) | 單位渲染 + fog of war：milsymbol MIL-STD-2525（文字烤入 icon，離線）→ MapLibre symbol 層；OFFLINE 虛影 + intel 三級 contact（DETECTED/CLASSIFIED/IDENTIFIED）+ 時效透明度；**500 單位 render FPS headless=122（≥30 達標）**；Playwright 4 綠 |
| O4.5 (M4-5) | DONE | Opus 4.8 (2026-07-21) | branch feat/o4.5-order-ux (從 main) | 下令 UX：orders 端點 auth 化（token 推導 issuer）+ GET orders/units（faction-scoped）+ E2E stub gateway；前端 cop 指令面板（MOVE 點地圖 / ENGAGE 選目標 + precheck 顯示 + pending/取消）；Playwright 3 order E2E（下 MOVE 全流程） |
| O4.6 (M4-6) | DONE | Opus 4.8 (2026-07-21) | branch feat/o4.6-e2e-ci (從 main) | **M4 達成**。E2E 煙霧（登入→COP→WS live→下 ENGAGE 令→precheck→**收到 ENGAGEMENT_RESOLVED 事件經 WS**）+ CI e2e job（redis + chromium + playwright）；cop 接 useSessionStream + 事件面板；publish_event；18 E2E 綠 |
| O5.1 (M5-1) | DONE | Opus 4.8 (2026-07-21) | branch feat/o5.1-weather-skeleton | Weather 骨架套 _sdk：SYNTHETIC 關鍵影格插值 + effects 映射 + weather.proto + 插件/compose；23 測試（插值/schema 驗證）；weather 近 100% |
| O5.2 (M5-2) | DONE | Opus 4.8 (2026-07-21) | branch feat/o5.2-weather-live (stacked) | CWA LIVE 模式：CwaHttpSource + 格網化 + stale 狀態機（斷網→stale、恢復→LIVE、30min 告警）+ stale→DEGRADED；WeatherProvider 統一介面；24 測試 |
| O5.3 (M5-3) | DONE | Opus 4.8 (2026-07-21) | branch feat/o5.3-weather-integration (stacked) | 天氣效果整合：app/weather（效果→env weather_modifier 映射）+ WeatherClient（非硬依賴→CLEAR 降級）；驗收暴雨 vs 晴天可觀測不同；13 測試；100% |
| O5.4 (M5-4) | DONE | Opus 4.8 (2026-07-21) | branch feat/o5.4-comms (stacked) | **M5 達成**。Comms/EW：鏈路預算（§6.1 公式 + 6/0 dB 門檻）+ networkx mesh 兩級子圖連通至指揮錨點（孤島→OFFLINE）；Core 強制 §6.2 三列後果（指令延遲/COP 凍結/敵情粒度）+ CommsClient（非硬依賴→全 ONLINE）；46 測試（§6.2 逐列 + 端到端真插件）；docker healthy |
| O6.1 (M6-1) | DONE | Opus 4.8 (2026-07-21) | branch feat/o6.1-role-manager (從 main) | AI 推論地基：OpenAI-compatible client（httpx, env 注入 `OPENAI_BASE_URL`；air-gapped/CI 用 ReplayClient 錄放 mock）+ RoleManager（5 角色註冊表、priority 分組佇列**OPFOR 最高**、adapter 攤銷、注入時鐘）+ InvocationLogWriter；13 測試；+core/app/py.typed |
| O6.2 (M6-2) | DONE | Opus 4.8 (2026-07-21) | branch feat/o6.2-guardrails | AI 模式（§9.0 AI_OFF/BARE/FULL）+ Guardrail Gateway G1–G6（core，不可 bypass；G5 模式感知；攔截→GUARDRAIL_INTERVENTION）；21 測試/guardrails 覆蓋 98%；契約 +AI_DISABLED |
| O6.3 (M6-3) | DONE | Opus 4.8 (2026-07-21) | branch feat/o6.3-rag | RAG 管線（入庫 CLI markdown-only + Qdrant 6 collection 含 doctrine_general + HashEmbedder/bge-m3 惰性 + QdrantCitationVerifier）；**空庫合法**（index_empty→降級）；7 測試（roundtrip+空庫）；+qdrant-client |
| O6.4 (M6-4) | DONE | Opus 4.8 (2026-07-21) | branch feat/o6.4-prompts-evals | 5 角色 prompt（模式適配）+ output schema $defs（intel/aar/whitecell）+ eval runner（§19.4 四門檻，FallbackResponder，空庫→schema-only+警告）；8 測試；`python -m matso_ai.evals.run` |
| O6.5 (M6-5) | DONE | Opus 4.8 (2026-07-21) | branch feat/o6.5-opfor-loop | OPFOR 自主迴路 run_opfor_turn（decider→護欄→物理預檢→重試≤2→fallback；AI_OFF 拒啟動＝傳統兵推；無繞過物理特權）；7 測試（含 AI_OFF/no-strike→fallback/可重現）|
| O6.6 (M6-6) | DONE | Opus 4.8 (2026-07-21) | branch feat/o6.6-eval-ci | **M6 達成**。eval gate 進 CI（python job +step，fallback mock；空庫→schema-only+EVAL_CORPUS_EMPTY 綠）+ 真模型手動 workflow；CLI exit-code 測試 |
| M7-1 ~ M7-5 | TODO | — | — | |
| M8-1 ~ M8-4 | TODO | — | — | |

## M0 驗證紀錄（2026-07-18）

ruff check / ruff format / mypy strict / pytest(1 passed 1 skipped) / replay placeholder /
buf lint / check-jsonschema metaschema / openapi-spec-validator / schema_sync_check(15 tables, 118 cols) /
pre-commit install / eslint / vue-tsc / core `GET /healthz` 200 / frontend `GET /` 200 — **全部通過**。

## 決策記錄（重大取捨，一行一條，附日期）

- 2026-07-18：uv workspace root = repo root，單一 venv（ADR 001）。
- 2026-07-18：沿用 npm 不引入 pnpm；node:22-alpine 需先升 npm 再 `npm ci`（ADR 003）。
- 2026-07-18：CI 不用 prisma migrate diff（MariaDB JSON 永久誤報）；以 migrate deploy + schema_sync_check 取代（ADR 004）。
- 2026-07-18：proto 改放 contracts/proto/matso/<pkg>/v1/（buf 標準佈局，免 lint 例外）。
- 2026-07-18：MariaDB 對外綁 **3307**（本機 3306 被使用者既有 mariadb_lan 容器占用，勿動它）。
- 2026-07-18：ruff 停用 RUF001-003（中文全形標點誤報）。
- 2026-07-18：DB 權威 = db/prisma/schema.prisma；Python 端 SQLAlchemy 唯讀跟隨（SPEC_FULL §15.4）。
- 2026-07-18：hex grid 採 H3 res 8 預設；路徑規劃自寫 A*（HOW_TO §8）。
- 2026-07-18：SPEC.md 保留為歷史文件；一切以 SPEC_FULL.md 為準。
- 2026-07-20：gRPC codegen 用 grpcio-tools 離線產生（非 buf generate），產物不入 git（ADR 005；因 SPEC §18 air-gapped）。buf lint/breaking 仍保留。
- 2026-07-20：契約 fuzz 用 schemathesis **v4**（v3 不支援 FastAPI 產生的 OpenAPI 3.1）；order 端點只斷言「不 5xx」。ruff B008 放行 FastAPI `Depends()` 慣例。

## Backlog / 發現的問題

### O6 → O7 交接項（2026-07-21，M6 + 多陣營完成後）

**部署層接線（介面皆已備注入點，接線即可）**：
- **AI 迴路 ↔ kernel**：kernel 事件 → 建 context → `run_opfor_turn`（注入 RoleManager-decider +
  TerrainGatewayAdapter feasibility + QdrantCitationVerifier + scenario no_strike + relations）→ orders 落 pending → `intervention_events` 寫 Ledger。
- **真 AI 後端**：vLLM（`OPENAI_BASE_URL`）、bge-m3 模型檔（env 注入路徑）、Qdrant 服務；`RecordingClient` 錄 fixtures 供 CI `ReplayClient`。
- **聚合裁決分流**：kernel `should_aggregate` → `resolve_multiway_tick`（session forces + FactionRelations）。
- **AIInvocationLog / AI_DISABLED 端點**：目前無 AI REST 端點；O6.5 迴路接 kernel 時於入口 `require_ai_enabled`。

**多陣營待接（O7 依賴）**：
- **O7.1 依賴 O6.7**：scenario `factions:`（id/顯示名/顏色）+ `relations:`（上三角，未宣告預設 HOSTILE）+ victory_conditions 任意陣營；loader 建 `FactionRelations` 注入 session、驗證（未知/保留字/非法關係→精確錯誤）。
- **session 熱狀態載入 relations**：OrderService / sweep / 聚合的 `relations` 目前為注入參數（預設全 HOSTILE）；O7.1 由 scenario 載入、White Cell（O7.4）局中 `set_relation` 寫 Ledger + 熱狀態。
- **WargameSession.aiMode 欄位**（§9.0 per-session 模式持久化）→ O7.4 白軍控制台（局中切模式）；目前為 `resolve_ai_mode` 的設定預設。
- **下令目標改用真 intel contacts**：O6.10 為 E2E 便利在 `STUB_GATEWAY` 下讓 `GET /units` 全放行；O7 UX 應改由偵測到的敵方 contact 選 ENGAGE 目標，移除該 affordance。
- **前端 faction palette**：`buildUnitFeatures` 已收 palette 參數；O7.1 由 scenario `factions[].color` 注入取代 `DEFAULT_FACTION_COLORS`。

**其他 O6 部署掛帳**：
- **SPEC_INGEST / O9**（文檔轉換）與 doctrine 語料仍待人備；RAG/eval 空為合法常態，AI 自動降級 AI_BARE。
- **真模型 eval** 為手動 workflow（`ai-eval-manual.yml`），需可達 vLLM 端點。

### 2026-07-19 M0–M1 code review 發現（10 主要 + 8 次要；修復卡 = O1.7，worklog: docs/worklog/O1.7.md）

> **修復狀態（同日，O1.7）**：R1–R4、R6–R9、r11–r18 ✅ **全部修復**（含回歸測試）；
> **R10 ✅ 已於 O3.1 實作**——ledger 指令序列重播接入 golden harness（`order_replay_60`，同序列→同 stateHash）。
> R5（checkpoint 後前滾 / RPO=0）：orders 已落地（O3.1），但「由 checkpoint 後的 ledger 指令重播前滾」
> 尚未接進 recover()，留待 O3.4 移動執行 + O8.1 重播服務（SPEC §18 已加 Phase 註記）。
> r16 的 regex→DMMF 解析升級留備忘（WARN 已改硬錯誤）。

主要發現（依嚴重度；★ = 已實證重現）：
- **[R1]★ rollback 後 LedgerWriter tip 快取過期** → Kernel 下一次 append 產生重複 seq → IntegrityError 停擺 tick loop。兩個 writer 實例間無快取失效機制（ledger.py `_tips`）。
- **[R2]★ recover() 復活被 rollback 的狀態**：rollback 不刪較晚的 checkpoint，recover 無條件取最高 tick → 崩潰復原悄悄撤銷回滾（checkpoint.py load_latest）。
- **[R3] events_after_checkpoint 以 tick 計數**，rollback 後 ledger tick 非單調 → 計數混入被棄世代。需以單調的 ledger seq 錨定 checkpoint（加 `ledgerSeq` 欄）。
- **[R4] CI 整合測試假綠燈**：python job 無 DB/Redis services → 12 個整合測試全 skip；integration job 起了 compose 卻不跑 pytest。
- **[R5] SPEC §18「RPO=0」與實作不符**：recover 只到 checkpoint 當下、無前滾、RecoveryResult 缺續跑資訊。實作已記 deferred，但 SPEC 文字未加 Phase 註記。
- **[R6] SPEC §3.3 MUST「自動降頻」無任何實作**：overran/overrun_count 無消費者，runtime 節奏迴圈不存在。
- **[R7] broadcast seq 存 Redis INCR 不耐 Redis 清空**（正是復原假設的崩潰模式）→ ws 重連補償契約失效；且 ws_protocol.md「與 Ledger seq 同源」與實作不符。
- **[R8] TICK_OVERRUN 把牆鐘 duration_ms 寫進被 hash 的 aiDecision** → 生產 ledger 無法由重播重現（golden 不受影響）。應把非決定性診斷移出 hash 範圍（中性 `detail` 欄）。
- **[R9] async tick path 上同步阻塞 I/O**（ledger commit / redis / checkpoint）違反 HOW_TO §3.1；RedisHotState 每單位 GET+SET 與 get_all N+1 → 500 單位時 ~150ms/tick 純網路等待。
- **[R10] golden replay 未實作「讀 Ledger 指令序列重跑」**（SPEC §3.2 / TASKS O1.6 字面），目前只驗合成想定 seed 決定性；ledger-based 重播應明確改記為 O3.1 後補作。

次要發現：
- [r11] `core/app/errors.py` 未建（HOW_TO §3.1 要求領域錯誤集中定義）；rollback 的領域錯誤拋裸 ValueError。
- [r12] 覆蓋率工具未接（SPEC §19.3 要求 ≥80% 無法量測）。
- [r13] verify_ledger.py 的 `_normalize_url` 與 config.py `sqlalchemy_url` 重複同一段邏輯。
- [r14] 測試鷹架重複：SQLite session_factory fixture ×3、no-op Kernel 建構 ×4、`_NullSink` ×2、DEV_DB/REDIS_URL ×2——無 conftest.py。
- [r15] verify_ledger 全量載入事件（大 session 恐 OOM）；verify_chain 已收 Iterable，可 streaming。
- [r16] schema_sync_check 對未知型別只 WARN 不 fail → 該欄 drift 永遠測不到（假綠燈）；model body regex 對未來含 `}` 的區塊會截斷。
- [r17] `_BaseHotState` 用 NotImplementedError 而非 ABC abstractmethod（漏實作晚至 tick 中才爆）。
- [r18] RedisHotState 隱性要求 `decode_responses=True`，建構時不驗證（bytes client → checkpoint 時 TypeError）。
- （已反駁：RNG `_derive_seed` 冒號碰撞疑慮——master_seed 為 int，第一個冒號即無歧義分隔，映射單射。）
- （設計約束備忘：compute_diff 不表達「欄位移除」——熱狀態欄位集固定的前提下成立，docstring 已註明；子系統設計時勿用刪 key 表達狀態。）

### 既有 backlog

- **[O2.1 realdata ✅ 已驗證]** 真檔 `/Volumes/M200/Maps/TW_ALL.tif`（1GB）SLA benchmark 已通過（冷啟動<30s、p99<5ms）。真檔 nodata=0.0、無 overview。
- **[O2.3 待辦]** 真檔無 overview 金字塔——viewshed 降採樣需要時，以 gdaladdo 建外部 .ovr 或調整採樣。
- **[O2.3 GRASS 對照 release-gated]** modules/terrain/tests/grass_compare/ 骨架完成（確定性抽樣可測）；`_grass_visibility`（docker osgeo/grass-gis r.viewshed 呼叫 + raster↔點取樣）為 release 前必完成的 TODO。CI 自動 skip（grass marker）。
- **[O2.3→O5.4 交接]** check_los 的 fresnel_clearance = 幾何最小餘隙（公尺）。O5.4 comms 模組的鏈路預算吃 `obstruction_db`（繞射/遮蔽附加損耗）作為注入項，保持模組純；**由 terrain fresnel_clearance/LOS 換算成 dB 的映射屬部署層**（Core 每通訊 tick 從 terrain CheckLos 填 request，同 O3.6/O5.3 注入慣例），尚未接活。
- **[外接硬碟 M200 資產]** TW_ALL.tif / taiwan.osm.pbf / taiwan_drive.graphml 皆在 `/Volumes/M200/Maps/`；路徑一律 env 注入（MATSO_DTED_PATH 等，見 modules/terrain/.env.example），未掛載時 try_open_default 降級、開發用合成夾具。
- **[上游相容備忘]** rasterio 1.5.0 × numpy 2.5 內部 reshape DeprecationWarning——pyproject filterwarnings 以訊息精確過濾；rasterio 修復後移除該行。
- **CI workflow 尚未在真 GitHub Actions 驗證過**（repo 無 remote/commit）。首次 push 後檢查五個 job。
- schema 變更流程：因 ADR 004，PR checklist 必須人工確認「改 schema.prisma 必附 migration」。
- SPEC_FULL §16.3 的 proto 片段仍寫 `service PluginBase`，實際契約已更名 `PluginBaseService`（buf SERVICE_SUFFIX）——下次改 SPEC_FULL 時順手更新。
- Checkpoint stateBlob >16MB 策略 = ADR 002（Open，M1-5 前決定）。
- schema_sync_check v1 只比對 table/column/nullable/pk/型別大類；index/unique/FK 未比對（工具 docstring 有註記）。
- 開發機為使用者共用機器：3306（mariadb_lan）、8080（pma_lan）已被占用；MATSO 用 3307/8000/3000/6333/6379。
- **[O1.6/前滾待辦]** DeterministicRNG 尚未實作 get_state/set_state（generator 狀態序列化）。O1.5 的 recover 只保證「復原到 checkpoint 當下」；mid-interval crash 的完整前滾需 RNG state 或「從 checkpoint/從 0 重跑」（O1.6）。checkpoint 目前也只含單位熱狀態（未含 RNG state / order queue）。
- **[事件 schema 議題]** TICK_OVERRUN 等引擎事件目前把結構化診斷塞進 `aiDecision` JSON 欄（schema 無中性欄）。事件類型變多前（O3 起），應檢討於 TacticalEventLog 加一個中性 `detail` JSON 欄（需 prisma migrate + ADR）。
- **[O3.4 待辦]** 子系統（movement 等）實際寫入單位熱狀態的路徑未定；O1.4 只讓 Kernel 持有 hot_state 並 drain/broadcast，diff 現階段為空亦正確。single-writer 原則下子系統應經 Kernel 更新。
- **[O1.4 已交付]** RedisBroadcaster 只到 Redis 落地（ring buffer/pub-sub）；WS 客戶端 fan-out（訂閱、faction 過濾、推前端）屬 O4.3。
- **[裝配提醒]** 真實裝配 Kernel 時：event_sink=LedgerWriter、hot_state=RedisHotState、broadcaster=RedisBroadcaster、wall_clock=app.runtime.PerfCounterClock。

## 下一步建議（給下一個接手的 agent）

**M0–M3 已合併回 main + CI 全綠。M5 環境模組全數完成（O5.1–O5.4：Weather LIVE/SYNTHETIC + 效果整合 + Comms/EW）。** 剩餘主線：**M4 前端**（O4.1 起）與 **M6 AI Phase 1**（需 vLLM 節點）。兩條平行路可續：

1. **M4 前端 COP（O4.1 起）**：認證 + lobby（login/JWT/refresh；後端 auth 端點同卡，Argon2id+JWT）。驗收：Playwright 登入→lobby、錯誤密碼被拒、token refresh。**注意**：platform/ 仍是 Nuxt 初始模板；API 型別一律由 `contracts/core_api.yaml` 生成（禁手寫）；元件放 `platform/app/components/<區域>/`。先讀 SPEC §13。
2. **M5 環境模組續做（O5.2/O5.3）**：
   - O5.2 CWA LIVE 模式（API 拉取、格網化、stale 降級 + 30min 告警）；effects_mapping.yaml 外部化 + White Cell 熱調整。
   - **O5.3 天氣效果整合**——把 O5.1 的 effects 接進裁決/偵測/移動：EnvSnapshot/DetectionEnv/AggregateEnv 的 weather_modifier（目前佔位 1.0）由 weather client（gRPC）每天氣 tick 填入。驗收：暴雨 vs 晴天同交戰結果分佈可觀測不同（固定 seed 比係數）。
3. **M3 交接（真實部署組裝）**：kernel↔API↔terrain/weather 的正式接線（gRPC client、真 LedgerWriter、Redis hot_state、DB 呼叫 to_thread、tick_source 接活 SimClock）於 M4/部署階段；O3.6 已以注入假件證明子系統協同正確。聚合裁決分流（should_aggregate）於 O7.1 想定就緒後接入 kernel。
4. **可複用件**：`app.adjudication`/`app.movement`/`app.intel`/`app.orders`、統一錯誤處理、`PhysicsGateway` 注入；`matso_sdk`（MatsoPlugin + harness）；weather/terrain 插件範本。
5. **codegen 提醒（ADR 005）**：乾淨 checkout 後、跑測試/mypy 前先 `uv run python ops/tools/gen_proto.py`（含 plugin_base/terrain/weather 三 proto）。CI/Dockerfile 已自動化。
6. **分支狀態**：main = M0–M3（已推送、CI 綠）。`feat/o5.1-weather-skeleton` 從 main 開，待合併。
7. 開發環境：`uv sync` 一律在 **repo root**；compose `docker compose up -d --wait`。**CI 效能測試**：夾具版 p99 標 `benchmark`、CI 以 `-m "not benchmark"` 排除（本機仍跑）。golden 改動後 `uv run python ops/tools/rerecord_golden.py`。
