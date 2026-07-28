---
task: "#93 P1 推演參數可調（全域參數集中系統設定）"
status: DONE（P1；P2/P3 見下）
started: 2026-07-28T19:30+08:00
updated: 2026-07-28T20:40+08:00
agent: Opus 5
spec: docs/PARAMS.md（本卡第一步產出的清冊）
---

# #93 P1：把「兵推行為」參數變成可調

## 前置：先產清冊再動手
`docs/PARAMS.md` 把全域參數分四層（H 熱更新 / R 重啟該局 / C 冷啟動 / P 需改程式）。
**P 層才是兵推行為那群**，成本在讀取端改寫而非 UI——本卡就是把 P 層的核心子集改成可讀設定。

## 三條紀律（設計時就定死，並各有測試）
1. **預設值＝原本的模組常數**：未設定時 `SimParams()` 與硬編碼行為**位元相同**。
   `test_defaults_match_the_original_constants` 直接比對 `movement/params.py` 的常數——
   這條若鬆掉，等於偷偷改了所有既有局的物理。**故既有局、既有測試、golden replay 全不受影響**。
2. **預覽與執行讀同一份**：`movement/params.py` 的註解寫著「單一真相——預覽端與執行端共用」。
   若只讓執行端可調，預覽就會與實跑分歧——那正是 SPEC_MOVEMENT 要消滅的 bug。
   故 `api/movement`（預覽）與 `engine/movement`（執行）都改讀 `SimParams`。
3. **不做全域可變狀態**：以明確傳遞的 frozen dataclass 承載，不用 module-level 可變 dict；
   讀取點固定、可測試、不影響決定性。

## 生效時機（依使用者裁示）
- **執行端**：session runner 啟動時讀一次 → **進行中的推演局不受影響**
  （不會半場改變物理規則）；新局或該局重跑才套用。
- **預覽端**：每次請求讀取 → **立即反映**。這是刻意的：預覽本就是「如果現在下令會怎樣」。

## 交付
| 檔案 | 動作 | 說明 |
|---|---|---|
| `docs/PARAMS.md` | 新增 | 四層分類的參數清冊（#93 第一步） |
| `core/app/sim_params.py` | 新增 | `SimParams` + `parse_sim_params`（**逐欄寬容**）+ `load_sim_params` |
| `contracts/core_api.yaml` | 修改 | **`/system/config` 原本完全不在契約裡**（#54 留下的缺口，同 `/intel`）→ 補上 GET/PUT + `SimParamsView`/`SystemConfigEdit`/`SystemConfigView` |
| `core/app/api/system.py` | 修改 | view 帶 `sim`；PUT 接受 `sim`（驗證後回寫正規化結果） |
| `core/app/movement/mobility.py` | 修改 | `mobility_from_stats`/`resolve_*` 可帶徒步速度覆寫（未帶＝原行為） |
| `core/app/engine/movement.py`、`logistics.py` | 修改 | 建構時可注入 `SimParams` |
| `core/app/api/movement.py` | 修改 | 預覽改讀同一份參數 |
| `core/app/sim_runtime.py` | 修改 | runner 啟動時 `load_sim_params` → 注入移動/補給/偵測 |
| `platform/app/pages/system-settings.vue` | 修改 | 「推演參數」區塊（含生效時機說明） |

**P1 參數集**：徒步越野/道路速度、後備車輛速度、行軍耗損（per profile）、補給撥交距離、
內建目視距離、偵測掃描間隔。

## 測試證據
- 新增 14 條單元測試：預設等同原常數、未知 profile 退回、壞值**逐欄**退預設
  （一個打錯的數字不該讓整場推演跑不動）、掃描間隔下限 1、存讀往返一致。
- gates：`pytest` **1110 passed / 8 skipped**（**golden 6 未破**）、ruff/mypy(205)/schema-sync/前端全綠。
- **容器實測**：
  - 未設定 → GET 回全預設；
  - PUT `foot_xc_kmh=12, resupply=7, intrinsic=-999(壞值), FOOT耗損=0.11`
    → 壞值退 4000、未動的 TRACKED 保持 0.03、其餘生效；
  - **預覽實際反映**：foot_xc=12 → 10.9 km/h；改回 5 → 4.5 km/h（比值 2.42 ≈ 12/5，
    地形除數相同）→ 證明設定確實走到速度模型，不只是存進 DB。
  - 驗完**已把設定重設回全預設**，使用者環境未被改動。

## 未做（P2/P3，見 PARAMS.md 分期）
- **P2**：韌性/逾時（gRPC deadline、breaker）、串流（ring 容量）搬進設定頁。
- **P3**：C 層 env 一律唯讀顯示 + 標「需重啟 X 服務」——目前系統資訊區已有部分，
  尚未逐項標註「需重啟哪個服務」。
- **R 層**（tick 率/節奏/AI 心跳/runaway 上限）尚未納入 UI；機制與 P1 相同，加欄位即可。
- 交戰/天氣/通聯的常數（`_IDENTIFY_THRESHOLD`、天氣效應係數、`ONLINE_MARGIN_DB`…）仍寫死。
