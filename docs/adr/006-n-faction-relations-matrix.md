# ADR 006 — N 方對抗與陣營關係矩陣

- 狀態：**Accepted**（2026-07-21）
- 決策者：使用者（需求）+ Opus 4.8（盤點與設計）
- 影響範圍：契約、DB schema、intel、聚合裁決、orders/precheck、guardrails、AI 迴路、前端、想定

## 背景

系統原設計隱含兩軍對抗：`Faction` 為封閉 enum（BLUE/RED/WHITE_CELL/ALLIED），「敵人＝非我方」。
使用者需求：**N 方對抗（如藍、紅、黃三軍）+ 可設定的陣營關係矩陣**（同盟/中立/敵對）。

盤點結論（2026-07-21 全 codebase 審視）：
- **已泛化（N 方友善）**：偵測 sweep（`!= 我方` 邏輯）、fog-of-war/intel store、faction-scoped
  API（units/orders/WS filter）、單位級交戰裁決（與陣營無關）、授權閘門、OPFOR 迴路（faction 參數化）。
- **寫死兩軍（blocker）**：
  1. `scenario.schema.json` victory_conditions `faction: enum[BLUE,RED]`；
  2. 聚合裁決 `resolve_aggregate_tick(blue, red)` + 事件欄 `blue_loss/red_loss`；
  3. `Faction` 封閉 enum（prisma/core/前端）且**契約漂移**（core_api：BLUE/RED/WHITE/GREEN ≠
     模型 BLUE/RED/WHITE_CELL/ALLIED）；`ALLIED` 為無邏輯死值；
  4. 無敵友關係模型（只能 FFA，不能分隊）；
  5. 前端 SIDC 二元敵我（own=F、contact 一律 H）。

## 決策

### D1 — Faction 由封閉 enum 改為「想定定義的字串 id」
- `faction` 在 DB（TacticalUnit/SessionParticipant/IntelContact）與契約中改為 **string**
  （pattern `^[A-Z][A-Z0-9_]{1,31}$`）。合法值集合由 **scenario 定義**（`factions:` 清單），
  載入時全量驗證；執行期對未知 faction 拒絕。
- **`WHITE_CELL` 為保留字**：非交戰方、statutory 全知（統裁），不得出現在 orbat/關係矩陣。
- 遷移：prisma `enum Faction` → `String` + migration；core `Faction(StrEnum)` 降為
  「保留字常數 + 驗證函數」；`ALLIED` 死值移除。契約漂移（WHITE/GREEN）一併修正。

### D2 — 關係矩陣（FactionRelation）
- 三值：`ALLIED`（同盟）/ `NEUTRAL`（中立）/ `HOSTILE`（敵對）。
- **對稱**（A→B == B→A；非對稱關係不支援，複雜度不值——需要時以 ROE 差異表達）。
- 想定資產：`scenario.yaml` `relations:` 上三角清單（`[A, B, ALLIED|NEUTRAL]`）；**未宣告的
  配對預設 `HOSTILE`**（兵推的常態是對抗——與現行「非我皆敵」語義一致，既有 BLUE/RED
  測試/想定零遷移；同盟與中立屬例外，須明示宣告）。
- **White Cell 可局中調整**（宣戰/停火）→ `FACTION_RELATION_CHANGED` Ledger 事件（證據性）；
  關係矩陣屬 session 熱狀態 + Ledger 可重播。
- 語義（單一權威 `core/app/factions/` 關係服務，所有子系統經它查詢）：

| 查詢 | ALLIED | NEUTRAL | HOSTILE |
|------|--------|---------|---------|
| 偵測（成為 contact） | 否（盟軍互見真實位置，經共享視圖） | **是** | 是 |
| ENGAGE 預檢 | 拒（friendly fire 僅 White Cell override） | **拒**（ROE 違規） | 允許 |
| 護欄 G4 | 攔截 | 攔截 | 通過 |
| 聚合裁決配對 | 不配對 | 不配對 | 配對 |
| 情報共享 | 可（等級可設，Phase 2 細化） | 否 | 否 |
| WS audience | 己方 + 盟軍可選 | 各自 | 各自 |

### D3 — 聚合裁決泛化
`resolve_aggregate_tick(blue, red)` → `(force_a, force_b)` 中性參數；多方混戰＝對每一
**HOSTILE 配對**逐一裁決（配對序確定性排序）。事件欄 `blue_loss/red_loss` →
`initiator_loss/target_loss`。**Ledger 事件內容變更 → golden replay 重錄**（rerecord_golden.py）。

### D4 — 前端 affiliation 由關係推導
SIDC：own=F、ALLIED=F（或 A）、NEUTRAL=N、HOSTILE=H；faction 顯示色由 scenario 定義
（`factions[].color`）。

## 後果

- **想定成為 faction 權威**——O7.1（scenario loader）依賴本設計先落地（TASKS O6.7）。
- 既有 BLUE/RED 測試/夾具照跑（BLUE/RED 變成合法字串實例 + 顯式 HOSTILE 關係）。
- golden replay 需重錄一次（D3 事件欄改名）。
- `SessionParticipant.faction` 等 DB 欄位遷移需 prisma migrate（ADR 004 流程）。
- 全系統「敵我」判斷收斂到單一關係服務——各子系統禁止自行 `!= faction` 判敵（紅線化）。

## 任務對應

TASKS **O6.7–O6.10**（插於 O6 之後、O7 之前；O7.1 依賴 O6.7）：
- O6.7 資料模型與契約遷移（string faction + 漂移修正 + migration + 保留字）
- O6.8 關係矩陣服務 + intel/orders/guardrails 整合
- O6.9 聚合裁決泛化 + golden 重錄
- O6.10 前端多陣營（SIDC/顏色/faction 選擇）+ 三方 E2E
