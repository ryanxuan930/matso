# MATSO — 系統擴充規格（SPEC_EXTEND）
# 單位內多武器・聯合兵種交戰（Combined-Arms Intra-Unit Fires）

> 本文件擴充 [SPEC_FULL.md](SPEC_FULL.md) §7.1（戰鬥裁決引擎），為「一個單位以其**武器組合**同時對目標發揮火力」的權威設計。
> 語言/關鍵字慣例同 SPEC_FULL：正體中文敘述、程式識別字/API 欄位一律英文；MUST/SHOULD/MAY 依 RFC 2119。
> 對應任務板：**#44–#48（P1–P5）**。工程規範見 [HOW_TO.md](HOW_TO.md)。

---

## 0. 背景與問題陳述

真實戰場上，班以上編制的單位同時持有**多種武器**：大部分士兵配步槍、班另有機槍/榴彈發射器/反裝甲武器。單位交戰時是把這些武器**一起**帶到目標上（聯合兵種），而非只用一種。

MATSO 現況（三條裁決路徑）**把整包武器降維成一件**：

- `WeaponResolver.weapon_for()`（[core/app/engine/engage_wiring.py](core/app/engine/engage_wiring.py)）：honor 玩家選定武器，否則回**主武器＝射程最遠者**。
- `resolve_engagement` 單發、`_resolve_volley`（#30 班齊射）、`_resolve_aggregate`（#33a 營級 Lanchester）——**三者皆只吃一個 `WeaponProfile`**。
- 熱狀態彈藥為**單一純量** `ammo`（僅主武器）。

精確界定缺口：#30 已模擬「**同型**武器全員射擊」（例：7 支步槍齊射），缺的是**武器種類的組合**（7 步槍 + 1 機槍 + 2 具反裝甲一起打）。本規格補上此「單位內聯合兵種火力疊加」。

**參考系統**：COMBATXXI / JTLS（聚合級 weapon-target 殺傷率加總）、JCATS/OneSAF（實體級湧現，MATSO 刻意不採）。MATSO 走**聚合級 weapon-target-kill-rate summation**。

---

## 1. 設計原則與紅線（沿用，不可違反）

1. **AI 永不裁決物理**：武器命中/毀傷/射程/可達仍是 `core/app/adjudication/` 純同步純函數。AI（LLM）**僅得決定火力政策（fire policy，戰術意圖）**，由確定性引擎執行。
2. **確定性可重播**：所有隨機性經注入的 `DeterministicRNG`（stream="adjudication"）。多武器加總 MUST 維持「相同 (輸入, rng 狀態) → 相同結果」。
3. **golden replay 不破**：新增路徑 MUST **條件化啟用**（見 §5），單一武器單位行為 MUST 與現況位元一致；必要時重錄 golden。
4. **契約先行**：改 `contracts/` → 驗證 → 再實作；DB 變更只走 `prisma migrate`。
5. **一次一張卡**：P1→P5 循序；範圍外問題進 PROGRESS.md Backlog。

---

## 2. 目標模型：武器組合加總（weapon-target kill-rate summation）

單位對目標 T 的單次交戰戰力損失為**逐武器貢獻之和**：

```
strength_loss(T) = Σ over 單位持有的每件武器 i ∈ 合格集 E:
      shots_i          （eff_shooters_i × rate_i，受各自 ammo_i 封頂）
    × p_hit_i(range)   （各武器自己的射程/命中曲線 base_ph）
    × pk_i(T.armor)    （WeaponProfile.expected_casualties(armor_class)）
    × dispersion_i     （每件武器一次 DeterministicRNG 抽樣，期望≈1）
    × cp_per_platform  （authorized / platform_count，全武器共用）
```

**武器–目標匹配自動化（關鍵）**：不需人工挑武器——

- **射程帶**：`weapon_i.in_envelope(range)` 為 false → 該武器貢獻 0（遠距只有長程武器打得到）。
- **裝甲類**：`expected_casualties(armor_class)` 已內建 weapon×armor 殺傷率（反裝甲打步兵≈0、步槍打主戰車≈0）。

**逐武器合法性篩選（procedure 改動核心）**：現況「任一不合法 → 拒絕整場交戰」改為「**逐武器篩選**」——某武器沒彈/超射程/該火模式無 LOS 或彈道被阻 → 該武器不入合格集 E（貢獻 0），**不再讓整個 ENGAGE 被 REJECTED**。唯有 E 為空（無任何武器可打）才回 REJECTED（reason 取最能說明的：全 OUT_OF_RANGE / NO_AMMO / NO_LOS）。

**火力政策（fire policy）**：保留玩家/AI 能動性的合法決策面（非物理事實）：
- `FREE`（自由開火，預設）：E 全部開火。
- `SMALL_ARMS_ONLY`：僅輕兵器（保持隱蔽/節約重火力）。
- `ANTI_ARMOR_HOLD`：反裝甲/飛彈類僅在目標為裝甲時才用（勿浪費在步兵）。
- `SINGLE:<weapon_id>`：相容既有「指定單一武器」行為（回退）。

政策 MUST 只做「篩選/加權」，不得改變任何 p_hit/pk/傷害的**物理數值**。

---

## 3. 攻擊進行程序（新）

| 步驟 | 內容 | 歸屬 |
|------|------|------|
| ① 指定目標 | 玩家/AI 下令「以單位 X 交戰 T」，可附 fire_policy（預設 FREE） | order 層 |
| ② 逐武器資格篩選 | 對 X 每件武器 i 判定 `ammo_i>0 ∧ in_envelope_i(range) ∧ 火模式可達 ∧ policy 允許` → 合格集 E | adjudication（純函數）|
| ③ 火力分配 | 預設 E 全部對 T 開火（P5 才做多目標分配） | adjudication |
| ④ 合併裁決 | `Σ_{i∈E} volley_i`（各自 dispersion 抽樣）→ 總戰力損失；各扣 ammo_i；產生**單一** `ENGAGEMENT_RESOLVED` 事件，`ai_decision.per_weapon[]` 帶逐武器明細供 AAR | adjudication |
| ⑤ 返火/壓制 | 同現況 | 既有 |

---

## 4. 分階段落地（P1–P5）

### P1（#44）資料層：武器清單 + per-weapon 彈藥

- **目標**：把「單位 → 單一主武器 + 單一彈藥純量」升級為「單位 → **有序武器清單**（各帶 profile / quantity / ammo）」，不改任何裁決數值。
- **產出**：
  - `WeaponResolver` 新增 `weapons_for(unit_id) -> list[WeaponEntry]`（WeaponEntry：`weapon_id, profile, quantity, ammo`），保留既有 `weapon_for/primary_ammo/quantity_for`（相容）。清單順序 MUST 穩定（依 template_id/inst.id 排序）以利決定性。
  - 熱狀態 per-weapon 彈藥：`seed_combat_state` 除既有純量 `ammo`（保留＝主武器，相容）外，新增 `ammo_by_weapon: {weapon_id: int}`；只在鍵不存在時 seed（不覆寫執行期已扣量）。
  - 純資料，不接進裁決（P2 才接）。
- **驗收**：
  - 單元測試：多武器單位 `weapons_for` 回全部武器（含 quantity/ammo）；單武器單位清單長度 1 且與 `weapon_for` 一致；`ammo_by_weapon` seed 正確且不覆寫既有量。
  - `uv run pytest core/tests/unit -q`、`uv run mypy`、`uv run ruff check .` 綠。**golden 不受影響**（未接裁決）。

### P2（#45）裁決層：`resolve_combined_engagement`（Σ volley）+ gating

- **目標**：新增純函數 `resolve_combined_engagement(weapons, shooter_ctx, target, env_for_weapon, rng, tick)`，對合格武器逐一算 volley 貢獻並加總；接進 `EngagementAdjudicator`。
- **產出**：
  - `core/app/adjudication/combined.py`（純同步純函數，frozen dataclass I/O）：逐武器 `_legality_reason` 篩選 → 各自 volley 期望毀傷 → Σ → 單一 `EngagementResult`（含 `per_weapon` 明細 + `ammo_spent_by_weapon`）。E 為空 → REJECTED。
  - env 需逐武器（`indirect_fire`、`trajectory_clear` 依武器飛行剖面不同）：接線層提供 `env_for(shooter, target, weapon)`。
  - `EngagementAdjudicator.resolve` gating：**單位武器系統數 ≥2 且非聚合** → 走 combined；否則走既有單發/齊射路徑（**golden/單武器不變**）。
  - RNG 紀律：每件武器**恰一次** dispersion 抽樣，順序＝武器清單穩定序 → 可重播。
- **驗收**：
  - 單元測試：混合武器打步兵（步槍/機槍貢獻大、反裝甲≈0）與打裝甲（反裝甲貢獻大、步槍≈0）；遠距只有長程武器貢獻；逐武器彈藥各自封頂與扣減；E 空 → REJECTED；同 seed 同結果（determinism）。
  - **golden replay 全綠不需重錄**（gating 保單武器路徑）；scripted_battle DoD 仍綠（若其單位單武器）。若任何 golden 變動 → 用 `rerecord` 工具重錄並在 worklog 記錄 diff 理由。
  - 全 gate 綠。

### P3（#46）令/ROE：火力政策（fire policy）

- **目標**：ENGAGE 令可帶 `fire_policy`；AI 可設意圖；預設 FREE 維持現況觀感。
- **產出**：
  - 契約先行：`contracts/` 的 Order/EngageCommand 加 `fire_policy` 欄（enum，選填，預設 FREE）；驗證後再實作。
  - `EngageCommand` + precheck + 接線傳遞 `fire_policy` 至 combined 裁決的武器篩選。
  - OPFOR/AI decider 得輸出 `fire_policy`（schema 加欄，選填）——僅意圖，物理仍由引擎裁。
- **驗收**：SMALL_ARMS_ONLY 不消耗反裝甲彈；ANTI_ARMOR_HOLD 對步兵不放飛彈、對裝甲才放；SINGLE:<id> 等價現況單武器；契約 lint + schemathesis 綠。

### P4（#47）前端：武器組合顯示 + AAR 逐武器

- **目標**：COP ENGAGE 由「單選武器」改為顯示**即將開火的武器組合**（逐件標示在不在射程/彈量/是否被政策排除）；火力政策選擇；AAR 顯示逐武器貢獻。
- **產出**：
  - `platform/app/pages/session/[id]/cop.vue`：ENGAGE 面板列出單位武器清單（可視化 in-range/ammo/被政策排除），fire_policy 下拉；保留「指定單一武器」＝ SINGLE 政策。
  - AAR：`ENGAGEMENT_RESOLVED.ai_decision.per_weapon[]` → 逐武器命中/毀傷明細呈現。
- **驗收**：Playwright：下多武器 ENGAGE → 面板顯示組合 + 政策切換 → 收到事件 → AAR 見逐武器明細。前端 `lint`/`typecheck` 綠。

### P5（#48，後續/較大）保真：目標編成組成 + 多目標火力分配

- **目標**（列為後續，不擋 P1–P4）：突破「目標為單一 armor_class 聚合」的天花板。
  - 目標編成組成（platform mix）：單位被攻擊時逐**平台型**消耗（反裝甲打掉車、輕兵器打掉人）。
  - 多目標火力分配：單位火力在多個目標間分配（weapon-target allocation matrix）。
- **產出/驗收**：另立子規格；需 model/contract 擴充 + 可能 golden 重錄。**本階段先只在 SPEC_EXTEND 記錄設計方向**。

---

## 5. 決定性與 golden replay 策略（P2 關鍵）

- **Gating 準則**：combined 路徑僅在「射手單位持有 ≥2 種可產生 WeaponProfile 的武器系統」且「非營級聚合」時啟用。單一武器單位、既有 golden（movement/RNG-walk，不跑武器混合交戰）與單武器 scripted_battle **行為位元不變**。
- **RNG 消耗**：combined 對每件合格武器抽**恰一次** dispersion（順序＝穩定武器序）。單武器情形退化為「一次抽樣」，與現況 `_resolve_volley` 一致。
- **重錄程序**：若 P2 後任何 golden `state_hash` 變動，MUST 以 `core/tests/replay` 的 rerecord 工具重錄，且在 worklog 明列「哪個 golden、hash 前後、變動來源」；不可無說明覆蓋。

---

## 6. 契約異動清單（contract-first）

| 階段 | 檔案 | 異動 | 相容性 |
|------|------|------|--------|
| P3 | `contracts/…` Order / EngageCommand | 加 `fire_policy` enum（選填，預設 FREE） | 向後相容（選填） |
| P3 | AI decider 輸出 schema | 加 `fire_policy`（選填） | 向後相容 |
| P4 | `core_api` 交戰事件視圖（若前端需 per_weapon） | `ai_decision.per_weapon[]` 已在事件 payload，非強制契約欄 | 無破壞 |
| P5 | 目標編成組成 model + schema | 另議 | 需評估 |

P1/P2 **不動契約**（純內部裁決/資料層）。

---

## 7. 驗收準則總表

- 每階段 gate：`uv run pytest`（touched 區）、`uv run mypy`、`uv run ruff check .`、`npx @bufbuild/buf lint`、`ops/tools/schema_sync_check.py`；前端階段加 `cd platform && npm run lint && npm run typecheck`。
- **跨階段不變式**：golden replay 全綠（或有據重錄）；單武器單位行為不變；AI 不觸碰物理數值。

---

## 8. 風險與掛帳

- **目標單一 armor_class 天花板**（P5 才解）：P1–P4 期間「一個排內同時有步兵與車」只能以單一裝甲類近似——這是**已知限制**，不視為 bug。
- **per-weapon 彈藥的執行期重啟**：熱狀態已扣量不可被 DB 初值覆寫（seed 僅在鍵不存在時寫），P1 MUST 遵守。
- **聚合路徑一致性**：#33a 營級 Lanchester 目前只用主武器 `expected_casualties`；理想上攻擊係數應改用武器組合加總。列為 P2 的**選配延伸**（可獨立於排/連 combined 之後再做），不擋主線。
