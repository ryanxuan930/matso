---
task: "WP-A3 修復 G4 no-strike 護欄（欄位匹配＋資料源）"
status: DONE
started: 2026-07-29T10:55+08:00
updated: 2026-07-29T13:10+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-A3；相關 SPEC_FULL §10（護欄）、§11（想定）、§13.2（COP 標註）
---

# WP-A3 修復 G4 no-strike 護欄

## 目標摘要
G4（禁射區）**從未攔過任何東西**。規格點出兩個斷點，掃描時發現**第三個**：

| # | 斷點 | 來源 |
|---|---|---|
| 1 | 欄位不匹配：G4 只讀令面 `target_h3`，AI 的 ENGAGE 令帶 `target_unit_id` → 永遠對不上 | 規格已知 |
| 2 | 無資料源：`no_strike_hexes` 恆為空（想定/白軍都沒有宣告處） | 規格已知 |
| 3 | **`GUARDRAIL_INTERVENTION` 從未落帳**：`intervention_events` 自 O6.2 就存在但**無任何 production 呼叫端** → AAR 的「護欄攔截 N 次」恆為 0、重播書籤標不出來 | **本卡掃描發現** |

## 交付
| 檔案 | 動作 | 說明 |
|------|------|------|
| `contracts/scenario.schema.json` | 修改 | 加 `no_strike_zones`（name/zone_class/geometry；polygon\|circle；GeoJSON [lng,lat]） |
| `contracts/core_api.yaml` | 修改 | `OrderRequest.acknowledge_restricted`；error code `ORDER_NO_STRIKE_ZONE` |
| `db/prisma/migrations/20260729000000_wpa3_no_strike_zones/` | **新增** | `WargameSession.noStrikeZones Json?`（NULL＝無禁射區，既有局零遷移） |
| `db/prisma/schema.prisma`、`core/app/models/tables.py` | 修改 | 雙邊同步（schema-sync 142 欄綠） |
| `core/app/orders/no_strike.py` | **新增** | 幾何→h3 格集（純函數 `zones_to_cells`）＋兩來源合流（想定宣告 + MapFeature 標註） |
| `core/app/guardrails/gateway.py` | 修改 | `TargetLocator` Protocol；G4 改判目標實際位置；NO_STRIKE 硬擋 / RESTRICTED_FIRE 只升級 |
| `core/app/ai_loop/orders_bridge.py` | 修改 | `UnitTargetLocator`（unit→熱狀態/DB 座標→h3） |
| `core/app/ai_loop/{opfor,worker,orchestrator}.py` | 修改 | 格集每週期自 DB 現讀；護欄攔截經 `event_sink` 落帳 |
| `core/app/orders/{precheck,service,schemas}.py`、`api/deps.py` | 修改 | 人類路徑：`no_strike` 檢查 + `acknowledge_restricted` override + override 留痕 |
| `core/app/state/ledger.py` | 修改 | `LedgerWriter` 型別放寬為 `Callable[[], Session]`（AI worker 走 db_factory） |
| `platform/app/composables/useMapFeatures.ts`、`pages/session/[id]/cop.vue` | 修改 | 白軍在地圖編輯器把面標記為禁射/限制射擊區 + 紅色說明列 |
| `core/app/scenario/{loader,dump}.py` | 修改 | 想定宣告載入 + 落地 session + roundtrip 輸出 |
| `scenarios/examples/tutorial-platoon/scenario.yaml` | 修改 | 兩個示範禁射區 |
| `core/tests/unit/test_no_strike_zones.py` | **新增** | 20 條 |

## 設計決定
1. **存宣告而非格集**：`noStrikeZones` 存 `{name, zone_class, geometry}`，格集於讀取時導出。
   理由：res-8 下一個中型區即數百格，存格集會讓欄位膨脹且與宣告重複；且白軍可局中增修。
   每週期重算（不快取）——快取會讓白軍的變更不生效，而 zone 數是個位數、成本遠低於同路徑的 terrain gRPC。
2. **guardrails 維持零 DB**：G4 需要查 DB 才能定位目標，故比照 G3 的 `OrderFeasibilityChecker`
   加一個注入式 `TargetLocator`。連 `ZoneClass` 都不 import（那個模組會讀 DB），改在 gateway 內
   自帶字面值，並用一條測試釘住兩處一致。
3. **NO_STRIKE vs RESTRICTED_FIRE 的處置差異**：`GuardrailOutcome.accepted` 原本 `= not g4.blocked`，
   無法表達「攔但不擋」。改為 G4 回**一組** findings，gateway 只讓帶 `zone_class=NO_STRIKE` 的
   blocked 影響 `accepted`，其餘僅觸發 `escalate_white_cell`。
4. **定位不到不擋**：locator 回 None 時放行——寧可漏擋也不要因為查不到就誤殺合法令，
   真正的把關在 submit 端 precheck（那裡有權威資料）。
5. **人類的 override 是新機制**：既有的 #28 強穿阻礙走的是「預覽端點警告 + 預設放行 + 執行期代價」，
   與禁射區要的「擋下 + 明確 override」語義相反，故不複用而新增 `acknowledge_restricted`。
   NO_STRIKE **不可** override（硬規則）；RESTRICTED_FIRE 的 override 寫
   `ORDER_RESTRICTED_FIRE_OVERRIDE` 至 Ledger 供 AAR 追究「誰明知而為」。
6. **MapFeature 來源不限 owner_faction**：禁射區是全局的人道/交戰規則，不是某軍私有標註——
   某軍圈了醫院，敵軍的 AI 也該受同一條約束。

## 測試證據
- 新增 17 條（幾何/座標順序/兩級別/資料源/G4 兩種目標表達/MOVE 不擋/定位失敗/人類三情境）。
- **`uv run pytest -m "not benchmark"` → 1136 passed / 8 skipped**；**golden 6 未破**。
- ruff / ruff format / mypy(207) / schema-sync(142 欄) / OpenAPI 驗證 / JSON Schema metaschema 全綠。
  （`redocly lint` 的 72 errors 為**改動前既有**、且非本專案 gate——CI 用 `openapi_spec_validator`。）
- 前端 `npm run lint` + `vue-tsc` 綠；`npm run gen:api` 由契約重生型別。

### 容器實測（唯讀探測使用者的局，不寫入）
```
既有局（無宣告）：no_strike=0 restricted=0 → 零行為變更 ✓
真實 RED 單位 R5 @ (24.2506,120.8453)；半徑 400m 圓 → 4 格；目標格分類 = NO_STRIKE
UnitTargetLocator 解析 target_unit_id → 884ba04ab3fffff  ← 改版前這一步不存在，G4 因此永遠對不上
[NO_STRIKE ] accepted=False escalate=True 剩餘令=0  攔截理由：打擊保護目標——硬阻擋，升 White Cell
[RESTRICTED] accepted=True  escalate=True 剩餘令=1  ← 保留但要白軍確認
全庫既有 GUARDRAIL_INTERVENTION：無（佐證斷點 3——改版前從未落過帳）
```

## 座標順序陷阱（寫錯不會報錯，只會靜默失效）
repo 幾何一律 GeoJSON `[lng, lat]`，`h3.LatLngPoly` 吃 `(lat, lng)`。寫反的話禁射區會跑到地球
另一端而「永遠攔不到」——與修好前的症狀一模一樣、極難察覺。轉換集中在 `_ring_to_cells` 一處，
並有一條測試斷言「經緯對調後的位置不在格集內」。

另補頂點格：`polygon_to_cells` 只收「格心落在多邊形內」者，小於一格的區域會一格都框不進 →
小型禁射區形同虛設。

## 想定 loader（收尾補完）
- `LoadedScenario.no_strike_zones` + 兩處建構點（package / bundle）+ `create_session_from_scenario`
  落地（樣板＝`factionRelations` 的寫入路徑）。
- **`_validate_no_strike` 會拒絕「算不出任何格」的宣告**：那種區在執行期完全攔不到東西，
  悄悄放行等於讓想定作者以為保護了醫院、實際上沒有——安全機制的**沉默失效**最危險。
  錯誤訊息直接點名最可能的成因（座標順序應為 [lng, lat]）。
- `scenario_to_dict` 一併輸出 → **匯出再匯入不會掉保護區**（`fixed` 旗標曾遺失的同類前例，有測試釘住）。
- 官方想定 `tutorial-platoon` 加了兩個示範區（衛生所 NO_STRIKE / 文化資產 RESTRICTED_FIRE），
  實測載入：5 格 + 31 格、分類正確、區外回 None。

## 未做 / 已知限制
- 前端只做「標記既有面為禁射區」，沒有做「畫一個禁射區」的專用工具（沿用既有繪面流程即可）。
- G4 目前只約束 `ENGAGE`；`MISSION` 令型尚不存在（WP-A2 做完要把它加進 `_STRIKE_ORDER_TYPES`）。

## 範圍外發現（記 PROGRESS backlog）
- `contracts/ai_output.schema.json` 的 `tactical_order` **未宣告 `target_lat`/`target_lng`**，
  但 decider 的 OUTPUT_INSTRUCTION 明確要 LLM 產出它們（靠 JSON Schema 預設允許額外屬性才沒爆）。
  屬契約漂移，應補宣告。

## 中斷續作指引
- **本卡已全部完成並實測**（含收尾的 loader/roundtrip/官方想定）。無未竟項。
- 後續相關：WP-A2 做完 MISSION 令型後，要把它加進 `gateway._STRIKE_ORDER_TYPES`。
