---
task: "WP-B6 想定資產補齊"
status: IN_PROGRESS
started: 2026-07-29T18:30+08:00
updated: 2026-07-29T18:30+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-B6（V2.0 路線第三張）；SPEC_FULL §11（想定管理）
---

# WP-B6 想定資產補齊

## 目標摘要
想定體系的欠帳收尾卡。SPEC_FULL §11.1 宣告了完整的 scenario package 格式，但實際只兌現了一半：
`roe.yaml` 沒有 schema、`overrides/` 沒有載入端、官方想定只有 1 個（規格說 3 個）。
外加一個 roundtrip bug（`scenario_to_dict` 掉 `fixed` 旗標——匯出再匯入單位就會動）。

## 規格四項
1. `roe.yaml` 的 JSON schema（規格宣告了、schema 缺）
2. 官方想定補到三個（教學小型 / 營級防禦 / 聯合防衛大型；SPEC_V2 建議以 [JCATS-A] 的
   裝甲旅突穿想定結構建 `armor-breakthrough`）
3. `scenario_to_dict` 修復 `fixed` 旗標遺失（**先修**）
4. `overrides/`（mobility_matrix 想定覆寫）載入端實作

**驗收**：三個官方想定 export→import→export 位元一致；含 roe/msel/文書/no-strike 的完整示範。

## 開工掃描（5 條平行讀者）的關鍵發現

### 「掉 `fixed`」只是冰山一角——匯出路徑是**全面失真**的
| 欄位 | scenario.schema 有 | loader 讀 | dump 寫 | 前端編輯器 |
|---|---|---|---|---|
| `fixed`（單位） | ✅ | ✅ | ❌ **規格點名的 bug** | ✅（前端反而是對的） |
| `description` | ✅ | ❌ `LoadedScenario` 根本沒這欄 | ❌ | ❌ |
| `factions[].display_name` | ✅ | ❌ 只留 color | ❌ | ❌ |
| `no_strike_zones` | ✅ | ✅ | ✅（WP-A3 補的） | ❌ **禁射區會被靜默刪掉** |
| `hex_resolution` / `aggregate_adjudication_level` | ✅ | ✅ | ✅ | ❌ |

**最嚴重的一項不是 `fixed`，是前端編輯器**：用編輯器開一個有禁射區的官方想定再存回去，
`no_strike_zones` 會整段消失——保護區沒了、而且不會有任何錯誤訊息。這正是 WP-A3
自己標記為「安全機制的沉默失效最危險」的那一類，只是發生在另一條路徑上。

### 驗收條文「位元一致」抓不到這些 bug
export→import→export 比的是**第二次與第三次**輸出。dump 掉 `fixed` → 兩次輸出都沒有
`fixed` → 位元一致照樣綠燈。真正該釘的是**無損性**：`load(pkg)` 與 `load(dump(load(pkg)))`
逐欄位相等。本卡兩條都測。

### 其他事實
- loader **完全沒讀** `roe.yaml` / `weather_script.yaml` / `overrides/`（三者的 `files.*`
  在 schema 裡都有宣告——宣告了不讀，等於文件說謊）。
- `no_strike_zones` 現住在 `scenario.yaml` 根層（WP-A3），但 SPEC_FULL §11.1 寫它住 `roe.yaml`
  ——**兩個權威，必須先裁決**（見下）。
- orbat schema 只有 `designation/unit_level/lat/lng/parent/fixed`，**沒有 equipment**，
  但 SPEC_FULL §11.1 的錯誤訊息範例明寫 `units[3].equipment[0]: unknown template 'T-999'`。
  裝備目前只能靠 `seed_default_equipment=True` 全員配同一把步槍 → **現階段做不出
  「戰車連 vs 步兵連」這種有意義的想定**。
- loader 的 orbat/msel 讀取邏輯**各抄了兩份**（package 路徑 / bundle 路徑），`fixed` 當初
  兩份都加對了、只漏 dump。任何新欄位都要改四個點。
- mobility 覆寫的硬骨頭：A* 在 **terrain 容器**、用它自己那份 `contracts/mobility_matrix.json`，
  `GetPathRequest` 只有 `{from_h3, to_h3, mobility_profile}` → core 端覆寫到不了 A*。
- golden 不受影響（golden 的三個想定用 NoOp/玩具 movement，完全不碰 mobility_matrix）。

## 計畫（分四階段，每段綠燈點 commit）
- [x] 開工掃描
- [ ] **S1 匯出無損化（先修）**：`fixed` + `description` + `display_name`；roundtrip 測試由
      「部分欄位」升級為「全欄位無損 + dump 冪等位元一致」；**前端編輯器補齊失真欄位**
      （禁射區優先）
- [ ] **S2 `roe.yaml`**：契約先行 → loader 讀 → 落 session → **執行期真的生效**
      （不做「宣告了不執行」的安全機制）
- [ ] **S3 `overrides/`**：mobility 想定覆寫（core 端注入，不用全域 lru_cache）
- [ ] **S4 兩個官方想定** + 三個想定的無損/位元一致驗收

## 執行紀錄
- `18:30` 開卡。掃描確認 `scenarios/` 下只有 `tutorial-platoon`；`contracts/` 無 roe schema。
- `18:55` 5 條讀者回報完成，發現前端編輯器會靜默刪掉禁射區（比規格點名的 `fixed` bug 嚴重）。

## 中斷續作指引
- **下一步第一件事**：S1（匯出無損化）。
