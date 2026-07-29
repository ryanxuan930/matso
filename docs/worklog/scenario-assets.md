---
task: "WP-B6 想定資產補齊"
status: DONE
started: 2026-07-29T18:30+08:00
updated: 2026-07-29T21:40+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-B6（V2.0 路線第三張）；SPEC_FULL §11（想定管理）、§10 G4（ROE）
---

# WP-B6 想定資產補齊

## 目標摘要
想定體系的欠帳收尾卡。SPEC_FULL §11.1 宣告了完整的 scenario package 格式，實際只兌現一半：
`roe.yaml` 沒有 schema、`overrides/` 沒有載入端、官方想定只有 1 個（規格說 3 個）、
外加一個 roundtrip bug（`scenario_to_dict` 掉 `fixed` 旗標）。

## 開工掃描（5 條平行讀者）的關鍵發現

### 「掉 `fixed`」只是冰山一角——匯出路徑是**全面失真**的
| 欄位 | schema 有 | loader 讀 | dump 寫 | 前端編輯器 |
|---|---|---|---|---|
| `fixed`（單位） | ✅ | ✅ | ❌ **規格點名的 bug** | ✅（前端反而是對的） |
| `description` | ✅ | ❌ 模型沒這欄 | ❌ | ❌ |
| `factions[].display_name` | ✅ | ❌ 只留 color | ❌ | ❌ |
| `no_strike_zones` | ✅ | ✅ | ✅（WP-A3 補的） | ❌ **禁射區會被靜默刪掉** |
| `hex_resolution` / `aggregate_adjudication_level` | ✅ | ✅ | ✅ | ❌ |

**最嚴重的不是 `fixed`，是前端編輯器**：用它開一個有禁射區的想定再存回去，保護區整段消失、
無任何錯誤訊息——WP-A3 自己標記為「最危險」的那類沉默失效，發生在另一條路徑上。

### 驗收條文「位元一致」單獨抓不到這些 bug
export→import→export 比的是**第二次與第三次**輸出。dump 掉 `fixed` → 兩次輸出都沒有 → 照樣綠。
故本卡釘**兩條**性質：**無損**（`load(pkg)` 與 `load(dump(load(pkg)))` 逐欄位相等，以
`dataclasses.fields` 列舉→新增欄位自動涵蓋）＋**冪等**（dump 兩次位元一致）。

### 另外抓到的資產缺陷（規格未列）
- **`tutorial-platoon` 的勝負條件寫著 `type: eliminate`——那不是 `triggers.py` 支援的 type**
  （支援的是 `faction_eliminated`）。想定照樣載入，直到執行期評估勝負才丟 `TriggerError`，
  等於**整局都不會判勝負**。同類風險也在 MSEL 的 trigger。→ 本卡把 condition DSL 的驗證
  提前到載入時（`validate_condition`），並修正官方想定與三處測試 fixture。
- orbat schema **沒有 equipment**，但 SPEC_FULL §11.1 的錯誤訊息範例明寫
  `units[3].equipment[0]: unknown template 'T-999'`。沒有它，新想定只能全員同一把步槍
  ——做不出「戰車連 vs 步兵連」。→ 本卡補上（規格四項之外的第五項，但沒有它第二項沒意義）。
- loader 的 orbat/msel 讀取與 `LoadedScenario` 建構**各抄兩份**（package / bundle 路徑），
  `fixed` 當年就是「兩份都加對、第三處漏掉」。→ 收斂成 `_build()` / `_msel_entries()`。

## 交付（分四段，每段綠燈點 commit）

### S1 匯出無損化
| 檔案 | 說明 |
|---|---|
| `core/app/scenario/loader.py` | `LoadedScenario` 補 `description` / `faction_display_names`；兩條入口共用 `_build()` |
| `core/app/scenario/dump.py` | 補輸出 `fixed`（只在 True 時，同前端慣例）/ `description` / `display_name` |
| `platform/app/composables/useScenarioEditor.ts` | 補 `noStrikeZones` / `description` / `hexResolution` / `aggregateAdjudicationLevel` / `displayName` |
| `core/tests/unit/test_scenario_roundtrip.py` | 無損 + 冪等兩條性質，對 `scenarios/examples/` **自動參數化** |

### S2 `roe.yaml`（契約 → 載入 → **兩個確實會擋的生效點**）
| 檔案 | 說明 |
|---|---|
| `contracts/roe.schema.json` | **新增**。`default_fire_policy` / `weapon_restrictions`；`reason` 必填 |
| `core/app/orders/roe.py` | **新增**。純解析 `parse_roe` + DB 載入 `load_session_roe`（同 no_strike.py 分工） |
| `db/prisma/migrations/20260729120000_wpb6_scenario_roe/` | `WargameSession.roe Json?`（NULL＝無限制） |
| `core/app/adjudication/{combined,adjudicator}.py` | **生效點 1（權威）**：逐武器篩，被禁者 HELD/reason=ROE，不發射/不耗彈/不抽樣 |
| `core/app/orders/precheck.py` | **生效點 2（早退＋留痕）**：明確指名被禁武器的令 → `ORDER_ROE_VIOLATION` |
| `core/app/{sim_runtime,engine/engage_wiring}.py` | runner 讀該局 ROE → 注入裁決層；武器帶 category/template_name 供比對 |

### S3 `overrides/`（想定機動覆寫）
| 檔案 | 說明 |
|---|---|
| `contracts/mobility_matrix.schema.json` | **新增**。出貨矩陣過去是 contracts/ 下唯一沒被驗的檔 |
| `core/app/movement/mobility_matrix.py` | `MobilityRules` 值物件 + `merged()` 深合併；模組級函數降為「代理出貨預設」的薄殼 |
| `core/app/movement/session_mobility.py` | **新增**。該局規則的載入層（純值層維持零 DB） |
| `db/prisma/migrations/20260729130000_wpb6_mobility_overrides/` | `WargameSession.mobilityOverrides Json?` |
| `core/app/engine/movement.py`、`core/app/api/movement.py` | 執行端與**預覽端**讀同一份 |

### S4 官方想定補到三個 + condition DSL 載入時驗
| 檔案 | 說明 |
|---|---|
| `contracts/orbat.schema.json` | 加 `equipment[]`（template/quantity/ammo） |
| `core/app/scenario/loader.py` | 開局時把宣告的編裝變成 `EquipmentInstance`；未知範本名報**精確路徑** |
| `core/app/scenario/triggers.py` | **新增 `validate_condition`**（載入時驗 type 與必填欄位，遞迴 all/any） |
| `scenarios/examples/battalion-defense/` | **新增**。27 單位 / 大漢溪—石門隘口；機步營守 vs 裝甲營攻 |
| `scenarios/examples/joint-defense/` | **新增**。29 單位 / 高雄西南沿海；BLUE+GREEN 盟軍 vs RED（三方 + 旅級） |
| `scenarios/examples/tutorial-platoon/` | 補 roe.yaml + overrides；修 `eliminate` → `faction_eliminated` |

## 設計決定
1. **No-Strike 住哪**：SPEC_FULL §11.1 寫 roe.yaml，WP-A3 已把它放進 scenario.yaml 且隨局
   持久化、白軍可於 COP 地圖增修。**兩個權威比放錯位置更糟**，故維持現狀並更新 SPEC_FULL
   那一行指向實際位置。roe.yaml 負責 §10 G4 的另一半（怎麼打的規則）。
2. **只宣告會生效的 ROE**：SPEC_FULL §10 G4 另提「不得越過某線」，需要邊界幾何 + MOVE 攔截，
   延後至 WP-C10/B5。**刻意不先放進 schema**——宣告了不執行的安全機制比缺功能危險（WP-A3 教訓）。
3. **ROE 的第三個生效點（護欄 G4）刻意不做**：AI 的 ENGAGE 令幾乎不帶 `weapon_id`，
   裁決層已完整覆蓋 AI 路徑；為此在零 DB 的護欄層注入要查 DB 的武器分類器換不到實際攔截。
4. **機動覆寫不得改變可通行性**：A* 跑在 terrain 容器、讀它自己那份出貨矩陣，
   `GetPathRequest` 只帶 `{from_h3, to_h3, mobility_profile}`，**看不到想定覆寫**。改可通行會
   讓規劃端與執行端分歧（規劃穿過不可通行 → 半路 MOVE_BLOCKED 停死；或判不可達 → 退回直線）。
   只改速度則路線仍可走。由 loader 強制。要真正支援得先讓 terrain 吃得到 per-session 矩陣（另一卡）。
5. **不用「清 lru_cache 再重灌」做覆寫**：同一 core 行程同時跑 N 局，全域可變狀態會跨局污染
   且取決於哪局先啟動（非決定性）。以值物件 + 建構參數注入，同 `sim_params` 的既有紀律。
6. **ROE 缺檔要炸，MSEL 缺檔可略過**：ROE 是合規機制，「以為有限制、其實沒讀到」沒有外顯症狀。

## 測試證據
- 新增 48 條（roundtrip 13 / ROE 21 / 機動覆寫 14）；**`uv run pytest` → 1232 passed / 8 skipped**。
- **golden 6 未破**（golden 的想定用 NoOp/玩具 movement，不碰 mobility_matrix / 裁決 ROE）。
- ruff / ruff format / mypy(210) / schema-sync(16 表 144 欄) / OpenAPI / JSON Schema metaschema
  / 前端 lint + vue-tsc 全綠。
- **實證新測試抓得到舊 bug**：把 `dump.py` 的修正 stash 掉 → 新測試 3 條紅（含無損那條）；還原後 9 條全綠。
- 前端以 `tsc` 轉譯後直接跑 `export(import(bundle))`，確認 scenario 段落逐欄位無損。
- 三個官方想定實測載入：
  ```
  tutorial-platoon   單位  5 陣營2 MSEL 2 禁射區2(5+31格) 編裝 0 ROE=True 覆寫=True
  battalion-defense  單位 27 陣營2 MSEL 5 禁射區3(14+7格) 編裝59 ROE=True 覆寫=True
  joint-defense      單位 29 陣營3 MSEL 8 禁射區4(11+21格) 編裝49 ROE=True 覆寫=True
  ```
  三者的無損與位元一致 roundtrip 皆綠（測試掃描 `scenarios/examples/`，新增想定自動納入）。

## 未做 / 已知限制
- **想定不能覆寫可通行性**（見設計決定 4）——要做得先改 terrain proto，另立一卡。
- `weaponeering` 覆寫（`overrides/` 也提到）未做：`EquipmentTemplate` 是**全域表**，
  per-session 覆寫會污染同時進行的其他局。SPEC_V2 §6 WP-B4（參數凍結簽證）已預見此問題，
  長期方向是快照/簽證而非 per-session 覆寫表。
- `weather_script.yaml` 仍未被 loader 讀取（`files.weather_script` 宣告了沒消費端）——屬 WP-C4。
- 前端想定編輯器只保證**不失真**，沒有為 description / display_name / 禁射區加編輯 UI。
- ROE 的「不得越過某線」延後（設計決定 2）。

## 中斷續作指引
- **本卡已全部完成並實測**。無未竟項。
- 後續相關：WP-C4 接 `weather_script.yaml`；WP-B4 處理 weaponeering 的凍結/簽證；
  terrain proto 支援 per-session 矩陣後可放寬機動覆寫的可通行限制。
