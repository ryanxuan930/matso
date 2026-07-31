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

## 追記（2026-07-31）：斷補漏了第三條裁決路徑

活體驗收 C16 抓到——`adjudicator._resolve_combined`（聯合兵種加總）**沒有乘
`supply_effectiveness`**，而齊射與聚合兩條都有。後果是同一支部隊有兩種物理：操作員在下令時
點了武器下拉就走單武器路徑（會餓），不點就走聯合兵種（不會餓）。更糟的是本卡驗收條文寫的是
「斷補的**裝甲連**」，而裝甲連正是典型的多武器單位——那句條文要適用的對象，剛好落在唯一
不生效的那條路徑上。已修，並補 `test_a_starving_combined_arms_unit_fires_fewer_rounds`。

**寫測試時踩到的捨入陷阱**（值得記著）：`ammo_spent = ceil(quantity × eff × rate)`，
小編成會被 `ceil` 整個吃掉——7 支步槍 ×0.9 ＝ 6.3 → 仍然 ceil 成 7，於是**拿掉生產程式碼那一行
測試照樣綠**。建制數要夠大（測試用 70/20）差異才浮得出來。活體檢查用 90 發不是 9 發，同一個理由。

## ⏸ 中斷點（2026-07-31 12:55 收工）——回來從這裡接

### workflow 停在哪

C7 的多 agent workflow（`wf_466a04d0-08a`）六個 agent：
**3 個建置軌（10:11–10:16 全回）＋ 1 個活體驗收（12:13 回）＋ 2 個對抗式查證**。
查證只回了**一個**，第二個還在飛就收工了——**那份結果已隨關機消失，回來要重跑**。

已回的那份找到 11 條。下面全數抄錄，因為 workflow 記錄不會留到下次開機。

⚠ **除了第 1 條，其餘我都還沒獨立確認**。查證 agent 附了探針輸出，看起來紮實，
但本 repo 這一週已經有查證 agent 把「純函數對的」誤報成「接線對的」的前例——
**回來要逐條自己跑一次探針再動手**，不要照單全收。

### 1. ✅ 已修（commit 4b68d8c）

`adjudicator._resolve_combined` 沒乘 `supply_effectiveness`。探針數字：

```
VOLLEY(單武器):          吃飽掉 6.3760  斷補5日掉 1.5940  比值 0.2500
COMBINED(>=2武器未指名):  吃飽掉 5.8256  斷補5日掉 5.8256  比值 1.0000  ← 完全無效
```

### 裁定進度（2026-07-31 下午，逐條自己跑過探針）

| # | 嚴重度 | 裁定 |
|---|--------|------|
| 1 | HIGH | ✅ 成立，已修（`4b68d8c`） |
| 2 | HIGH | ✅ **成立**，已修——10 份的補給點確實撥出 30 份 |
| 3 | HIGH | ❌ **不成立**（不是程式碼缺陷）——見下方追記 |
| 4 | HIGH | ✅ **成立**，已修——簽證確實雜湊不到生效中的消耗率 |
| 5 | MEDIUM | ⚠️ 部分成立：錨點算術漏了「保養與修復共用同一份 IX」。已改錨點註解＋測試改跑生產順序 |
| 6 | MEDIUM | ✅ **成立**，已修——見底期間時鐘凍住，補給到位時被追討整場時間債 |
| 7 | MEDIUM | ✅ **成立但比報告更嚴重**——不是差一天，是斷補曲線**隨結算頻率漂**。已修 |
| 8 | MEDIUM | ✅ **成立**，已修——整補不寫 DB，`GET /units` 讀 DB 所以畫面看不到修復 |
| 9 | LOW | ⚠️ 部分成立：程式碼沒錯，是註解誇大。已改註解 |
| 10 | LOW | ✅ 成立，已改註解 |
| 11 | LOW | ❌ 不成立：`VERIFY_C7_ARMOR` 已被活體驗收自己清掉，DB 裡只剩使用者的 6 局 |

### 2. ❗ HIGH｜補給點撥交不守恆（`engine/supply_wiring.py` `draw_from`）

庫存 10.0 的補給點、3 個 `on_hand=0/capacity=10` 的同陣營單位、**同一 tick**：

```
ISSUED_TOTAL: 30.0                 ← 帳本 RESUPPLIED 事件加總
UNIT_ON_HAND: {u1: 10.0, u2: 10.0, u3: 10.0}
POINT_STOCK: 10.0 → 0.0            ← 只掉 10.0
```

**20.0 份 Class I 憑空生出。** 這條若成立，C7.2「打擊敵後勤」整個失去意義——
補給點的庫存不是真的約束。**優先級最高**。

### 3. ❌ 不成立｜整補率活系統是 0 —— 是**陳舊設定**，不是程式碼缺陷

原始碼是對的：`SimParams().repair_per_day` 與 `parse_sim_params({})` 都給 10.0。
重建 `matso-core` 容器後活 API 仍回 0.0，所以也不是陳舊映像。真正的來源是 DB：

```
integrationConfig.sim  →  24 個欄位的完整快照，其中 repair_per_day: 0.0
```

那是**設定頁在 C7.3 校準之前存下的舊預設**（當時 `REPAIR_PER_DAY` 就是 0.0）。
已透過 `PUT /system/config` 改回 10.0，活 API 驗證通過。

**⚠ 但這裡有一個更一般的坑，已記進 PROGRESS Backlog**：設定頁存檔時會回寫
**全部 24 欄**，於是「有人打開過設定頁按了儲存」等於把當時的每一個預設值永久凍結——
日後任何校準變更在那台機器上都靜默失效。這次就是它讓 C7.3 整補整個沒開起來，
而所有單元測試都是綠的（測試走的是程式碼預設，不是 DB 裡那份快照）。

### 4. ❗ HIGH｜`supply_daily_rates: {}` 的語義衝突

活系統回空表。而 `system-settings.vue:343-351` 對操作員寫著「**未列出的補給類別＝不消耗**」，
同一局的 `B-3-A` 卻是 `I 2.725/3.0`（確實在消耗）。**畫面對統裁說謊。**

連帶：WP-B4 參數凍結簽證封存的是 `to_config()` 的 `supply_daily_rates`＝空表——
**簽證封不住真正生效的消耗率**，那正是簽證存在的理由。

### 5. ❗ MEDIUM｜整補錨點在生產接線下不成立（`refit_wiring.py`）

```
A. 只跑 _refit_tick（＝校準軌測試的形狀）
   day=4 strength=100.000
B. 生產接線（_supply_tick + _refit_tick 同時跑）
   day=4 strength=96.363
   day=5 strength=96.363  ← 卡住，NO_PARTS
```

測試 `test_supply_calibration.py:439` 給的是 `IX=40.0`（不是錨點宣稱的 20），
且整個函式沒呼叫過 `_supply_tick`。**又一次「測試餵的不是引擎真的會產生的資料」。**

### 6. ❗ MEDIUM｜`supply_tick` 停止前進 → 補給後料件瞬間蒸發

```
tick 1440   supply_tick=1440
tick 2880   supply_tick=1440   ← 不再前進
tick 100000 supply_tick=1440
# auto_resupply 補回滿載 20.0 後，於 tick 101440 結算：
{'supply_tick': 101440, 'supply': {'IX': [0.0, 20.0]}}   ← 20 點在一個 tick 內消失
```

### 7. ✅ 成立，而且比報告指出的更嚴重——斷補曲線**隨結算頻率漂**

報告說「差一天」。自己跑才發現差的不是一天，是**同一個想定會因為 `tick_supply`
多久跑一次而得到不同的曲線**：

```
（修正前）滿載 3 DOS 被切斷
  逐 tick 結算    → 第 3.99 模擬日掉到 ×0.9
  一天結算一次     → 第 3.00 模擬日就掉
```

原因：`_starved_days` 只問「區間結束時是不是空的」，是的話就把**整段** elapsed
都記成斷補，不管它是第幾分鐘見底的。生產跑逐 tick 所以畫面是對的，
但重播、補算、粗粒度測試都會走出另一條曲線——而同輸入同結果是這個引擎最基本的承諾。

改成用消耗率反推見底時刻（撐 `on_hand / rate` 天，剩下的才算餓著）。順帶拿掉
`round(..., 4)`：每 tick 只加 0.000694，進位成 0.0007 是每次多算 0.8%，
階梯會提早 11 個 tick 觸發。修正後三種頻率一致：

```
逐 tick / 每 6 小時 / 每日  →  全部 4.0000
```

### 8. ❗ MEDIUM｜整補不寫 DB `current_strength`（`refit_wiring.py:229-241`）

只寫熱狀態，DB 要等下次 checkpoint。與 `adjudicator`/`movement`/`fire_wiring` 的做法不一致。

### 9–11. LOW

- `contracts/scenario.schema.json` 的 `supply_points.stock` 是 `additionalProperties: false` 且只列 I/IX，
  與新增的 `parse_class`（認商品名）矛盾——契約擋在前面，`{"FUEL": …}` 仍進不來。
- `sim_runtime.py:914` 註解說 `repair_per_day=0`（預設），與 `refit_wiring.py:57` 的 `10.0` 打架（同第 3 條）。
- **活體驗收沒清乾淨**：`VERIFY_C7_ARMOR`（`2d335019…`）還留在 DB 裡，回來手動刪。

### 回來要跑的

```bash
cd ops/compose && docker compose up -d --wait
uv run python ops/tools/live_system_check.py --starve-days 3.0
```

⚠ `--starve-days 3.0` 走完整階梯（×0.9→×0.75→×0.5）要 **~37 分鐘牆鐘**（斷補要累積真的模擬時間，
不是當掉）。收工前最後一輪只跑了 `1.0`（16/16 綠），**完整階梯尚未在修完 C16 後重跑過**。

## 中斷續作指引

- **下一步第一件事**：C7.2（再訂購水位與補給線、`SUPPLY_POINT`、打擊敵後勤）。
  `needs_resupply()` 已備好當觸發線。
- **未竟項**：
  1. **LOGISTICS capacity 的類別分艙未擴**——契約現有 `capacity: {AMMO/FUEL/WATER_FOOD/BATTERY}`，
     與本卡的 Class I/III/V/IX 命名不一致。要對齊得改契約（紅線 4，宜與 C7.2 一起）。
  2. **想定/ORBAT 不能宣告單位的 `supply` 初值**——目前只能由 API 或 MSEL 注入。
  3. 前端不顯示補給水位與斷補狀態。
  4. Class IX 的消費端在 C7.3（修復）。
