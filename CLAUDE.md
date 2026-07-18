# MATSO — AI Agent 必讀（本檔會被所有 Claude agent 自動載入）

AI 輔助兵棋推演系統。Neuro-Symbolic 架構：確定性物理引擎（Python）與 LLM 戰術推理嚴格分離。

## 文件地圖（權威順序）
- **SPEC_FULL.md** — 系統規格的唯一權威（SPEC.md 是歷史文件，勿改勿依）
- **TASKS.md** — 任務板；使用者說「開發 O1.1」即指此檔的任務條目
- **HOW_TO.md** — 工程規範（§0 Agent 守則、§3 程式規範、§4 實作指南、§8 陷阱）
- **PROGRESS.md** — 跨 session 進度帳本
- **docs/worklog/O*.md** — 各任務工作日誌；**docs/adr/** — 架構決策

## 強制流程
**開工前**：
1. 讀 PROGRESS.md（現況 + 下一步建議）。
2. 被指派 O 任務 → 讀 TASKS.md 該條目 + 其「規格」欄列的 SPEC_FULL/HOW_TO 章節。
3. 檢查 `docs/worklog/O<id>.md` 是否已存在——存在＝接續任務，讀「中斷續作指引」後接手，不重做已完成步驟；不存在＝從 `docs/worklog/_TEMPLATE.md` 複製建立。

**開發中**：每完成一個實質步驟（檔案、測試綠、決策）立即更新 worklog；每個綠燈點 commit（訊息含任務編號）。

**收工/中斷前**：worklog 的「中斷續作指引」更新到最新 → 跑驗收指令 → 更新 PROGRESS.md 任務板 → commit。

## 紅線（違反 = 錯誤實作，完整版見 HOW_TO §0）
1. 模擬邏輯禁用 `datetime.now()`/`time.time()`/裸 `random`——一律 `SimClock` 與 `DeterministicRNG`。
2. AI（LLM）永不裁決物理事實（命中/可見/可達）——那是 `core/app/adjudication/` 的工作，且該目錄必須是純同步純函數。
3. Guardrail Gateway 不可加 bypass。fog of war 的 faction 過濾只能在後端。
4. 契約先行：改 `contracts/` → 驗證 → 再實作。DB 變更只走 `prisma migrate`（schema 權威 = db/prisma/schema.prisma）。
5. 一次只做一張任務卡；範圍外問題記入 PROGRESS.md Backlog，不順手修。

## 環境速查
- Python：repo root `uv sync` / `uv run <cmd>`（單一 venv，ADR 001）；前端與 db 用 **npm**（不用 pnpm，ADR 003）。
- 服務：`cd ops/compose && docker compose up -d --wait`（**MariaDB 對外 3307**；本機 3306/8080 被使用者其他容器占用，勿動）。
- 全部關卡：`uv run pytest`、`uv run ruff check .`、`uv run mypy`、`npx @bufbuild/buf lint`、`uv run python ops/tools/schema_sync_check.py`、`cd platform && npm run lint && npm run typecheck`。
