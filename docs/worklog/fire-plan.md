---
task: WP-C10.3       # SPEC_V2 §6 WP-C10（FirePlan + at_tick/on_call 排程）
status: IN_PROGRESS
started: 2026-07-31T04:40+08:00
updated: 2026-07-31T04:40+08:00
agent: Opus 5
---

# WP-C10.3 火力計畫（FirePlan）+ at_tick / on_call 排程

## 目標摘要

把「預劃目標」變成實體：`FirePlan{targets:[{id, latlng, ammo_type, rounds, schedule}]}`。
`at_tick` 到時自動打；`on_call` 由 FSO 席位一鍵呼叫。打的方式就是 C10.2 已經做好的
`FIRE_MISSION` 令——**本卡不新增任何物理**，只補「什麼時候、由誰、對哪個預劃點」下那道令。

驗收（SPEC_V2 §WP-C10）：預劃 H-20 分的攻擊準備射擊自動執行。

## 開工前先確認的一件事

規格寫「`at_tick` 到時由 MSEL 執行器（WP-B2 複用）自動生成令」。**這句話目前不成立**：

- `MselEngine.check()` 只能回 `list[LedgerEvent]`（`core/app/scenario/triggers.py:123`）——
  它**產生不了令**，注入的事件只是帳本上的一筆紀錄。
- 而且活執行期根本沒接它：`sim_runtime.py` 傳的是 `NoOpTriggerChecker()`。

所以「複用 MSEL」要嘛是擴充 MselEngine 的能力、要嘛是走另一條路。這是本卡第一個要定的事。

## 執行紀錄

- `04:40` 開卡。修正 SPEC_V2 §WP-C10 的卡片編號表（C10.2 被面射擊佔用後其餘順延一號），
  以及 `call-for-fire.md` 內已過時的切卡表。
- `04:45` 起 ultracode survey workflow（六個平行讀者掃 MSEL/實體/下令/C2/砲兵指派/前端）。

## 中斷續作指引

- **下一步第一件事**：讀 survey workflow 的綜合結論，定下「排程器放哪」與「自動生成的令
  由誰當 issuer」兩個決定，再動工。
- **目前卡點**：無。
- **尚未驗證的假設**：待補。
