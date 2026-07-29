---
task: "WP-C5 通聯後果閉環：位置凍結與敵情粗化"
status: IN_PROGRESS
started: 2026-07-29T23:55+08:00
updated: 2026-07-29T23:55+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-C5（V2.0 路線第六張）；SPEC_FULL §6.2「MUST enforce」；contracts/ws_protocol.md
---

# WP-C5 通聯後果閉環：位置凍結與敵情粗化

## 目標摘要
SPEC_FULL §6.2 的三種戰術後果只做了一種：`order_admissible`（OFFLINE 收不到新令、DEGRADED 延遲）
已於 #33b 接進 movement/adjudicator；`position_report_*`（斷聯單位在**己方** COP 位置凍結）與
`intel_granularity`（DEGRADED → 敵情粗化）**定義了但沒有任何消費者**。本卡把兩者接到投影層：
資料照常演進，只是「指揮所看不到」。

## 計畫
- [ ] 盤點：誰在寫/讀 comms 狀態，投影層在哪
- [ ] 契約先行：`UnitView.stale_since_tick`、`StateSnapshotView.comms_posture`、ws_protocol
- [ ] 位置回報：CommsSystem 依 `position_report_interval` 落 `report_lat/lng/tick`
- [ ] 投影：`/units`（含 `/state` 快照）、STATE_DIFF、AI context 三路共用同一純函數
- [ ] 敵情粗化：`IntelService` 依陣營 comms 姿態量化到 h3 res-6 + fidelity 上限 DETECTED
- [ ] 前端：stale 單位半透明 + 時間戳
- [ ] 測試 / 關卡 / 容器實測 / PROGRESS / SPEC_V2 勾選

## 執行紀錄
- `23:55` 開卡。盤點結果見下「開工掃描」。

## 開工掃描（規格四項之外發現的東西）

1. **STATE_DIFF 完全沒有陣營受眾**（最嚴重）。`build_state_diff_envelope` 產出的信封沒有
   `faction`/`factions` 標籤 → `stream/faction_filter.is_visible` 對所有人回 True →
   **每個連線的 client 都收到全部單位（含敵軍）的即時 lat/lng/health/fuel/ammo**。
   前端只是「沒把不認識的 unit 畫出來」而已（`cop.vue` 的單位清單來自 `GET /units`），
   開 devtools 就能看到敵軍完整動態。這是**紅線 3**（fog of war 過濾只能在後端）的違反。
   與本卡的關係：規格要求「STATE_DIFF 的**己方視角**投影中 OFFLINE 單位凍結」——
   一個廣播給所有人的信封**根本沒有「己方視角」可言**，不先做 per-faction 投影就做不了凍結。
2. **`TacticalUnit.comms_status` 播種後從未被寫過**。`CommsSystem` 只寫熱狀態的 `comms_state`，
   而 `GET /units` 回的是 DB 欄位 → 重新整理/重連後通聯狀態一律顯示播種值（ONLINE）。
   前端靠 STATE_DIFF 的 patch 蓋掉，所以只在「剛載入、還沒收到該單位下一次狀態轉移」時說謊。
3. `IntelContact` **沒有觀測者單位欄位**（只有 `faction`）。故敵情粗化只能做規格寫的
   「本陣營整體 comms 姿態」，做不到更真實的「該筆情報的回報者斷聯 → 該筆凍結」。記 backlog。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| （施工中） | | |

## 測試證據
（施工中）

## 決策與陷阱
（施工中）

## 中斷續作指引
- **下一步第一件事**：契約先行（core_api.yaml + ws_protocol.md），再動實作。
