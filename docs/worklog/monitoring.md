---
task: V2.1 WP-E4
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-E4 監控落地

## 使用者裁示：不加容器

規格寫「compose 增 prometheus+grafana 服務」。動手前先問，因為那是**部署決定不是程式決定**：
Grafana 預設埠 3000 **已被前端占用**，而且 air-gapped 部署每多一個映像就多一件要打包的事。
使用者選了「metrics only」——`/metrics` 端點 + 儀表板/告警規則留成檔案，由既有監控接手。

## 兩個關鍵設計

**1. 行程內註冊表，因為 runner 與 API 同一個行程。** `SimManager` 由 `main.py` 的 lifespan
啟動，所以 tick 量測與 `/metrics` 共用記憶體，不需要 Redis 之類的載體。
⚠ 這是**前提不是巧合**——哪天 runner 被拆成獨立行程，這個模組會安靜地只回報 API 行程
看得到的部分（tick 指標全部歸零）。註解寫在模組頂端留給那時候的人。

**2. 指標不帶 session/單位標籤。** 兩個獨立理由，任一個都足夠：
- **基數爆炸**：每開一局多一組時間序列，長期堆積會拖垮 Prometheus。
- **`/metrics` 不驗證身分**（Prometheus 機器對機器抓取）。把 session id 放進去等於把
  「有哪些推演正在跑」公開出去。**指標回答「系統健康嗎」，不回答「誰在打誰」。**
有一條測試掃過所有輸出，斷言唯一合法的標籤是直方圖的 `le`。

## 為什麼自己寫 exposition 而不是 `prometheus_client`

與「不加容器」同一個理由（air-gapped 少一個相依），而且換到完全的可控性。
**代價是要自己把格式寫對**——而我第一版就寫錯了（見下）。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/metrics.py | 新增 | 註冊表 + 9 個具名指標 + Prometheus 文字輸出 |
| core/app/main.py | 修改 | `GET /metrics`（`include_in_schema=False`、**永不拋**） |
| core/app/engine/kernel.py | 修改 | tick 時長 + 超時計數（**不寫帳本**——那是牆鐘觀測不是模擬事實） |
| core/app/state/broadcaster.py | 修改 | WS 扇出量 |
| core/app/sim_runtime.py | 修改 | 執行中局數 gauge |
| core/app/ai_loop/worker.py | 修改 | 護欄攔截計數 |
| ai/matso_ai/inference/role_manager.py | 修改 | LLM 延遲（**ai 不硬相依 core**，取不到就算了） |
| ops/monitoring/ | 新增 | scrape 片段、告警規則、3 份 Grafana 儀表板、README |
| core/tests/unit/test_metrics.py | 新增 | 13 條 |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1934 passed, 8 skipped, 4 deselected**
- `core/tests/replay` → **8 passed（golden 未重錄）**——量測不入帳本，故不影響雜湊鏈
- ruff / mypy(265) → clean；儀表板 JSON 與規則 YAML 皆可 parse
- 突變測試 5 個全數被抓（含我自己犯過的那個）

## 決策與陷阱

**⚠ 直方圖累積做了兩次。** `observe()` 對每個 `value <= upper` 的桶都 +1（counts 已是累積），
`render()` 又累加一次 → 桶數超過 `_count`，**Prometheus 算出來的分位數是錯的**。
而且錯得看起來很合理：曲線仍然單調遞增，只有把 `+Inf` 與 `_count` 對起來才看得出破綻。
**是我印出樣張逐行看才發現的**，不是測試發現的——測試是之後補的。
這正是自己寫 exposition 的代價，也是為什麼那一組測試要逐字釘住輸出。

**tick 量測不寫帳本。** 它是**牆鐘**的觀測，不是模擬事實；放進 Ledger 會讓同一份想定
在不同機器上算出不同的 hash。

**`/metrics` 永不拋。** 一個壞掉的指標不該讓抓取整個失敗——那會讓監控在最需要它的時候
（系統有問題時）先掛掉。

**告警門檻寫得保守。** 會叫的告警才有人看，一直叫的告警會被靜音。
`MatsoAiWorkersAllDown` 特別要求 `matso_active_sessions > 0`——沒有推演時 worker 為 0
是正常的，單看 worker 數會一直誤報。

## 中斷續作指引

- **下一步第一件事**：G3 E2E 補齊 + G4 白軍控制台，然後 V2.1 exit 的 CPX 驗收。
- **未竟項**：
  1. **`matso_io_latency_ms` 與 `matso_ai_workers` 尚無寫入端**——指標定義好了但沒有
     instrumentation 點（DB/Redis 要包一層計時器；AI worker 數要在 orchestrator 那側數）。
     **這是「定義了但沒接」，正是本 session 抓過三次的病**，所以明寫在此而不是假裝做完了。
  2. 儀表板未在真的 Grafana 上開過（只驗了 JSON 可 parse）。
  3. 告警規則未在真的 Prometheus 上跑過 `promtool check rules`。
