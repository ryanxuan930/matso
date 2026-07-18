# core/ — Core Orchestrator

FastAPI + Python 3.12。規格：SPEC_FULL.md §2–3, §7–8, §10, §16；實作指南：HOW_TO.md §4.1–4.2。

```
app/
├── api/           # REST + WS 端點（薄層：IO、授權、faction-scope）
├── engine/        # SimClock, DeterministicRNG, Kernel tick loop, movement, sensors, logistics
├── adjudication/  # ★ 純同步純函數裁決引擎 — 不碰 DB/Redis/時鐘/RPC
├── guardrails/    # Guardrail Gateway G1–G6
├── intel/         # per-faction fog of war store
├── orders/        # Order validator + 狀態機
├── plugins/       # Plugin registry、gRPC clients、circuit breaker
├── state/         # Redis 熱狀態（Kernel 為唯一寫入者）、checkpoint、ledger writer
└── models/        # SQLAlchemy models（唯讀跟隨 db/prisma/schema.prisma）
tests/
├── unit/  property/  replay/  integration/
```

紅線（HOW_TO §0）：模擬邏輯禁用牆鐘與裸 random；AI 永不裁決物理；護欄不可 bypass。
起點任務：M0-1（uv workspace root 就設在此目錄或 repo root，由認領者決定並記入 ADR）。
