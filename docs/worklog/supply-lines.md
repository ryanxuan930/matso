---
task: V2.1 WP-C7.2
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C7.2 再訂購水位與補給線

## 目標摘要

規格的一句話定義了這張卡的價值：**「這讓『打擊敵後勤』成為可行戰法」**。
補給點是地圖上一個有庫存、有陣營、打得掉的東西；打掉它，下游單位的水位就不再回升。

## 四個決定形狀的裁決

**1. 補給點是 `MapFeature(kind="SUPPLY_POINT")`，理由與煙幕同一條。** 熱狀態是 unit 鍵值的，
補給點不是單位（不移動、不交戰、沒有戰力）。硬塞 pseudo-unit 會讓每個 `hot.get_all()` 消費端
都得學會忽略它。存 MapFeature 免費得到持久化、地圖圖層、**以及已經存在的敵我可見性語義**。

**2. 撥交是「拉」不是「推」。** `draw_from()` 由需要補給的一方呼叫（低於水位 → 找最近的
己方補給點 → 拉）。做成補給點主動推送的話，補給點就得知道全場有誰、誰缺什麼——那是全知，
而且**會讓「補給線被切斷」變得無法表達**（推送不需要路徑）。

**3. 「打掉補給點」用既有的摧毀語義，不是新機制。** WP-C10.2 的面射擊已經會處理落點半徑，
本卡只提供 `destroy_at()` 供火力裁決在命中時呼叫。不另造一套「攻擊建物」的裁決
——那會變成第二套傷害模型。

**4. 只拉自己陣營的補給點。** 盟軍補給點要不要共用是**後勤協定問題不是物理問題**；
預設不共用比較保守，要開放應該是想定的明確宣告而不是預設。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/engine/supply_points.py | 新增 | `SupplyPoint`、`nearest_usable`、`draw_from`（部分撥交）、`destroy_at`、`topped_up` |
| core/app/engine/supply_wiring.py | 修改 | `auto_resupply()`：拉式補給 + `RESUPPLIED` 事件 |
| core/app/engine/fire_wiring.py | 修改 | 面射擊命中 → `destroy_at` + `SUPPLY_POINT_DESTROYED` 事件 |
| core/app/sim_runtime.py | 修改 | `_resupply_tick` 掛 pre_tick |
| core/tests/unit/test_supply_points.py | 新增 | 12 條（含驗收條文） |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1878 passed, 8 skipped, 4 deselected**
- `core/tests/replay` → **8 passed（golden 未重錄）**
- ruff / mypy(262) → clean（**無 DB migration、無契約變更**）
- **驗收條文達成**：`test_destroying_a_point_cuts_the_supply_line` ——
  補給正常 → 打掉補給點 → 同一個單位再也拉不到，水位停在原地
- 突變測試 6 個全數被抓（其中一個靠突變才發現測試不夠，見下）

## 決策與陷阱

**庫存不足時給一部分，不整批拒絕。** 那才是真實的補給點行為，而且「拉到一半」正是指揮官
需要看見的訊號（這個補給點快空了）。

**撥交夾在容量上限**——背包裝不下就是裝不下。

**`load_points` 依 id 排序**：撥交順序不可隨查詢順序漂，否則兩個單位搶同一批庫存的結果
會不確定。

**JSON 欄位整包換掉**才會被 SQLAlchemy 視為 dirty（同 WP-C2 的教訓）。

**⚠ 又一條測試被突變測試修正（本 session 第五次）。** 「沒有單位缺補就不查補給點」原本只
斷言「沒有事件」——但拿掉那個 early return 之後，`hungry` 仍是空的、迴圈本來就不會跑，
所以測試照樣綠。要真的驗到我在 docstring 宣稱的「零成本」，得讓 DB 一被碰就爆
（`_ExplodingDb`）。**斷言結果不等於斷言路徑。**

## 中斷續作指引

- **下一步第一件事**：C7.3（修復與人員補充；前線不整補）。
- **未竟項**：
  1. **補給車運輸未做**。規格寫「補給車自動往返（MISSION `MOVE_MARCH` 複用）」——
     本卡只做庫存與撥交（先把帳做對，再讓車跑起來）。目前單位必須自己走到補給點 3 km 內。
  2. **`RESUPPLY_VOUCHER` / 審批鏈未接**。規格說低於水位可接 WP-B5 審批或自動核准（想定開關）；
     現況一律自動撥交，沒有開關。
  3. **補給點不能從想定/UI 建立**——只能由 API 或 MSEL 注入 MapFeature。
  4. 前端不畫補給點，也不顯示庫存。
