# ops/ — 部署與工具（SPEC_FULL §20）

| 目錄 | 內容 |
|------|------|
| `compose/` | Phase 1 Docker Compose 拓撲（AI 節點在外部主機，OPENAI_BASE_URL 指向） |
| `prometheus/` `grafana/` | 觀測性設定；核心 metrics：tick_duration_ms, ai_queue_depth, guardrail_blocks_total 等 |
| `tools/` | CI 工具：`schema_sync_check.py`（M0-4）、`verify_ledger.py`（M1-2）、`rerecord_golden.py`（M1-6） |

告警三件套：TICK_OVERRUN、plugin DOWN、AI 逾時率 >20%。
