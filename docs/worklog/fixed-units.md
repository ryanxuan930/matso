---
task: "#56 固定單位（指揮部）"
status: DONE
started: 2026-07-24T07:00+08:00
updated: 2026-07-24T07:40+08:00
agent: Opus 4.8
---

# #56 固定單位（劇本 ORBAT 可設指揮部等不移動單位，避免被派去交戰）

## 目標摘要
劇本編輯器設定 ORBAT 時，能標記某單位為**固定單位**（指揮部/後勤/陣地）：不接受 MOVE 令、
不會被 AI 或人類派去移動/機動交戰。原本 AI 會把指揮部也調去打，需要一個編成層旗標擋住。

## 設計決策
- **旗標＝編成約束，非物理裁決**：故權威閘門放在 **order validator**（`validate_order`），不是
  precheck（物理）也不是 adjudication（AI 不裁決物理，紅線）。`OrderService.submit` 是所有下令
  的唯一入口（AI 經 orders_bridge、人類經 API 都走它）→ 一處擋住覆蓋全部路徑。
- **只擋 MOVE**：固定＝不移動。ENGAGE（原地自衛）不受限——「固定」不等於「非戰鬥」。AI 端另以
  context 標【固定·勿調動】軟性引導 LLM 勿把指揮部投入攻勢。
- **與下令者無關**：白軍/導演也不能對固定單位下 MOVE（防誤把指揮部派出去）。
- **布局仍可擺放**：`reposition` 端點（地圖狀態編輯 #55 的 god setup）**不經** validate_order，
  故固定單位仍可於編輯模式拖放定位——旗標只約束推演中的戰術移動，不約束布局。
- **專用欄位**（非塞 attributes JSON）：`TacticalUnit.isFixed Boolean`，比照 faction/unitLevel
  的建模方式，可查詢、型別化（走 prisma migrate，紅線 4）。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| contracts/orbat.schema.json | 改 | unit 加 `fixed` boolean（預設 false） |
| contracts/core_api.yaml | 改 | 錯誤碼 +`ORDER_UNIT_FIXED`；UnitView +`is_fixed` |
| db/prisma/schema.prisma | 改 | TacticalUnit +`isFixed Boolean @default(false)` |
| db/prisma/migrations/20260724000000_o_fixed_units | 新增 | ALTER TABLE 加 isFixed |
| core/app/models/tables.py | 改 | is_fixed 欄（唯讀跟隨） |
| core/app/scenario/loader.py | 改 | ScenarioUnit.fixed；兩個 orbat reader 讀 `fixed`；create 寫 is_fixed |
| core/app/orders/validator.py | 改 | **權威閘門**：MOVE + is_fixed → OrderValidationError(ORDER_UNIT_FIXED) |
| core/app/ai_loop/context.py | 改 | UnitMeta.is_fixed；己方視圖 fixed=True；render 出【固定·勿調動】 |
| core/app/ai_loop/worker.py | 改 | load_unit_meta 讀 is_fixed |
| core/app/api/units.py | 改 | UnitView +is_fixed + _view 透出 |
| platform/app/composables/useScenarioEditor.ts | 改 | EditorUnit.fixed + export/import roundtrip |
| platform/app/pages/scenario-editor.vue | 改 | ORBAT TreeTable「固定」勾選欄（🔒 指揮部） |
| platform/app/pages/session/[id]/cop.vue | 改 | 單位清單 🔒 標記 + 下令面板固定提示 + submit MOVE 前端擋 + realAsOwn 帶 isFixed |
| platform/app/composables/useMilsymbol.ts | 改 | lockBadgeImage（canvas 生成鎖頭 ImageData，離線免 glyphs） |
| platform/app/composables/useUnits.ts | 改 | OwnUnit.isFixed + UnitFeature.fixed；own 特徵帶 fixed |
| platform/app/components/map/MapCanvas.vue | 改 | unit-fixed-lock 符號層（addImage 鎖頭 + filter own+fixed，右上角疊放） |
| platform/app/types/api.ts | 生成 | gen:api（UnitView.is_fixed） |
| core/tests/unit/test_order_validator.py | 改 | +3（MOVE 擋 / 白軍也擋 / ENGAGE 不擋） |
| core/tests/unit/test_scenario_loader.py | 改 | +1（fixed 載入 + 落地 is_fixed） |
| core/tests/unit/test_faction_context.py | 改 | +1（視圖 fixed + 渲染標記） |
| core/tests/unit/test_units_api.py | 改 | +1（is_fixed 透出 UnitView） |

## 測試證據
- 觸及區 unit：test_order_validator / test_scenario_loader / test_faction_context / test_order_service /
  test_units_api → 50 passed。
- 全量：core unit + ai **760 passed**；**golden replay 6 綠不受影響**（新欄預設 false、無想定使用）。
- mypy 192 Success；ruff All checks passed；schema-sync 16 tables / 140 columns 一致。
- 前端 lint + typecheck 綠；contracts buf/openapi/jsonschema 皆通過。
- prisma migrate deploy 已套用 3307；core 容器重建上線。

## 完成 / 後續可強化
- **完成**：劇本編輯器勾「固定」→ 該單位落地 isFixed → MOVE 令一律被擋（AI 落 rejected、人類收
  ORDER_UNIT_FIXED）→ AI context 標【固定·勿調動】不派其機動 → COP 清單顯示 🔒 且前端先擋 MOVE。
- **地圖符號鎖頭**（追加）：COP 地圖對固定單位（我方）於符號右上角疊放鎖頭徽章——canvas 生成
  ImageData 走 addImage（**離線免 glyphs**，air-gapped 仍可渲染），只 own+fixed（fog of war 不洩漏
  敵方編成）。**瀏覽器實測**（session e2e-orders，B1 設固定）：清單 B1 🔒、地圖 B1 符號右上角鎖頭
  正確顯示；驗畢還原 B1 isFixed=0。
- **後續可強化**：固定單位若被圍可考慮「撤收→轉為可動」的白軍指令；ORBAT 依 unit_type（HQ）自動
  預設 fixed；COP 地圖符號對固定單位加鎖形記號。
