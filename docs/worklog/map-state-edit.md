---
task: "#55 地圖狀態編輯"
status: DONE
started: 2026-07-24T05:30+08:00
updated: 2026-07-24T06:05+08:00
agent: Opus 4.8
---

# #55 地圖狀態編輯（COP 布局：拖放單位 + 繪障礙，完成再開始兵推）

## 目標摘要
在 COP 讓白軍/導演暫停推演、直接拖放編輯單位位置、用既有地圖編輯器繪障礙/建築，布局完成後按
「開始兵推」恢復。障礙繪製與地圖特徵拖曳早已存在（#12/#26/#11）；新做的是**單位直接拖放定位**與
**編輯模式（暫停→布局→開始）**。

## 設計決策
- **編輯模式＝暫停**：進入時 POST /control PAUSE，完成 POST /control RESUME（「完成後再開始兵推」）。
- **限白軍/導演**（is_omniscient）：編輯任一陣營位置是布局動作（god setup）。
- **single-writer 紅線**（同 #52）：sim 的 RedisHotState mirror 忽略外部寫 → 座標編輯走**命令通道**
  （`live_position`），sim 迴圈 pre_tick drain 套自己的 hot；暫停中編輯 → RESUME 後第一 tick 生效。
  座標同時寫 DB（權威，供顯示/reconnect/seed）。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/state/live_position.py | 新增 | pos_cmd 通道（push/drain/apply；後到覆寫、未 seed 略過） |
| core/app/api/units.py | 改 | POST /{sid}/units/{uid}/reposition（白軍 gate；寫 DB current_lat/lng + push_pos_cmd） |
| core/app/sim_runtime.py | 改 | _apply_live_edits 併入 drain/apply_pos_cmds |
| platform/.../MapCanvas.vue | 改 | editUnits prop + unitMove emit + 單位拖曳（重用 FEAT_DRAG 落點預覽） |
| platform/.../cop.vue | 改 | mapEditMode + enterMapEdit(PAUSE)/startWargame(RESUME)/onUnitMove(reposition→refetch)；header 入口 + 編輯工具列（開始兵推鈕） |
| core/tests/unit/test_live_position.py | 新增 | 4 tests（通道 roundtrip/apply/last-wins/skip-unseeded） |
| core/tests/unit/test_units_api.py | 改 | +2 tests（reposition 白軍 OK / commander 403） |

## 測試證據
- test_live_position（4）+ test_units_api reposition（2）+ live_ammo 迴歸 → 綠
- 前端 lint/typecheck → 綠

## 測試證據（補）
- mypy 全量 192 → Success；ruff 全量 → All checks passed
- reposition 路由於運行容器 OpenAPI 確認註冊；core 重建 healthy

## 完成 / 後續可強化
- **完成**：白軍於 COP 按「地圖狀態編輯」→ 暫停 → 拖單位定位 + 繪障礙 → 「開始兵推」恢復。
- **後續可強化**：拖曳時單位圖標即時跟隨（目前用落點預覽點，放開後 refetch 定位）；編輯模式內新增/刪除單位；ORBAT 面板數值化改座標。
