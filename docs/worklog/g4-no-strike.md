---
task: "WP-A3 修復 G4 no-strike 護欄（欄位匹配＋資料源）"
status: IN_PROGRESS
started: 2026-07-29T10:55+08:00
updated: 2026-07-29T10:55+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-A3；相關 SPEC_FULL §10（護欄）、§11（想定）、§13.2（COP 標註）
---

# WP-A3 修復 G4 no-strike 護欄

## 目標摘要
護欄鏈 G1–G6 中的 **G4（禁射區）實質上從未攔過任何東西**——兩個獨立的斷點：
1. **欄位不匹配**：G4 只讀令面的 `target_h3`，但 AI 令帶的是 `target_lat/lng`（MOVE）或
   `target_unit_id`（ENGAGE），永遠對不上。
2. **無資料源**：`no_strike_hexes` 由 deps 傳入卻恆為空——想定沒有宣告禁射區的地方，白軍也沒有介面設。

本卡把兩端接起來：想定/白軍能宣告禁射區 → 存 session → G4 解析目標**實際位置**再判 →
NO_STRIKE 硬擋、RESTRICTED_FIRE 升級白軍確認；人類下令走 precheck 警告＋明確 override。

## 計畫
- [ ] 理解階段：workflow 4 reader（guardrails / scenario+db / map-features / 人類下令路徑）
- [ ] 契約先行：`scenario.schema.json` 加 `no_strike_zones`；DB 欄位 migration
- [ ] 幾何 → h3 格集（多邊形/圓形取樣）
- [ ] G4 判定改寫（解析目標位置；NO_STRIKE vs RESTRICTED_FIRE）
- [ ] 人類路徑：precheck 警告 + override（override 記 Ledger）
- [ ] 白軍 UI：地圖標註標記為禁射區
- [ ] 測試 + gates + 容器實跑

## 執行紀錄
- `10:55` 建卡。WP-A1 已完成並推上 main（`28b1e02`）。理解階段 workflow `wf_76f4ec88-d14` 啟動。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| （施工中） | | |

## 決策與陷阱
- 規格明示：**MOVE 令不擋**（開進禁射區不違規，打進去才是）。

## 中斷續作指引
- **下一步第一件事**：讀 workflow `wf_76f4ec88-d14` 的四份掃描結果。
- **golden**：標示不動（護欄在 AI 決策路徑，不進 Kernel tick）——待確認。
