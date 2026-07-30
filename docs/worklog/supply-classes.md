---
task: V2.1 WP-C7.1
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C7.1 補給類別最小集

## 目標摘要

[JTLS-F p.1058] Class I–X 與再訂購水位；[JCATS-A p.26–27]「絕非申請後直接恢復戰力」。
本卡做 Class I（口糧/水）與 IX（維修件）的存量、消耗、斷補效能與再訂購水位。
C7.2（補給線/補給點）、C7.3（修復/人員補充）另開卡。

## 與規格不同的裁決

**1. 不把 Class III/V 搬進新體系。** 規格列出四個類別，其中 III（油料，#84）與 V（彈藥，#44）
**已經有能用的模型與測試**。為了「類別體系整齊」而重寫，換到的是一次大改與一輪回歸風險，
換不到任何行為。本卡只補真正缺的兩個，並提供一份共用的水位語義（`is_below_reorder`）
讓四個類別都能用同一套判斷。

**2. golden 不必重錄。** 規格把 C7 標成「golden：重錄」。中性預設守住就不必：
消耗率預設全 0、`supply` 缺鍵回**空 dict**（不是「全部 0」）、`tick_supply` 對空 dict
直接回 None ——既有局**一個熱狀態鍵都不會被寫**。實測 8 個 golden 未動。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/adjudication/supply.py | 新增 | 純函數：`SupplyLevel`（`capacity<=0`＝未編制）、`consume`、`starvation_modifier`（階梯） |
| core/app/engine/supply_wiring.py | 新增 | 熱狀態存取 + `tick_supply`（按經過 tick 補算）+ `supply_effectiveness` |
| core/app/adjudication/adjudicator.py | 修改 | 斷補 → 射手效能倍率 |
| core/app/sim_params.py | 修改 | `supply_daily_rates`（空 dict＝全 0＝中性） |
| core/app/sim_runtime.py | 修改 | `_supply_tick` 掛 pre_tick（與壓制衰減同位置） |
| core/tests/unit/test_supply.py | 新增 | 16 條 |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1866 passed, 8 skipped, 4 deselected**
- `core/tests/replay` → **8 passed（golden 未重錄）**
- ruff / mypy(261) → clean（**無 DB migration、無契約變更**）
- 突變測試 6 個全數被抓（其中一個靠突變才發現測試不夠，見下）

## 決策與陷阱

**`capacity <= 0` ＝「未編制」，不是「空的」。** 未編制的類別不消耗、不觸發再訂購、不扣效能
——否則每個單位都會為它沒有的東西不斷申請補給。

**缺鍵回空 dict，不是「全部 0」。** 後者會讓每個既有單位看起來都處於斷補狀態。

**按經過 tick 補算，不每 tick 扣一點。** 每 tick 扣 `rate/ticks_per_day` 會累積浮點誤差，
而且 checkpoint 回滾之後帳目就對不起來。記「上次結算的 tick」，回滾把它一起帶回去，帳自動一致。

**沒有實際消耗時連時間戳都不寫**——完全沒有消耗的局不該每 tick 推一次 STATE_DIFF。

**`write_levels` 依類別名排序**：熱狀態會進 `compute_state_hash`，dict 順序不穩就會讓
同一個世界算出不同的雜湊。

**斷補只看 Class I**：口糧斷了才是「斷補」；維修件（IX）見底影響的是修復，不是即刻戰力。

**斷補乘進射手效能而非命中率**——餓肚子影響的是這支部隊整體發揮（人累、裝備沒保養），
不是彈道。

**⚠ 又一條測試被突變測試修正。** 「零消耗率不寫任何東西」那條**碰不到它要驗的 guard**：
測試沒有給 `supply_tick`，於是 `elapsed == 0` 就先回 None 了。要走到「有經過時間、
但消耗量是 0」那條路才驗得到。這是本 session 第四次靠突變測試發現測試斷言不到位。

## 中斷續作指引

- **下一步第一件事**：C7.2（再訂購水位與補給線、`SUPPLY_POINT`、打擊敵後勤）。
  `needs_resupply()` 已備好當觸發線。
- **未竟項**：
  1. **LOGISTICS capacity 的類別分艙未擴**——契約現有 `capacity: {AMMO/FUEL/WATER_FOOD/BATTERY}`，
     與本卡的 Class I/III/V/IX 命名不一致。要對齊得改契約（紅線 4，宜與 C7.2 一起）。
  2. **想定/ORBAT 不能宣告單位的 `supply` 初值**——目前只能由 API 或 MSEL 注入。
  3. 前端不顯示補給水位與斷補狀態。
  4. Class IX 的消費端在 C7.3（修復）。
