# MATSO 部署接線 Checklist

> 功能開發（M0–M9）已完成且 CI 全綠。本檔是「把已完成的元件接到真實執行期」的操作清單——
> 所有接點都是**注入式介面**（程式碼已備），部署即接線，不需再改核心邏輯。
> 任務板對應：TASKS.md **O10**。每項標注 [檔案/介面] 與 [why]。

圖例：⬜ 待做 · 🔌 注入點（程式碼已備）· ⚠ 紅線/安全 · 📦 外接資產

---

## A. 執行環境與資產（先決條件）

- ⬜ `MATSO_ENV=production` → 啟動即 `Settings.ensure_production_safe()` fail-fast
  （拒：預設 `JWT_SECRET` / `STUB_GATEWAY=1` / `CORS_ORIGINS=*`）。[core/app/config.py] ⚠
- ⬜ 設 `JWT_SECRET`（≥32 bytes）、`CORS_ORIGINS`（實際前端來源，勿用 `*`）、`DATABASE_URL`、`REDIS_URL`。⚠
- ⬜ 📦 外接資產（一律 env 注入、缺失優雅降級，勿硬編）：
  - `MATSO_DTED_PATH` → `TW_ALL.tif`（terrain）；`taiwan.osm.pbf`/`taiwan_drive.graphml`
  - CWA `api_key`（weather LIVE）
  - tileserver `.mbtiles`（compose profile `tiles`；#2 底圖已建：見下）
  - terrain hex 快取（#4）：`uv run python -m terrain.precompute --res 8 --bbox <MIN_LNG MIN_LAT MAX_LNG MAX_LAT>`
    （MATSO_DTED_PATH + MATSO_HEX_CACHE_DIR）→ 產 `res8.parquet`；未建則 pathfinding 回 TERRAIN_UNAVAILABLE
  - 街道底圖（#2）：`docker run --rm -v <MAPS>:/data ghcr.io/onthegomap/planetiler --osm-path=/data/taiwan.osm.pbf
    --output=/data/tiles/taiwan.mbtiles --download` → 向量 mbtiles；設 `MBTILES_DIR`+`TILE_URL`，`--profile tiles` 起 tileserver
  - 衛星 / 軍用底圖（#2 抽換點）：設 `NUXT_PUBLIC_SATELLITE_URL`（raster XYZ）或 `NUXT_PUBLIC_BASEMAPS`（JSON）即現選項
  - bge-m3 模型檔（RAG 嵌入）、OCR 模型檔（tesseract/PaddleOCR，O9.2）
- ⬜ `docker compose up -d --wait`（MariaDB 3307 / redis / qdrant / terrain / weather / comms / tileserver）。
- ⬜ `prisma migrate deploy`（含 faction String、後續 O10 的 aiMode/refresh 撤銷 migration）。

## B. Kernel 真實裝配（O10.1）

- 🔌 組裝正式 Kernel（O3.6 已以假件證明協同）：
  - `event_sink=LedgerWriter(default_session_factory())`
  - `hot_state=RedisHotState`、`broadcaster=RedisBroadcaster`
  - `tick_source=SimClock`、`wall_clock=app.runtime.PerfCounterClock`
  - 子系統 gRPC client：`TerrainClient`/`WeatherClient`/`CommsClient`（斷路器已備）
  - DB 呼叫走 `to_thread`（tick loop 不阻塞，HOW_TO §3.1）
- ⬜ 聚合裁決分流：kernel `should_aggregate` → `resolve_multiway_tick(forces, relations)`（O6.9）。
- ⬜ 天氣/地形係數每 env-tick 填入 `EnvSnapshot`（gRPC client）。
- 🔌 comms 鏈路預算的 `obstruction_db`：由 terrain `check_los.fresnel_clearance` 換算 dB 注入（O2.3→O5.4，映射屬部署層）。

## C. AI 節點（O10.2）

- 🔌 vLLM（OpenAI-compatible）：`OpenAICompatibleClient(base_url=OPENAI_BASE_URL, model=MATSO_LLM_MODEL)`；
  air-gapped 內網。[ai/matso_ai/inference/client.py]
- 🔌 RAG：`RagStore(QdrantClient(url=...))` + 真 bge-m3 embedder 取代 `HashEmbedder`；`QdrantCitationVerifier` 注入護欄 G5。[ai/matso_ai/rag/]
- ⬜ 錄放 fixtures：以 `RecordingClient` 包真 client 錄一批 → CI `ReplayClient.from_dir` 重播。
- ⬜ 真模型 eval：手動 workflow `.github/workflows/ai-eval-manual.yml` 填 endpoint 跑 §19.4 四門檻。
- ⬜ 語料/eval 內容（待軍方/公開資料，DataSearch.md/EvalCreator.md 派給資料 agent）——**空語料系統仍運作**（自動降級 `AI_BARE`）。

## D. AI 迴路 ↔ Kernel（O10.3）

- 🔌 OPFOR 自主迴路接活：kernel 事件 → 建 context → `run_opfor_turn(decider=RoleManager 背後, gateway=GuardrailGateway, mode, feasibility=TerrainGatewayAdapter, citation_verifier=QdrantCitationVerifier, no_strike_hexes=scenario, relations=session)` → orders 落 pending（OrderService）。[core/app/ai_loop/]
- 🔌 護欄攔截 → `intervention_events()` → `LedgerWriter.append`（GUARDRAIL_INTERVENTION）。
- ⬜ `WargameSession.aiMode` 欄位（prisma migration + 模型鏡射）；`resolve_ai_mode(session.ai_mode, settings.ai_mode)`；AI 端點/迴路入口 `require_ai_enabled`。⚠ 護欄不可 bypass。
- 🔌 RoleManager 記 `AIInvocationLog`（含 mode）——注入 `InvocationLogWriter(default_session_factory())`。

## E. 想定 / 白軍執行期（O10.4）

- 🔌 開局：lobby `create_session` 接 `scenario_id` → `load_scenario_package()` → `create_session_from_scenario(db, loaded, master_seed)`（建 session+units）。[core/app/scenario/]
- 🔌 relations 熱狀態：從 scenario `FactionRelations` 載入 session；White Cell `/inject`+`set_relation` → `FACTION_RELATION_CHANGED` Ledger + 熱狀態更新。
- 🔌 MSEL：`MselEngine(entries, context_fn=讀熱狀態)` 掛 kernel `check_triggers()` 每 tick；victory `check_victory` 判勝負。
- 🔌 時間控制：kernel 消費 stream `SESSION_CONTROL`（PAUSE/RESUME/ROLLBACK→`recover()` O1.5）。
- ⬜ ENGAGE 目標改用真 intel contacts（移除 O6.10 的 `STUB_GATEWAY` units 全放行 affordance）。
- ⬜ 前端 faction 顏色由 scenario `factions[].color` 注入 `buildUnitFeatures(palette)`（取代 DEFAULT）。

## F. 安全補完（O10.5）⚠

- ⬜ refresh token 撤銷/輪替：DB 撤銷表（migration）+ rotation + logout 失效 + 停用帳號即撤（CODE_REVIEW **C5**）。
- ⬜ 建局角色 gate：`create_session` 加角色檢查（誰能開演習）——完整角色×端點矩陣延伸（CODE_REVIEW **C8**）。
- ⬜ 稽核：管理操作進 audit log（獨立於戰術 Ledger，SPEC §12）。

## G. 文檔/OCR & 觀測性（O10.6）

- ⬜ OCR 引擎：裝 tesseract + 中文 PaddleOCR；模型檔 env 注入；缺失自動降級「僅文字層」（O9.2 已備）。
- ⬜ GRASS r.viewshed 對照（release-gated，O2.3）：release 前跑 docker osgeo/grass 對照 ≥98%。
- ⬜ 觀測性（SPEC §20.3）：Prometheus 匯出 `tick_duration_ms/ai_queue_depth/guardrail_blocks_total/ws_clients/plugin_health`；Grafana「推演健康」儀表板；`TICK_OVERRUN`/plugin DOWN/AI 逾時率>20% 告警。
- ⬜ CI 維運：actions node20→24 升級（deprecation 警告）；覆蓋率工具接入量測（backlog r12）。

---

## 建議部署順序
A（環境/資產）→ B（Kernel）→ E（想定開局，先跑無 AI 傳統兵推驗證）→ D（接 AI 迴路，AI_BARE）→ C（真 vLLM/RAG，AI_FULL）→ F（安全）→ G（觀測/OCR）。

**先決可用性**：完成 A+B+E 即可跑 **AI_OFF 傳統兵推**（人對人，全物理引擎）——不需任何 AI 節點。
