# MATSO 進度帳本

> 跨 session / 跨 AI Agent 的唯一進度事實。維護規則見 [HOW_TO.md](HOW_TO.md) §7。

## 目前狀態摘要（3 行內，最新在上）

- 2026-07-19：**O2.1 完成（M2 起點）**。branch `feat/o2.1-dted`（stacked on O1.7）。DtedMap（rasterio windowed 讀取、nodata→water、出界拋錯）+ **`MATSO_DTED_PATH` 環境變數注入**（真檔在外接硬碟，使用者需求）+ 合成 GeoTIFF 夾具（測試時生成）；SLA benchmark 掛 `realdata` marker 待真檔。151 passed / coverage 90.6%。worklog: docs/worklog/O2.1.md。
- 2026-07-19：O1.7 完成（code review R1–R10/r11–r18 全數修復）。
- 2026-07-18：M1 里程碑達成（O1.1–O1.6）。下一步：**O2.2**（Hex grid 預計算）。

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
| O2.1 (M2-1) | DONE | Fable 5 (2026-07-19) | branch feat/o2.1-dted (stacked) | DtedMap + `MATSO_DTED_PATH` 路徑注入（外接硬碟）+ 合成夾具；23 測試（2 realdata 待真檔跑 SLA） |
| O2.2 ~ O2.5 | TODO | — | — | 從 O2.2（Hex grid）開始；h3/pyarrow 依賴屆時才加 |
| M3-1 ~ M3-6 | TODO | — | — | |
| M4-1 ~ M4-6 | TODO | — | — | platform/ 仍是 Nuxt 初始模板（僅加了 eslint/typecheck/Dockerfile） |
| M5-1 ~ M5-4 | TODO | — | — | |
| M6-1 ~ M6-6 | TODO | — | — | 需 vLLM 節點；eval runner 路徑 = matso_ai.evals.run |
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

## Backlog / 發現的問題

### 2026-07-19 M0–M1 code review 發現（10 主要 + 8 次要；修復卡 = O1.7，worklog: docs/worklog/O1.7.md）

> **修復狀態（同日，O1.7）**：R1–R4、R6–R9、r11–r18 ✅ **全部修復**（含回歸測試）；
> R5/R10 ✅ 規格與任務已對齊——「checkpoint 後前滾」與「ledger 指令序列重播」為 Phase 註記，
> 實作列入 **O3.1 驗收**（SPEC §3.2/§18 已加註）。r16 的 regex→DMMF 解析升級留備忘（WARN 已改硬錯誤）。

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

- **[O2.1 realdata 待辦]** DTED 真檔 SLA benchmark（冷啟動<30s、p99<5ms）待外接硬碟掛載後執行：`MATSO_DTED_PATH=/Volumes/<硬碟>/TW_ALL.tiff uv run pytest -m realdata`。不達標時的優化方向記於 terrain/dted.py docstring。
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

1. 認領 **O2.2**（Hex grid 預計算）。產出：離線預計算 CLI（H3 res 7–9 cell 屬性 → parquet 快取）、`get_cell_batch`；terrain_class 以坡度+高程規則推導。規格：SPEC_FULL §4.2/§4.3、TASKS.md O2.2。deps: O2.1（已完成）。
   - **可直接複用**：`DtedMap`（⚠ cell 統計勿逐點 get_elevation N×N 次——用 rasterio 窗口批次讀取聚合）、合成夾具產生器（同一夾具測 hex 屬性）、`TerrainSettings`（parquet 快取路徑也用 env 注入模式，如 `MATSO_HEXCACHE_DIR`——外接硬碟使用者情境同 DTED）。
   - 依賴：`h3`、`pyarrow` 加到 modules/terrain。陷阱見 HOW_TO §8（h3 內建距離勿當 A* heuristic 以外用途）。
2. **外接硬碟情境（使用者環境）**：DTED 真檔以 `MATSO_DTED_PATH` 指定；realdata SLA benchmark 待真檔（見 backlog）。開發/CI 一律合成夾具，勿假設真檔存在。
3. **分支鏈狀態**：main ← O1.1 ← … ← O1.7 ← O2.1（皆 stacked，**未合併/推送**）——建議把 M1+O1.7 段先合併回 main。
4. 開發環境：`uv sync` 後一切在 repo root 跑；compose `docker compose up -d --wait`（mariadb 3307 / redis 6379）。**注意 OrbStack 可能隨機器休眠而停**——整合測試全 skip 時先 `open -a OrbStack`。
5. **golden replay 維護**：改動確定性邏輯後 `uv run python ops/tools/rerecord_golden.py` 重錄並在 PR 說明。
