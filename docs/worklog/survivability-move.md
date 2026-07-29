---
task: WP-C10.5       # SPEC_V2 §6 WP-C10 最後一張（陣地變換 survivability_move）
status: IN_PROGRESS
started: 2026-07-31T07:45+08:00
updated: 2026-07-31T07:45+08:00
agent: Opus 5
---

# WP-C10.5 陣地變換（survivability_move）

## 目標摘要

規格（SPEC_V2 §WP-C10）：

> **陣地變換**：砲兵射擊 N 輪後 `survivability_move`（自動位移 1–2km，想定開關）
> ——反砲兵雷達（遠期）預留。

意思是：一門砲在同一個陣地上打久了會被反砲兵火力找到，所以打完幾次任務就要換位置。
這是 WP-C10 的最後一張卡。

## 開卡就注意到的一件事

`SEED_ARTILLERY` 裡的 `emplace_ticks`、`rounds_per_mission`、`mobility.can_self_move`
**全 repo 沒有任何地方讀**——只在 `seed_weapons.py` 定義過。

這跟 C10.2 動手前 `dispersion_cep_m` 的處境一模一樣：資料早就在 schema 與種子裡，
只是沒有消費者。本卡很可能就是它們的消費者，尤其 `can_self_move`——
**牽引砲與自走砲對「換陣地」這道命令的反應本來就不同**。

## 執行紀錄

- `07:45` 開卡。起 survey workflow（既有的位置寫入路徑／想定層開關的先例／
  逐單位計數器要放哪／砲兵資料模型）。

## 中斷續作指引

- **下一步第一件事**：讀 survey 結論，定下「位移怎麼執行」（下 MOVE 令／走移動子系統／
  直接寫熱狀態）與「計數器放哪且能不能撐過重啟與回滾」兩個決定。
- **目前卡點**：無。
