# MATSO 監控（WP-E4）

**使用者裁示（2026-07-30）：不加 prometheus/grafana 容器。** 這裡只放設定與儀表板，
由既有的監控系統接手。理由有二：Grafana 預設埠 3000 已被前端占用；air-gapped 部署
每多一個映像就多一件要打包的事。

## 接上去

1. **抓取**：把 `prometheus-scrape.yml` 的 job 併進你的 `prometheus.yml`。
   端點是 `GET /metrics`（core，預設 8000）。
2. **告警**：把 `alerts.yml` 放進 Prometheus 的 `rule_files:` 指向的目錄。
3. **儀表板**：`dashboards/*.json` 直接 import 進 Grafana。

## 指標一覽

| 指標 | 型別 | 意義 |
|------|------|------|
| `matso_tick_duration_ms` | histogram | Kernel 單 tick 牆鐘時長 |
| `matso_tick_total` | counter | 已完成 tick 數 |
| `matso_tick_overrun_total` | counter | 超出 tick 預算的次數 |
| `matso_ws_fanout_total` | counter | WS 事件扇出總則數 |
| `matso_llm_latency_ms` | histogram | LLM 單次呼叫延遲 |
| `matso_guardrail_blocked_total` | counter | 護欄攔截次數 |
| `matso_io_latency_ms` | histogram | DB/Redis 單次操作延遲 |
| `matso_active_sessions` | gauge | 執行中的推演局數 |
| `matso_ai_workers` | gauge | 執行中的 AI 決策 worker 數 |

## 兩件要知道的事

**指標不帶 session/單位標籤。** 兩個獨立理由：基數爆炸（每開一局就多一組時間序列），
以及 `/metrics` 通常不驗證身分——把 session id 放進去等於把「有哪些推演正在跑」公開出去。
**指標回答「系統健康嗎」，不回答「誰在打誰」。**

**`/metrics` 不驗證身分。** Prometheus 是機器對機器抓取，塞 bearer token 只會讓部署變複雜。
正式部署請在反向代理層限制來源網段。
