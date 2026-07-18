# MATSO 進度帳本

> 跨 session / 跨 AI Agent 的唯一進度事實。維護規則見 [HOW_TO.md](HOW_TO.md) §7。

## 目前狀態摘要（3 行內，最新在上）

- 2026-07-18（晚）：**O1.5 完成**（branch `feat/o1.5-checkpoint-recovery`，stacked on O1.4）。ADR 002（inline zstd LONGBLOB + 8MB 護欄）；CheckpointManager + recover + rollback（ROLLBACK 事件）+ Kernel 每 N ticks checkpoint；20 新測試（含 4 崩潰復原/rollback 整合）。101 passed。worklog: docs/worklog/O1.5.md。
- 2026-07-18（晚）：O1.4（Redis 熱狀態 + diff）、O1.3（Kernel tick loop）、O1.2（Ledger）、O1.1（SimClock+RNG）完成。
- 下一步：**O1.6**（Golden replay harness，M1 收尾）。

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
| O1.6 | TODO | — | — | M1 收尾（Golden replay harness）；規格見 SPEC_FULL §19.1 |
| M2-1 ~ M2-5 | TODO | — | — | TW_ALL.tiff 需放至 modules/terrain/data/（不入 git）；rasterio/GDAL 依賴屆時才加（ADR 001 註記） |
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

1. 認領 **O1.6**（Golden replay harness，M1 收尾）。產出：`core/tests/replay/harness.py`（讀 Ledger 指令序列從頭重跑、比對最終 stateHash）、`ops/tools/rerecord_golden.py`、第一個 golden（空想定跑 100 ticks）；**移除 `core/tests/replay/test_golden_placeholder.py`**。規格：SPEC_FULL §19.1、TASKS.md O1.6。deps: O1.5（已完成）。
   - **可直接複用**：`compute_state_hash`（app.state.checkpoint，比對用）、`Kernel`（全 no-op stub + NullMonotonicClock + InMemoryHotState + FakeOrderSource 即可確定性空跑）、`app.engine.SimClock`、`app.state.recover`。
   - **設計提示**：replay 若採「從 tick 0 重跑」，DeterministicRNG 由 master_seed 自然重現，最單純、不需 RNG state 序列化。golden = 記錄 (master_seed, 指令序列) → 期望最終 stateHash。CI 已有 replay job 掛勾（跑 `-m golden`）。
   - 驗收：golden replay 測試以真 golden 通過；手動改任一裁決常數會使 hash 比對失敗。
2. **分支鏈狀態**：main ← O1.1 ← O1.2 ← O1.3 ← O1.4 ← O1.5（皆 stacked，**未合併/推送**）——由使用者決定合併時機。O1.6 應繼續 stack 在 O1.5 之上。完成 O1.6 即 **M1 里程碑達成**。
3. 開發環境：`uv sync` 後一切在 repo root 跑；compose 已可 `docker compose up -d --wait`（mariadb 3307 / redis 6379）。整合測試需 compose 起著才會實際執行（否則自動 skip）。
