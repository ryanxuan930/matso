---
task: V2.1 WP-C7.3
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C7.3 修復與人員補充（C7 收尾）

## 目標摘要

[JCATS-A p.26–27]：**「絕非申請後直接恢復戰力」**，且要「**於後方恢復再前送**」。
這張卡的整個重點就是那兩句話：整補要花時間，而且不能在前線做。

## 三個前提，缺一不整補

1. **在補給點半徑內**（C7.2 的 `nearest_usable`）——後方，不是隨便哪裡。
2. **附近沒有敵軍**（`SAFE_DISTANCE_M` 5 km）——落實「前線不整補」。
3. **有 Class IX**（維修件）——修復要料，且料件是**硬上限**（有多少料修多少）。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/engine/refit_wiring.py | 新增 | 三個前提、遭襲中斷、按經過 tick 累積修復、`REFIT_*` 事件 |
| core/app/sim_params.py | 修改 | `repair_per_day`（**0＝不修復＝中性**） |
| core/app/sim_runtime.py | 修改 | `_refit_tick` 掛 pre_tick（`repair_per_day<=0` 連 DB session 都不開） |
| core/tests/unit/test_refit.py | 新增 | 11 條（含驗收條文） |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1889 passed, 8 skipped, 4 deselected**
- `core/tests/replay` → **8 passed（golden 未重錄）**
- ruff / mypy(263) / schema-sync → clean（**無 DB migration、無契約變更**）
- **驗收條文達成**：`test_being_fired_on_interrupts_refit` ——
  開始整補 → 挨一發 → `REFIT_BLOCKED(UNDER_ATTACK)`、戰力沒有恢復、計時歸零
- 突變測試 **7 個全數被抓**

## 決策與陷阱

**遭襲判定用壓制度，不用戰力下降。** 被射擊就會累積壓制（WP-C1），那是「遭襲」最直接
而且**已經存在**的訊號。用戰力下降判會慢一拍（要真的被打掉人才算），而整補該在
第一發子彈打過來時就停。

**中斷時計時歸零**（`REFIT_TICK_KEY: None`）。不歸零的話，被打斷再回來會把中斷期間
也算成整補時間——那等於「一邊挨打一邊修車」。

**第一個 tick 只計時不修**——「絕非申請後直接恢復戰力」的字面落實。

**敵軍距離用真值，那是刻意的。** 紅線 3 管的是**玩家**的迷霧，不是物理。一支部隊知不知道
「附近有敵人」不需要它先偵測到（槍聲、車聲、上級通報都算）。而且這條規則的效果是
**限制自己**（不准整補），不是給予資訊優勢；`REFIT_BLOCKED` 也只說「附近有敵軍」
不說是誰、在哪。

**受阻事件只在狀態改變時發**，否則每 tick 都會洗版。

**修復按經過 tick 補算**（同 C7.1 補給消耗）——每 tick 加一點會累積浮點誤差，
而且回滾之後對不起來。

## 中斷續作指引

- **下一步第一件事**：C7 三卡全數完成，往 F3/F1。
- **未竟項**：
  1. **人員補充速率未獨立**。規格說「人員補充速率（人/模擬日）於想定定義」；
     本卡把裝備修復與人員補充合成同一個 `repair_per_day`（都作用在 strength 上）。
     要分開得先有 #48 的單位編成組成（人 vs 裝備分帳）。
  2. **想定不能設 `repair_per_day` / `SAFE_DISTANCE_M`**——目前只能改 SimParams。
  3. 前端不顯示整補狀態（`REFIT_*` 事件已進帳本，但 COP feed 沒有文案）。
