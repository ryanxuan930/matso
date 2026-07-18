# MATSO 開發手冊（HOW_TO）V1.0

> 本手冊是給 **AI Agent 與工程師** 的執行指南。規格權威在 [SPEC_FULL.md](SPEC_FULL.md)——本文告訴你「怎麼做、按什麼順序做、做到什麼程度算完成」。
>
> **如果你是接手開發的 AI Agent，先讀 §0，再讀 §1，然後直接跳到 §5 找你的任務。**

---

## 0. AI Agent 作業守則（MUST READ）

1. **開工前**：讀 `PROGRESS.md`（專案進度帳本）→ 讀 SPEC_FULL.md 中與你任務相關的章節 → 讀 `contracts/` 下相關契約。不要憑記憶臆測規格。
2. **收工前**：更新 `PROGRESS.md`（格式見 §7）；所有測試通過才可標記任務完成。
3. **契約先行**：任何跨模組介面，先改 `contracts/`、跑契約測試，再寫實作。手寫端點與契約不一致視為 bug，以契約為準。
4. **不越界**：一次只做一個任務卡（§5）。發現範圍外的問題 → 記入 `PROGRESS.md` 的 `## Backlog / 發現的問題`，不要順手修。
5. **決定性紅線**：模擬邏輯內禁止 `datetime.now()` / `time.time()` / 未受控的 `random`。一律用 `SimClock` 與 `DeterministicRNG`。CI 的 replay 測試會抓到你。
6. **AI 永不裁決物理**（SPEC_FULL §1.2 P1）：如果你發現自己在寫「讓 LLM 判斷是否命中/可見/可達」的程式碼，停下來，那是裁決引擎的工作。
7. **禁止繞過護欄**：Guardrail Gateway（SPEC_FULL §10）是強制路徑，不得為了 demo 方便加 bypass flag。
8. **禁止假資料矇混**：測試不可用 `assert True`、mock 掉被測物本身、或硬編碼期望輸出。裁決公式測試必須用 property-based testing。

---

## 1. Monorepo 結構

```
matso/
├── SPEC.md                  # 原始精簡規格（歷史文件，勿改）
├── SPEC_FULL.md             # 權威規格
├── HOW_TO.md                # 本文件
├── PROGRESS.md              # 進度帳本（AI Agent 交接用，隨時更新）
├── contracts/               # ★ 一切介面契約（先行）
│   ├── core_api.yaml            # OpenAPI 3.1（Core REST）
│   ├── ws_protocol.md           # WebSocket envelope 與訊息型別
│   ├── proto/matso/plugin/v1/plugin_base.proto   # 插件基礎 gRPC 服務（buf 標準佈局）
│   ├── proto/matso/terrain/v1/terrain.proto      # Terrain module gRPC
│   ├── weather_payload.schema.json
│   ├── scenario.schema.json
│   ├── ai_output.schema.json    # AI 結構化輸出（各角色一節）
│   ├── weaponeering.schema.json
│   └── mobility_matrix.json
├── core/                    # Core Orchestrator（Python 3.12 + FastAPI）
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── api/                 # REST + WS 端點（薄層，只做 IO 與授權）
│   │   ├── engine/              # SimClock, Kernel, movement, sensors, logistics
│   │   ├── adjudication/        # 純函數裁決引擎
│   │   ├── guardrails/          # Guardrail Gateway G1–G6
│   │   ├── intel/               # per-faction fog of war store
│   │   ├── orders/              # Order validator + 狀態機
│   │   ├── plugins/             # Plugin registry, gRPC clients, circuit breaker
│   │   ├── state/               # Redis 熱狀態、checkpoint、ledger writer
│   │   └── models/              # SQLAlchemy models（與 prisma schema 同步）
│   └── tests/
│       ├── unit/  ├── property/  ├── replay/  └── integration/
├── modules/
│   ├── _sdk/                # MatsoPlugin base class + 測試 harness
│   ├── terrain/             # DTED + hex grid 服務
│   ├── weather/             # CWA / synthetic 天氣服務
│   └── vision/              # 非 AI 影像仲裁（Phase 1.5）
├── ai/
│   ├── matso_ai/            # Python 套件（inference/rag/training/evals 子模組，M6 起實作）
│   ├── prompts/             # 各角色 system prompt（版本化 .md + frontmatter，資料目錄）
│   └── evals/cases/         # 評測案例（資料目錄；runner 在 matso_ai.evals）
├── platform/                # Nuxt 4 前端（已存在）
│   └── app/
│       ├── pages/           # §SPEC_FULL 13.1 的路由
│       ├── components/      # cop/, orders/, white-cell/, aar/, editor/
│       ├── composables/     # useSessionStream (WS), useCop, useOrders...
│       └── stores/          # Pinia
├── db/
│   └── prisma/schema.prisma # ★ DB schema 唯一權威
├── scenarios/               # 官方想定包（examples/ 內含 3 個）
├── ops/
│   ├── compose/             # docker-compose.yml + 各服務 Dockerfile
│   ├── grafana/  ├── prometheus/
│   └── tools/               # schema_sync_check.py 等 CI 工具
└── docs/
    ├── adr/                 # Architecture Decision Records
    └── runbooks/            # 運維手冊
```

---

## 2. 開發環境建置

### 2.1 需求版本

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.12.x | core, modules, ai（uv 會自動下載） |
| uv | latest | Python 套件管理（安裝：`curl -LsSf https://astral.sh/uv/install.sh \| sh`） |
| Node.js | 22 LTS | platform, prisma |
| npm | 11+ | 前端與 db 套件管理（ADR 003：沿用 npm，不引入 pnpm） |
| Docker + Compose | 24+ | 本地整合環境（macOS 可用 OrbStack） |
| buf | via npx | proto lint 與 breaking check（`npx @bufbuild/buf`，免全域安裝） |

### 2.2 First-time setup

```bash
# 0. Python workspace（在 repo root；一個 venv 涵蓋 core/modules/ai）
uv sync && uv run pytest                    # 應全綠
uv run pre-commit install

# 1. 基礎服務（DB/Redis/Qdrant）——完整 stack 直接 docker compose up -d --wait
cd ops/compose && docker compose up -d mariadb redis qdrant
#    注意：MariaDB 對外綁 3307（3306 常被本機既有服務占用）

# 2. DB schema（migration 一律走 prisma，見 SPEC_FULL §15.4）
cd db && cp .env.example .env && npm install && npx prisma migrate dev

# 3. Core（本機開發模式；容器版由 compose 起）
uv run uvicorn app.main:app --reload --port 8000 --app-dir core

# 4. Terrain module（需將 TW_ALL.tiff 放至 modules/terrain/data/，不入 git；M2 後可用）
uv run python -m terrain.server

# 5. 前端
cd platform && npm install && npm run dev   # http://localhost:3000
```

### 2.3 環境變數

各服務讀 `.env`（範本 `.env.example` 必須維護、必須可跑）。關鍵變數：
`DATABASE_URL, REDIS_URL, QDRANT_URL, OPENAI_BASE_URL`（指向 vLLM）, `TERRAIN_GRPC_ADDR, MASTER_SEED_OVERRIDE`（僅測試用）。

---

## 3. 工程規範

### 3.1 Python（core, modules, ai）

- **Lint/format**：ruff（`ruff check --fix` + `ruff format`）；**型別**：mypy --strict，零錯誤才可合併。
- 全部 I/O 走 async；裁決引擎（`core/app/adjudication/`）例外——**純同步純函數**，輸入輸出皆為 frozen dataclass / Pydantic model，不碰 DB、不碰 Redis、不碰時鐘。
- Pydantic v2 model 為 API 邊界；內部領域物件用 dataclass。
- 例外處理：領域錯誤一律拋自訂例外（`core/app/errors.py` 統一定義，對應契約中的 error code），API 層統一轉 HTTP。

### 3.2 TypeScript（platform）

- ESLint + `vue-tsc --noEmit` 零錯誤；元件用 `<script setup lang="ts">`。
- 型別來源：由 `contracts/core_api.yaml` 以 openapi-typescript 生成 `platform/app/types/api.d.ts`，禁止手寫 API 型別。
- 狀態管理：Pinia；WS 邏輯集中在 `useSessionStream()` composable，元件不得自行開 WebSocket。

### 3.3 Git 慣例

- Trunk-based：`main` 保護，feature branch `feat/m3-order-pipeline` 格式，PR 必須綠 CI + 一位 reviewer（人類或指定 review agent）。
- Commit：Conventional Commits（`feat(core): ...`, `fix(terrain): ...`）。
- 每個任務卡（§5）對應一個 PR，PR 描述必須引用任務卡編號。

### 3.4 CI Pipeline（GitHub Actions，`.github/workflows/ci.yml`）

依序：`lint → typecheck → unit → property → contract (schemathesis/buf) → schema_sync_check → integration (compose) → replay (golden)`。任一紅 = 不可合併。

---

## 4. 關鍵子系統實作指南

### 4.1 決定性基礎設施（M1 之前必懂）

```python
# core/app/engine/clock.py
class SimClock:
    """模擬時間唯一來源。tick 為 int，sim_time_ms = tick * tick_rate_ms。"""
    def now(self) -> SimTime: ...

# core/app/engine/rng.py
class DeterministicRNG:
    """numpy PCG64，以 (master_seed, stream_id) 派生。
    stream_id 是字串（如 "adjudication", "sensors"），以 SHA256 折疊成子種子。
    禁止在 stream 之間共用 generator。"""
```

- Kernel 是 Redis 熱狀態的 **唯一寫入者**。其他元件要改狀態 → 發 command 進 Kernel 佇列。
- Ledger 寫入順序：先計算 `selfHash = H(prevHash ‖ payload)` 再落庫；`seq` 由 Kernel 單調發號。

### 4.2 裁決引擎開發模式

1. 先在 `contracts/weaponeering.schema.json` 定義武器參數欄位。
2. 公式寫成純函數：`def resolve_engagement(order, shooter, target, env: EnvSnapshot, rng) -> list[Event]`。
3. `EnvSnapshot` 由 Kernel 事先收集（地形係數、天氣係數），裁決函數不做任何 RPC。
4. 每條公式配 property test（Hypothesis）：單調性、邊界（射程 0 / 最大）、係數為 1 時退化為 base 值。
5. 新公式或改係數 → golden replay hash 會變 → PR 必須說明並重錄 golden（`ops/tools/rerecord_golden.py`）。

### 4.3 插件開發指南（modules/_sdk）

```python
from matso_sdk import MatsoPlugin

class MyPlugin(MatsoPlugin):
    name = "my-module"
    kind = "CUSTOM"
    contract_version = "1.0.0"

    async def on_configure(self, config: dict) -> None: ...
    async def health(self) -> Health: ...
    # 領域 RPC 另外定義在自己的 proto，與 plugin_base.proto 並存
```

- SDK 已處理：gRPC server 啟動、manifest、health 端點、向 Core 註冊、graceful shutdown。
- 新插件 checklist：proto 進 `contracts/` → `buf lint` → 實作 → `modules/_sdk/harness` 整合測試（模擬 Core 呼叫）→ compose 加服務 → `PluginRegistry` seed。

### 4.4 AI 角色開發指南

1. Prompt 放 `ai/prompts/<role>.md`，YAML frontmatter 記 `version, adapter, temperature, max_tokens, output_schema`（指向 `contracts/ai_output.schema.json` 的節點）。
2. 輸出 schema 先加進 `contracts/ai_output.schema.json`。
3. Guardrail Gateway 是共用管線，新角色只需在 `guardrail_profiles.yaml` 宣告要啟用哪些檢查（G4 IHL 對戰術角色必開）。
4. 加 eval：`ai/evals/cases/<role>/*.yaml`（輸入、必要輸出特徵、禁止輸出特徵）。eval gate 門檻在 SPEC_FULL §19.4，`uv run python -m evals.run --role OPFOR_COMMANDER` 必須過才可改 prompt 上線。
5. Prompt 修改也是 PR——prompt 是程式碼。

### 4.5 前端 COP 開發要點

- 地圖層次：base tile → hillshade → hex grid（deck.gl H3HexagonLayer）→ 天氣 → 控制措施 → 單位（IconLayer + milsymbol 產生的 SVG atlas）→ 選取高亮。
- 所有伺服器狀態進 Pinia store，由 `useSessionStream()` 統一以 `STATE_DIFF` patch；元件只讀 store。
- Fog of war 是後端責任；前端 MUST NOT 假設拿得到 ground truth（收到什麼畫什麼）。
- 效能守則：單位圖層用 instanced rendering；每 tick 只 patch 變動單位；地圖互動（拖曳中）暫停 heavy layer 重繪。

---

## 5. 任務拆解（Task Cards）

> **可執行版任務內容（含詳細步驟、驗收指令與 worklog 協定）已移至 [TASKS.md](TASKS.md)**，
> 編號對應 `O<m>.<n>` ≡ `M<m>-<n>`；內容衝突時以 TASKS.md 為準，本節保留為摘要索引。
> 每張卡 = 一個 PR。`[deps]` 為前置卡。驗收 = 卡內所有 checkbox + CI 綠。
> 認領方式：在 PROGRESS.md 寫下卡號與你的識別。

### M0 基礎設施

- **M0-1 Monorepo 工具鏈**：uv workspace（core/modules/ai）、pnpm、ruff/mypy/eslint 設定、pre-commit。✅ `uv run pytest` 與 `pnpm lint` 可跑（允許 0 測試）。
- **M0-2 契約骨架** [M0-1]：建立 `contracts/` 所有檔案的 v0 版本（欄位可先少，結構要對）；buf + schemathesis 進 CI。✅ CI 契約 job 綠。
- **M0-3 Compose 環境** [M0-1]：mariadb/redis/qdrant/core(stub)/frontend 服務；healthcheck 全通過。✅ `docker compose up` 一鍵起。
- **M0-4 Prisma schema v1** [M0-3]：SPEC_FULL §15 全部表；migrate 可跑；`schema_sync_check.py` 完成並進 CI。✅ SQLAlchemy models 與 prisma 比對通過。
- **M0-5 CI pipeline** [M0-2,3,4]：§3.4 全部 job。✅ 對 main 的 PR 全 job 執行。

### M1 模擬骨幹

- **M1-1 SimClock + DeterministicRNG**：§4.1 規格；含「同 seed 同序列」測試與 stream 隔離測試。
- **M1-2 Ledger writer + hash chain** [M0-4]：append-only、seq 發號、hash 鏈驗證工具 `ops/tools/verify_ledger.py`。✅ 竄改任一事件可被驗證工具偵測。
- **M1-3 Kernel tick loop** [M1-1, M1-2]：SPEC_FULL §3.3 虛擬碼實作（movement/sensors 等先接 no-op stub）；tick 預算量測 + `TICK_OVERRUN` 事件。
- **M1-4 Redis 熱狀態 + single-writer** [M1-3]：狀態 schema、diff 計算、broadcaster stub。
- **M1-5 Checkpoint / rollback / 崩潰復原** [M1-4]：zstd 快照、rollback API、重啟後 checkpoint+ledger 重建。✅ kill -9 core 後 5 分鐘內狀態一致復原（整合測試模擬）。
- **M1-6 Golden replay harness** [M1-5]：replay runner、hash 比對、`rerecord_golden.py`。✅ 空想定 golden 進 CI。

### M2 地理引擎

- **M2-1 DTED 載入與高程查詢**：rasterio memmap、nodata→water 處理、`GetElevation`。✅ 冷啟動 <30s、p99 <5ms（benchmark test）。
- **M2-2 Hex grid 預計算** [M2-1]：H3 res8 全島 cell 屬性表（算一次存 parquet）、`GetCellBatch`。
- **M2-3 LOS / Viewshed** [M2-1]：大圓取樣 + 曲率修正；GRASS 對照測試 ≥98%。
- **M2-4 A* 路徑** [M2-2]：mobility matrix 載入、`GetPath`；property test：路徑成本 ≤ 任意鄰接替代路徑。
- **M2-5 Terrain 插件化** [M2-1..4, M0-2]：套上 `_sdk`，gRPC 服務 + Core 端 client + circuit breaker + 「Terrain DOWN → Session PAUSE」預案。

### M3 裁決核心

- **M3-1 Order pipeline** [M1-3, M2-5]：REST 下令 → validator → 物理預檢（同步 <50ms）→ pending queue → 狀態機全轉移。
- **M3-2 交戰裁決** [M3-1]：SPEC_FULL §7.1 管線 + weaponeering 資料載入 + property tests。
- **M3-3 偵測與 intel store** [M3-1]：sensor sweep（k-ring 預過濾）、DETECTED/CLASSIFIED/IDENTIFIED 升級、`IntelContact` 寫入、faction-scoped 查詢。
- **M3-4 移動執行** [M2-4, M3-1]：MOVE order → path → 逐 tick 推進 + 消耗油料 stub。
- **M3-5 聚合裁決（Lanchester）** [M3-2]：營級以上單位的聚合戰鬥；與個體裁決的切換閾值。
- **M3-6 腳本對戰驗收** [M3-1..4]：純 API 驅動的兩人想定 e2e 測試（藍移動→紅偵測→交戰→戰損入帳）。✅ 此測試就是 M3 的 DoD。

### M4 前端 COP（可與 M5 平行）

- **M4-1 認證 + lobby**：login/JWT/refresh、session 列表與建立。
- **M4-2 地圖基座**：MapLibre + 離線 tile + hillshade + hex 層。
- **M4-3 WS stream + store** [M4-1]：`useSessionStream` 完整實作（HELLO/last_seq 補償/全量重同步）。
- **M4-4 單位渲染 + fog of war** [M4-2,3]：milsymbol atlas、intel 分級渲染、OFFLINE 虛影。✅ 500 單位 30 FPS（Playwright + FPS 量測）。
- **M4-5 下令 UX** [M4-4]：指令面板、precheck 結果顯示、pending/歷史列表。
- **M4-6 E2E 煙霧測試** [M4-1..5]：Playwright：登入→建局→下令→看到裁決事件。

### M5 環境模組（可與 M4 平行）

- **M5-1 Weather module 骨架** [M0-2]：`_sdk` 插件、SYNTHETIC 模式（腳本插值）、payload schema 驗證。
- **M5-2 CWA LIVE 模式** [M5-1]：API 拉取、格網化、stale 降級。
- **M5-3 天氣效果整合** [M5-1, M3-2]：`EnvSnapshot` 納入天氣係數；裁決/移動/UAV 受影響（整合測試證明可觀測差異）。
- **M5-4 Comms 模組** [M2-3, M3-1]：鏈路預算、mesh 連通、ONLINE/DEGRADED/OFFLINE 後果（指令延遲/凍結）。

### M6 AI Phase 1

- **M6-1 vLLM client + RoleManager**：OpenAI-compatible client、LoRA 熱切換、角色佇列（OPFOR 優先）、`AIInvocationLog`。
- **M6-2 Guardrail Gateway** [M6-1]：G1–G6 管線 + `guardrail_profiles.yaml` + 攔截事件。裁決覆蓋率 ≥95%。
- **M6-3 RAG 管線** [M0-3]：入庫 CLI、Qdrant collections、引用查核 API（G5 用）。
- **M6-4 五角色 prompts + schemas** [M6-1..3]：SPEC_FULL §9.1 表格全部角色；各配 eval cases。
- **M6-5 OPFOR 自主迴路** [M6-4, M3-6]:事件驅動觸發 → 產令 → 護欄 → 物理預檢 → 入 pending。✅ M3-6 想定中紅軍全程無人操作仍能合理應對。
- **M6-6 eval gate 進 CI** [M6-4]：SPEC_FULL §19.4 門檻。

### M7 想定與白軍

- **M7-1 Scenario schema + loader**：package 驗證（精確錯誤路徑）、3 官方想定之第 1 個。
- **M7-2 MSEL 觸發引擎** [M7-1, M1-3]：時間/條件觸發、ad-hoc inject API。
- **M7-3 想定編輯器** [M7-1, M4-2]：ORBAT 樹、地圖佈署、控制措施、MSEL 時間軸、匯入匯出。
- **M7-4 白軍控制台** [M7-2, M4-3]：時間控制、視角切換、注入面板、AI 監控、護欄事件流、rollback UI。
- **M7-5 RBAC 完整化** [M4-1]：SPEC_FULL §12 全角色 + faction-scope 中介層測試（RED token 永遠拿不到藍軍 ground truth——contract test 強制）。

### M8 AAR

- **M8-1 重播服務**：Ledger → 前端時間軸流式重建；書籤。
- **M8-2 統計儀表板** [M8-1]：SPEC_FULL §14.2 指標的預計算 job + 圖表。
- **M8-3 AI 敘事報告** [M6-4, M8-2]：`AAR_ANALYST` 全流程，段落引用 event id 且引用可點擊跳轉。
- **M8-4 匯出** [M8-2,3]：PDF + JSON/CSV + 匿名化。

---

## 6. 驗證與除錯速查

（以下皆在 repo root 執行，除非另註）

| 我想確認… | 指令 |
|-----------|------|
| 全部測試 | `uv run pytest` |
| 只跑 replay | `uv run pytest core/tests/replay -m golden` |
| Lint / 型別 | `uv run ruff check .` / `uv run mypy` |
| Proto 契約 | `npx @bufbuild/buf lint` |
| JSON Schema / OpenAPI | `uv run check-jsonschema --check-metaschema contracts/*.schema.json` |
| 契約與程式一致（M3 後） | `uv run schemathesis run contracts/core_api.yaml --base-url http://localhost:8000` |
| DB schema drift | `uv run python ops/tools/schema_sync_check.py` |
| Ledger 完整性（M1-2 後） | `uv run python ops/tools/verify_ledger.py --session <id>` |
| AI eval（M6 後） | `uv run python -m matso_ai.evals.run --all` |
| 前端 lint / 型別 | `cd platform && npm run lint && npm run typecheck` |
| E2E（M4-6 後） | `cd platform && npm run test:e2e` |
| 整組服務 | `cd ops/compose && docker compose up -d --build --wait` |

---

## 7. PROGRESS.md 維護規則（AI Agent 交接協定）

`PROGRESS.md` 是跨 session / 跨 agent 的唯一進度事實。格式：

```markdown
# MATSO 進度帳本
## 目前狀態摘要（3 行內，最新在上）
## 任務板
| 卡號 | 狀態(TODO/DOING/DONE/BLOCKED) | 認領者 | PR | 備註 |
## 決策記錄（重大取捨，一行一條，附日期）
## Backlog / 發現的問題
## 下一步建議（給下一個接手的 agent）
```

規則：
1. 每完成或放下一張卡 **立即** 更新，不要等收工。
2. `BLOCKED` 必須寫明卡在哪（缺什麼檔案/決策/依賴卡）。
3. 「下一步建議」永遠保持可直接執行的粒度（例：「做 M2-3，注意 GRASS 對照測試的安裝見 modules/terrain/README」）。
4. 架構級決策同時寫入 `docs/adr/NNN-title.md`（Context/Decision/Consequences 三段即可）。

---

## 8. 常見陷阱（教訓預載）

- **時區**：CWA 資料是 UTC+8，模擬時間是抽象 tick——在 Weather module 邊界一次轉換，內部永遠用 tick。
- **H3 邊界**：台灣跨多個 H3 base cell，k-ring 在 res 邊界不會出錯但 `h3.grid_distance` 對遠距離可能拋例外——路徑規劃一律用自己的 A*，不要用 h3 內建距離當 heuristic 以外的用途。
- **Prisma Bytes 欄位**：MariaDB LONGBLOB 上限注意 checkpoint 大小；>16MB 的快照要分片或改物件儲存（已留 ADR 議題）。
- **vLLM LoRA swap**：切換非零成本，RoleManager 必須批次同角色請求；不要在 tick loop 內同步等待 AI。
- **WebSocket 背壓**：慢客戶端會塞爆 broadcaster——per-client send queue 上限 + 斷線重同步，不要無限緩衝。
- **量化模型**：WARBENCH 顯示 4-bit 模型 IHL 違規率飆升——部署 4/8-bit 時 G6 護欄自動加嚴，這不是可選項。

---

*本手冊隨里程碑演進。發現與 SPEC_FULL.md 矛盾時，以 SPEC_FULL.md 為準並開 issue 修正本文件。*
