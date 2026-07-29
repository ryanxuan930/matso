---
task: "WP-E1 活 session checkpoint 與崩潰復原"
status: DONE
started: 2026-07-29T14:20+08:00
updated: 2026-07-29T18:05+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-E1；相關 SPEC_FULL §3.4、§18；ADR 002、ADR 007（本卡新增）
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
| (3) 內容擴充：熱狀態＋RNG＋pending orders＋MSEL | **pending orders 早已全在 DB**（`Order.status`／`payload._leg`）；**MSEL 執行期不存在**（`trigger_checker=NoOpTriggerChecker`，WP-B2 未做） | 熱狀態＋RNG＋**Order 狀態快照**（rollback 需要）；MSEL 無資料源，未做 |
| (4) 重啟自動 recover ＋前滾 | production **無任何 `recover()` 呼叫端** | 新增 `state/resume` 路徑 |
| (5) ROLLBACK 端點接活 | 只發 WS 事件；且跨行程直寫熱狀態會被 runner 的 mirror cache 蓋掉 | PAUSE → 排入請求 → restart → runner 執行 |

### 掃描與實測另外發現的四個斷點（規格未列）
1. **`SimClock` 每次 runner 啟動都從 tick 0 重來**（`SimClock(tick_rate_ms=...)` 不帶 `start_tick`）。
   每次重啟／restart 旗標重建，該局 sim tick 歸零：Ledger tick 倒退、`issued_at_tick` 歸零
   （指令排序壞）、comms 延遲閘門的 `now_tick` 倒退、victory 的 time 條件永遠不到。
   這比「沒有快照」更嚴重，且**每次重啟都會發生**（不需崩潰）。
2. **`seed_combat_state` 無條件以 DB 座標覆寫熱狀態**（血量/彈藥有 `if not in existing` 保護，
   座標沒有）→ 它一執行，復原路徑就分辨不出「Redis 是否已空」。故移到 resume 之後。
3. **Kernel 於 `tick % interval == 0` 落快照 → tick 0 必落一筆**。加上斷點 1，
   會產生「tick=0 但 ledgerSeq 很大」的快照被 `load_latest` 選中，且 `(session, tick)`
   唯一約束會覆蓋掉真正的初始快照。故 (1) 與 (4) 必須一起做。
4. **`load_latest` 只依 ledgerSeq 排序，同分時「最新」未定義**（**容器實測發現**）：
   推演閒置時帳本沒有新事件，一整串快照共用同一個 ledgerSeq（實測 8 筆全是 134467），
   DB 想回哪筆就回哪筆 → 復原可能倒退一整個間隔。改為 `(ledgerSeq DESC, tick DESC)`，
   並讓 rollback 一併刪掉「同 seq 但 tick 更晚」者（否則決勝鍵會挑回剛被回滾掉的那個）。

## 規格衝突的裁決：「Ledger 尾段截斷」不做實體刪除（ADR 007）
規格驗收寫「rollback 後 Ledger 尾段正確截斷（hash chain 重錨定，設計須記 ADR）」。
現行設計三道防線都指向相反方向：`LedgerWriter` 只有 `append`；DB 權限層對 `matso_app`
REVOKE UPDATE/DELETE；`verify_chain` 第一條就檢查「seq 自 0 連續」——**實體截斷 = 驗證失敗**。

證據鏈若能被合法刪除，「鏈驗證通過」就不再能證明「沒有東西被刪過」，防竄改性歸零。
故採**邏輯截斷**：ROLLBACK 事件的 `detail` 記下被棄世代的 seq 區間，
`superseded_seqs()` 供消費端過濾（`aar/events.read_events` 已接）。完整論證見 ADR 007。

## 交付
| 檔案 | 動作 | 說明 |
|------|------|------|
| `core/app/engine/rng.py` | 修改 | `get_state`/`set_state`（PCG64 state 深拷貝 + stream 身分驗證） |
| `core/app/state/resume.py` | **新增** | `resume_tick` / `forward_roll` / `restore_rng` / `resume_session` / `apply_pending_rollback` |
| `core/app/state/checkpoint.py` | 修改 | 信封 v2（units/rng/orders）+ `extras_provider` + `list_points` + rollback 回捲令與世代標記 + ledgerSeq 同分決勝 |
| `core/app/state/ledger.py` | 修改 | `superseded_seqs()`（ADR 007 的邏輯截斷讀取端） |
| `core/app/state/hot_state.py` | 修改 | `session_tick_key()`——原本散在 4 個模組各自重複的字面值 |
| `core/app/aar/events.py` | 修改 | `read_events` 排除被回滾棄置的世代 |
| `core/app/sim_runtime.py` | 修改 | 掛 checkpointer、持有三條 RNG、resume/rollback 接線、`seed_combat_state` 移後 |
| `core/app/sim_params.py` | 修改 | `checkpoint_interval_ticks`（預設 600 ≈ 5 分鐘牆鐘） |
| `core/app/sim_control.py` | 修改 | `session_rollback_key()` |
| `core/app/api/control.py` | 修改 | ROLLBACK 接活（驗證 + 排入請求 + 暫停 + restart）；新增 `GET /checkpoints` |
| `core/app/errors.py` | 修改 | `RollbackTargetNotFoundError.http_status = 404` |
| `contracts/core_api.yaml` | 修改 | `checkpoint_interval_ticks`、`ControlResponse`、`CheckpointView`、`/checkpoints`、error code |
| `platform/app/pages/system-settings.vue`、`types/api.ts` | 修改 | 快照間隔輸入框（由契約重生型別） |
| `docs/adr/007-rollback-logical-truncation.md` | **新增** | 規格衝突的裁決 |
| `core/tests/unit/{test_rng,test_resume,test_control_api}.py` | 新增/修改 | 共 42 條 |
| `core/tests/integration/test_checkpoint_recovery.py` | 修改 | 真 Kernel 崩潰復原驗收（2 條） |

## 設計決定
1. **快照間隔存 tick 而非牆鐘秒**：Kernel 判 `tick % interval == 0`，快照點必須落在模擬時間的
   確定位置；牆鐘會隨 `TickPacer` 的過載降頻漂移。600 tick ≈ 5 分鐘牆鐘（@ 預設 0.5s/tick）。
2. **RNG 走 `extras_provider` 注入，不擴 `Checkpointer` 介面**：RNG 實例的持有者是 runner
   （子系統把它們藏成私有欄位，Kernel 拿不到），擴介面會一路改到 Kernel 與 golden harness。
3. **`stateHash` 只涵蓋 units 子樹**：驗收比對的「狀態雜湊」與 golden 的
   `compute_state_hash(hot.get_all())` 必須是同一個東西，混入 RNG/Order 會讓兩者脫鉤。
4. **只有熱狀態是空的才從快照還原**：core 崩潰但 Redis 存活時熱狀態比快照新，硬套等於把
   進度倒退一個間隔。RNG 相反——它只活在記憶體，任何重啟都要還原。
5. **前滾是投影不是重播**：Kernel 的輸入（DB 指令、感測掃描、AI 決策）沒被錄下來，沒東西可重跑。
   但事件本身記著結果值（移動帶 lat/lng、交戰帶 target_health/strength_after），依 seq 套用
   即可推回崩潰當下。投影時**不做型別正規化**（多一道 `float()` 會讓 `0` 變 `0.0`，雜湊就對不上）。
6. **rollback 由 runner 執行而非 API 行程**：`RedisHotState` 有 in-process mirror cache，
   API 直寫 Redis 跑中的 runner 看不到、下一個 tick 就蓋回去。API 只記請求 + 設 restart 旗標，
   舊 runner 收工後由掃描層重建，此時世上只有一個熱狀態寫入者（紅線）。沿用
   `live_position`／`live_ammo` 的命令通道紀律。
7. **rollback 回捲 Order 狀態、繞過狀態機**：狀態機管的是生命週期的合法前進，回滾是時間旅行——
   COMPLETED 退回 EXECUTING 在生命週期上非法、在時間軸上正確。硬塞進狀態機會讓它失去
   真正的用途。回滾點之後才下的令標 CANCELLED 而非刪除（稽核紀錄要留）。

## 已知限制（有意識的取捨，非疏漏）
- **RNG 只還原到快照當下，不是崩潰當下**：快照後消耗掉的抽樣次數沒有任何地方記著
  （Ledger 記結果不記抽樣），故最多倒退一個快照間隔的隨機序列。仍嚴格優於從種子重來。
  整合測試 `test_live_kernel_crash_and_resume_matches_state_hash` 把這個界線釘住。
- **前滾重建不到**：逐 tick 油耗（每 tick 改熱狀態但不發事件）、彈藥（事件不帶餘量，
  但 DB `EquipmentInstance` 有、`seed_combat_state` 會補）、`comms_state`（下個重算週期即回復）。
- **重跑窗**：崩潰若落在「Ledger 已寫、tick 鍵未更新」之間，該 tick 會重跑一次。
  已 drain 的指令狀態早已 commit，不會重跑第二次；重複的是移動步進。at-least-once 比漏跑安全。
- **MSEL 已觸發集未做**：`trigger_checker=NoOpTriggerChecker()`，WP-B2 之前沒有資料源。
  信封已是開放結構，屆時加一個 `msel` 區段即可。
- 前端只加了「快照間隔」設定欄；白軍 UI 的「挑書籤回滾」面板未做（端點與清單 API 已備）。

## 測試證據
- 新增 42 條單元 + 2 條整合；**`uv run pytest` → 1183 passed / 8 skipped**。
- golden 6 未破（`pytest core/tests/replay -m golden` 全綠）——活執行期掛 checkpointer
  對 golden 零影響（golden 自建 Kernel 不傳 checkpointer）。
- ruff / ruff format / mypy(208) / schema-sync(16 表 142 欄) / OpenAPI 驗證 / buf lint 全綠。
- 前端 `npm run lint` + `vue-tsc` 綠；`npm run gen:api` 由契約重生型別；容器實測畫面正確。

### 容器實測（驗收條文逐項）
```
[1] 重啟不歸零：rebuild core 前 tick=7235 → 後 tick=7350（改版前會回到 0）
[2] 活局落快照：間隔暫調 25 tick → SimCheckpoint 每 25 tick 一筆（壓縮後 6833 B / 4 單位）
[3] kill -9 + 清 Redis 熱狀態 → docker start：
    「session e2e-orders 熱狀態已遺失，自 checkpoint tick=7500 復原（前滾 0 則事件，RNG 3 條）」
    崩潰前雜湊 eaddaa3e…9fb582 == 復原後雜湊 eaddaa3e…9fb582  ✓
    崩潰於 tick 7520、快照於 7500 → 在 1 個快照間隔內自動恢復  ✓
[4] 活回滾（排入請求 → runner 重建時執行）：
    「已回滾至 tick=7450（棄置快照 5 份、Ledger seq 134468–134467、令還原 171/取消 0）」
    ROLLBACK 事件 seq=134468 落帳、detail 帶 superseded 區間；tick 自 7800 → 7451 續跑  ✓
    帳本一列未刪（實體截斷會讓 verify_chain 紅）  ✓
[5] 同 ledgerSeq 的 8 筆快照 → 決勝鍵修正後 load_latest 穩定取 tick 最大者  ✓
```
實測後已把暫調的 `checkpoint_interval_ticks` 移除、清乾淨自行設下的暫停旗標，該局續跑中。

## 中斷續作指引
- **本卡已全部完成並實測**。無未竟項。
- 後續相關：WP-B2（MSEL 執行期）做完後，把「已觸發集」加進快照信封的 `msel` 區段。
- 白軍 COP 的「回滾書籤」UI 尚未做——`GET /sessions/{id}/checkpoints` 已可列出候選 tick。
