---
task: "WP-E1 活 session checkpoint 與崩潰復原"
status: IN_PROGRESS
started: 2026-07-29T14:20+08:00
updated: 2026-07-29T14:20+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-E1；相關 SPEC_FULL §3.4、§18；ADR 002
---

# WP-E1 活 session checkpoint 與崩潰復原

## 目標摘要
活局（`sim_runtime`）從未落過任何 checkpoint——Kernel 的 checkpointer 參數自 O1.5 就在，
但組裝時沒傳。連帶：崩潰只能靠 DB 殘存值重建、ROLLBACK 端點無快照可回滾、
RNG 每次重啟從種子重跑同一序列。本卡把 O1.5 的機件接到活執行期並補上復原路徑。

## 開工掃描：規格五項 vs 實際斷點

| 規格項 | 實際狀況 | 處置 |
|---|---|---|
| (1) runner 掛 checkpointer | Kernel 介面完備，`sim_runtime` 沒傳 | 接上 + 間隔進 SimParams |
| (2) RNG get_state/set_state | 完全沒有出入口；活局有 3 條 stream | 新增；三條都存 |
| (3) 內容擴充：熱狀態＋RNG＋pending orders＋MSEL | **pending orders 早已全在 DB**（`Order.status`／`payload._leg`）；**MSEL 執行期不存在**（`trigger_checker=NoOpTriggerChecker`，WP-B2 未做） | 熱狀態＋RNG＋**order 狀態快照**（rollback 需要）；MSEL 留鉤子 |
| (4) 重啟自動 recover ＋前滾 | production **無任何 `recover()` 呼叫端** | 新增 resume 路徑 |
| (5) ROLLBACK 端點接活 | 只發 WS 事件；且跨行程直寫熱狀態會被 runner 的 mirror cache 蓋掉 | PAUSE → rollback → restart 旗標 |

### 掃描另外發現的三個斷點（規格未列）
1. **`SimClock` 每次 runner 啟動都從 tick 0 重來**（`sim_runtime.py` 建 `SimClock(tick_rate_ms=...)`，
   不帶 `start_tick`）。→ 每次重啟／restart 旗標重建，該局的 sim tick 歸零：Ledger 的 tick 倒退、
   `issued_at_tick` 歸零（指令排序壞）、comms 延遲閘門的 `now_tick` 倒退、victory 的 time 條件永遠不到。
   這比「沒有快照」更嚴重，且**每次重啟都會發生**（不需崩潰）。
2. **`seed_combat_state` 無條件以 DB 座標覆寫熱狀態**（血量/彈藥有 `if not in existing` 保護，座標沒有）
   → 復原順序必須讓它跑在 recover 之後，否則復原的座標會被 DB 值蓋掉。
3. **Kernel 於 `tick % interval == 0` 落快照 → tick 0 必落一筆**。tick 歸零的既有 bug 加上這點，
   會讓「tick=0 但 ledgerSeq 很大」的快照被 `load_latest`（依 ledgerSeq）選中，
   且 `(session, tick)` 唯一約束會覆蓋掉真正的初始快照。(1) 與 (4) 必須一起做才安全。

## 規格衝突：「Ledger 尾段截斷」不做（改世代標記）
規格驗收寫「rollback 後 Ledger 尾段正確截斷（hash chain 重錨定，設計須記 ADR）」。
現行設計三道防線都指向相反方向：
- `LedgerWriter` 刻意只有 `append`（無 update/delete）；
- DB 權限層 `ops/tools/grant_ledger_readonly.sql` 對 `matso_app` REVOKE UPDATE/DELETE；
- `verify_chain()` 第一條就檢查「seq 自 0 起連續」——**實體截斷 = 驗證失敗**（缺號＝被竄改）。

證據鏈可被刪＝防竄改性歸零。故本卡採**邏輯截斷**：ROLLBACK 事件本身成為新錨點，
`detail` 記下被棄世代的 seq 區間，消費端（AAR/replay）依此濾除。決策記 ADR 007。

## 計畫
- [ ] C1 `DeterministicRNG.get_state/set_state`（+ 測試釘住決定性不變）
- [ ] C2 `SimParams.checkpoint_interval_ticks` + 契約 + runner 掛 `CheckpointManager`
- [ ] C3 快照 envelope v2（units + rng + orders）+ resume 路徑（recover + 前滾 + start_tick）
- [ ] C4 ROLLBACK 接活 + 可回滾點列表端點 + ADR 007
- [ ] C5 文件（worklog / PROGRESS / PARAMS）+ 全關卡

## 執行紀錄
- `14:20` 開工掃描完成（4 條平行讀者）。確認 golden 路徑不傳 checkpointer → 本卡對 golden 零影響，
  但三條紅線不可碰：`compute_state_hash` 語義、`DeterministicRNG` 抽樣序列、tick 內子系統順序。

## 中斷續作指引
- **下一步第一件事**：C1（rng.py get_state/set_state）。
- **尚未驗證的假設**：numpy PCG64 的 128-bit state 經 canonical_json → zstd → json.loads 無損（要寫測試）。
