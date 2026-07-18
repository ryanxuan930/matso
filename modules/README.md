# modules/ — 可熱插拔模組（SPEC_FULL §4–6, §17）

每個模組 = 獨立 process/container，實作 `contracts/plugin_base.proto` + 自己的領域 proto。

| 目錄 | 內容 | 里程碑 |
|------|------|--------|
| `_sdk/` | `MatsoPlugin` base class + 整合測試 harness（先於任何模組完成） | M2-5 前 |
| `terrain/` | DTED（`data/TW_ALL.tiff`，不入 git）、H3 hex grid、LOS、A* | M2 |
| `weather/` | CWA LIVE / SYNTHETIC / REPLAY 三模式 | M5 |
| `vision/` | 非 AI 確定性 CV 影像仲裁（規則式 OpenCV） | Phase 1.5 |

開發流程：proto 進 `contracts/` → buf lint → 實作 → `_sdk/harness` 整合測試 → compose 服務 → PluginRegistry seed（HOW_TO §4.3）。
Terrain 是 Core 硬依賴：DOWN → Session 強制 PAUSE。Weather 可降級（stale 模式）。
