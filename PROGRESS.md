# MATSO 進度帳本

> 跨 session / 跨 AI Agent 的唯一進度事實。維護規則見 [HOW_TO.md](HOW_TO.md) §7。

## 目前狀態摘要（3 行內，最新在上）

- 2026-07-18（傍晚）：**O1.1 完成**（branch `feat/o1.1-simclock-rng`）。SimClock + DeterministicRNG（numpy PCG64）+ 23 單元測試；pytest/mypy/ruff 全綠、牆鐘 grep gate 乾淨。worklog: docs/worklog/O1.1.md。
- 2026-07-18（傍晚）：新增 CLAUDE.md / TASKS.md（O 編號）/ docs/worklog 協定。
- 下一步：**O1.2**（Ledger writer + hash chain，見 TASKS.md）。

## 任務板

| 卡號 | 狀態 | 認領者 | PR | 備註 |
|------|------|--------|----|------|
| M0-1 | DONE | Claude (2026-07-18) | — | uv workspace（root venv, ADR 001）；ruff+mypy strict+pytest；pre-commit 已 install；前端 eslint(@nuxt/eslint)+vue-tsc |
| M0-2 | DONE | Claude (2026-07-18) | — | buf STANDARD 通過（proto 移至 contracts/proto/matso/*/v1/）；JSON Schema metaschema ✓；OpenAPI 3.1 ✓ |
| M0-3 | DONE | Claude (2026-07-18) | — | compose：mariadb(3307)/redis/qdrant/core/frontend 全 healthy；`up -d --wait` exit 0 |
| M0-4 | DONE | Claude (2026-07-18) | — | init migration 已套用（db/prisma/migrations/20260718025607_init）；schema_sync_check.py：15 tables/118 columns 一致 |
| M0-5 | DONE | Claude (2026-07-18) | — | .github/workflows/ci.yml：python/frontend/contracts/schema-sync/integration 五 job（**未在真 GitHub 跑過**，首次 push 後要盯） |
| O1.1 (M1-1) | DONE | Opus 4.8 (2026-07-18) | branch feat/o1.1-simclock-rng | SimClock + DeterministicRNG；23 測試綠；numpy 2.5.1 |
| O1.2 ~ O1.6 | TODO | — | — | 從 O1.2 開始；規格見 SPEC_FULL §3/§15.3、HOW_TO §4.1 |
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
- **[O1.5 待辦]** DeterministicRNG 尚未實作 get_state/set_state（generator 狀態序列化）——checkpoint 中途復原時需要，O1.1 刻意不做（範圍紀律）。

## 下一步建議（給下一個接手的 agent）

1. 認領 **O1.2**（Ledger writer + hash chain）。產出：`core/app/state/ledger.py`（LedgerWriter：seq 單調發號、selfHash=SHA256(prevHash‖canonical_json)、批次寫入、無 update/delete）、`ops/tools/verify_ledger.py`、`ops/tools/grant_ledger_readonly.sql`。規格：SPEC_FULL §15.3、TASKS.md O1.2。
   - 可複用 O1.1 已建的 `core/app/engine/`（SimClock 供 tick，RNG 非本卡所需）；SQLAlchemy models 已在 `core/app/models/`（TacticalEventLog 有 seq/prevHash/selfHash 欄位）。
   - canonical_json 需「鍵序不同→輸出相同」單元測試；整合測試連 compose MariaDB:3307。
2. O1.1 在 feature branch `feat/o1.1-simclock-rng`，**尚未合併/推送**——由使用者決定合併時機。
3. 開發環境：`uv sync` 後一切在 repo root 跑；compose 已可 `docker compose up -d --wait`（mariadb 在 3307）。
