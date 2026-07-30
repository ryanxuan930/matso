---
task: V2.1 WP-A2（收尾修正）
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-A2 收尾：任務令在活執行期根本沒有被執行

使用者回報「任務的功能好像異常」。查證結果：**不是異常，是從來沒有接上過。**

## 兩個各自獨立的斷點

**斷點 1：`sim_runtime` 從來沒有把 planner 傳進 `Kernel`。**
`MissionPlanner` 在 repo 裡只有兩個引用——`subsystems.py` 的 Protocol 與 `NoOpMissionPlanner`。
grep 不到第三個。於是活執行期一直吃 NoOp：MISSION 令收得下、預檢會過、狀態變 VALIDATED、
指令列看得到——**然後什麼都不會發生**。沒有子令、沒有階段轉移、沒有錯誤訊息。

⚠ 與 WP-B2 記過的 MSEL 缺陷同一類：**槽留好了、實作寫好了、就是沒有人把它接上**。
下次再看到「Protocol + NoOp 各一個，grep 不到第三個引用」，那就是這個病。

**斷點 2：就算接上了，每一道 MOVE 子令都會被驗證層打回。**
`decomposer` 產的 MOVE payload 是 `{to_lat, to_lng, mobility_profile}`，
而 `MovePayload.to_h3` 是必填。**那不是分解器的疏漏**：它的 import 被白名單鎖在
`{__future__, typing, app.orders.mission}`（用來擋「分解器偷看地形/DB」），
它不能 import h3。latlng→hex 的正確位置是接線層。

補這一段之前，子令一律拋 `OrderValidationError("MOVE 載荷格式錯誤")`，
而第一版的 `_submit` 把它記成 INFO log 就吞了——**任務看起來在跑，實際上一步都不動**。

## 為什麼 golden 抓不到

`mission_seize_60` 在 `core/tests/replay/scenarios.py` 裡自帶一個 `_A2MissionPlanner`
（純記憶體版，直接把子令套進熱狀態）。它釘住的是**分解邏輯**，不是生產接線——
兩個斷點都在它照不到的地方。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/engine/mission_wiring.py | 新增 | `LiveMissionPlanner`：撈令 → 評估 → 送子令 → 記進度；`_hydrate` 補 `to_h3` |
| core/app/sim_runtime.py | 修改 | Kernel 傳入 `LiveMissionPlanner`（取代 NoOp） |
| core/tests/unit/test_mission_wiring.py | 新增 | 9 條，全部打在**接線層**（DB 的令進、DB 的子令出） |

## 設計裁決

**進度存在令上，不存在記憶體。** `MissionMemory` 只活在 planner 實例裡的話，
runner 一重啟（`SimManager` 每 3 秒掃描重建）任務就從 PLANNED 重跑一遍——SEIZE 會退回去
走第一個航路點。故寫回 `Order.payload._mission_state`：與 MOVE 的 `_leg`、
ENGINEER 的 `_work_until_tick` 同一套，**checkpoint 與重啟自動涵蓋**，不必另開熱狀態鍵。

**`world_view` 走 `build_faction_context()`**——與 LLM 指揮官看的是同一份投影。
自己組一份「反正分解器是確定性的」會直接違反紅線 3。

**子令被打回要留下痕跡。** 兩條都要在：帳本 `MISSION_SUBORDER_REJECTED`（供 AAR 追究）
＋ `OrderService.submit` 本來就會落的 REJECTED 列（供操作員當場看見）。
只記 INFO log 的版本正是這張卡要修的病。

**planner 的例外一律吞在自己這一層。** `run_tick` 對子系統沒有任何防護，
一個 raise 會讓 runner 崩潰後被 `SimManager` 每 3 秒重建成無限重啟迴圈。

## 測試證據

- `uv run pytest -q` → **1781 passed, 8 skipped**（+9；golden **未重錄**）
- ruff / mypy(253) / schema-sync / 前端 lint+typecheck → clean
- **活系統實測**（`e2e-orders`，重建 core 容器後）：使用者原本卡住的 2 道 MISSION 令
  由 VALIDATED 轉 EXECUTING，並產出 **5 道帶 `parentOrderId` 的 MOVE 子令**
  （1 COMPLETED / 4 EXECUTING）——修正前該 session 一道子令都沒有。

## 追加修正（同日，由 scout 工作流的反駁agent 指出）

**任務終局時子令沒有被收掉——這是我在本卡漏的第三個斷點。**

`_cancel_children` 只掛在 `OrderService.cancel`（使用者按取消）那一條路上，而
`LiveMissionPlanner` 走到終局時是**直接把母令寫成 COMPLETED**。於是任務完成（或失敗）之後，
最後一道 MOVE 子令仍是 EXECUTING——**部隊照著一個已經結束的任務繼續走**。失敗的任務更糟：
照著失敗的計畫繼續執行。

修法：
1. 把「收未終結子令」抽成 `orders/service.py::cancel_child_orders()`，兩條路徑共用同一份
   「什麼算未終結」的定義。
2. `_terminate()`：走 `next_status`（不再直接賦值）+ 收子令 + 發 `MISSION_ENDED` 事件。
   **每道令各自 try**——`next_status` 對非法轉移會拋，例外若往上冒會被 `plan()` 的外層
   try 吞掉並回 `[]`，於是**本 tick 每一道任務都停止規劃**。
3. **終局前 `populate_existing=True` 重讀**。真正的陷阱不是 `next_status` 不夠——是
   `expire_on_commit=False` + runner 整局共用一條 Session：`db.get` 直接命中 identity map
   回傳舊狀態，一句 SQL 都不發。於是被 API 行程取消掉的令在 planner 眼裡仍是 EXECUTING，
   `next_status` 順利通過 → **把 CANCELLED 靜靜覆寫成 COMPLETED**。

⚠ **這一段的測試被突變測試修正過兩次。** 第一版從外面連跑兩次 `plan()`，看起來對，
但把重讀和 `next_status` 都拿掉照樣全綠——因為第二次的 `_load` WHERE 就把 CANCELLED
濾掉了，終局那段根本不會執行。競態窗口在**一次 `plan()` 之內**，所以測試改成直接呼叫
`_terminate()`（白箱，但這個 guard 本身就是白箱問題）。三個突變現在都會紅。

## 中斷續作指引

- **下一步第一件事**：回到 V2.1 路線圖（C9 誤傷語意）。
- **未竟項**：SEIZE/DEFEND/SCREEN 三種任務型在活系統只做過 MOVE_MARCH 的端到端實測；
  任務被取消時未連帶取消已派生的子令（UI 文案已宣稱會，後端尚未做）。
