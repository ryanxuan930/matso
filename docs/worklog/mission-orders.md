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

## 中斷續作指引

- **下一步第一件事**：回到 V2.1 路線圖（C9 誤傷語意）。
- **未竟項**：SEIZE/DEFEND/SCREEN 三種任務型在活系統只做過 MOVE_MARCH 的端到端實測；
  任務被取消時未連帶取消已派生的子令（UI 文案已宣稱會，後端尚未做）。
