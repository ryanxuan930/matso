---
task: WP-B4          # SPEC_V2 §6 WP-B4（參數治理：凍結、簽證、審計）
status: DONE
started: 2026-07-31T17:30+08:00
updated: 2026-07-31T18:20+08:00
agent: Opus 5
---

# WP-B4 參數簽證與凍結

## 目標摘要

[JCATS-A p.14,25–26]：動態參數想定編輯期可調，「參數確認後**簽證鎖定不得再改**」。
MATSO 的裝備庫與 SimParams 隨時可改——多人演習的公正性與重播性沒有制度保障。

## 三個與規格不同的裁決（都寫在程式的 docstring 裡）

### 1. 鎖住的是**全域**，不是「該演習關聯的 session」

規格寫「對該演習關聯 session MUST 拒絕」，但那兩樣東西**都是全域單例**：
`EquipmentTemplate` 是一張全域武器庫（無 sessionId），`SimParams` 存在
`SystemConfiguration.integrationConfig["sim"]`（DB 單一列）。
`POST /equipment-templates` 與 `PUT /system/config` **根本不帶 session_id**——沒有東西可以 scope。

實際規則只能是：**有任何一場演習簽證生效中，這些全域寫入一律拒絕**。
代價是同時進行的散局管理員也改不了武器庫。這個取捨是真的，不藏起來：
在一個 CPX 場地裡「演習進行中不准動參數」本來就該是全場的規矩。
（PROGRESS.md 早記過同源限制：「EquipmentTemplate 是全域表，per-session 覆寫會污染其他局」。）

驗收條文的「未掛演習的散局不受影響」講的是**開局不被拒**，不是寫入保持開放——
散局照跑，只是這段期間全域參數唯讀。**有測試逐條釘住兩者**。

### 2. 簽證是 REHEARSAL 期間的**明示動作**，不是進 EXECUTION 的副作用

規格說「進入 EXECUTION 時執行 freeze」。但 `params_sealed` 同時是離開 REHEARSAL 的
**必要勾稽項**（WP-B1 的 checklist）——照規格寫會**死鎖**：
要進 EXECUTION 得先勾，而勾是進去以後才發生的。

改成 `POST /exercises/{id}/seal`。這也更貼近條文本身：「參數**確認後**簽證鎖定」，
確認是人做的事。

### 3. `PUT /system/config` 是**選擇性**拒絕

那個端點同時寫 `ai.*`（H 層，規格明說不凍）與 `sim`（凍結對象）。
整條擋掉會讓白軍在演習中連 LLM 端點都換不了。
且**值沒變就放行**——前端每次存檔都送整包，原封不動的重送變成 403 會讓設定頁完全不能用。

## 雜湊的三個細節

1. **雜湊正規化後的 SimParams**（`to_config(parse_sim_params(raw))`）而非庫裡的原始 JSON。
   `parse_sim_params` 逐欄把壞值/缺值退成預設，外觀不同的 JSON 可能產生**完全相同的物理**
   ——雜湊原始 JSON 會製造假的「被篡改」。
2. **機動矩陣雜湊檔案內容，不用它的 `version` 欄**。那欄是手寫的、沒有東西會自動 bump，
   拿它當版本雜湊等於零篡改偵測。
3. **不重用 `compute_state_hash`**。它的契約釘在熱狀態 units 子樹上，而它的值正是
   golden replay 在斷言的東西；讓參數治理共用它，等於把兩件無關的事綁在一起。只重用 `canonical_json`。

## 拒起：沒有 start 端點可以掛守衛

`SimManager` 每 3 秒掃一次 DB，把每個非封存的 session 都跑起來——**建列即開跑**。
所以簽證比對是 `_ensure` 裡的一個早退，形狀與既有的 `session_concluded_key` 相同。
且因為掃描永遠重試，**拒起只在第一次記一行 log**（`_seal_refused` 集合）——
每輪印一行會淹掉所有其他訊息。參數改回來時把該 id 移出集合，下次違規會再記一次。

## 擋掉一個很難查的自傷：seed 覆寫已簽證的武器庫

`seed_equipment._upsert_templates` 跑在**每一次**由想定開局時，且它會**覆寫**既有範本的
`base_stats`。簽證期間若照常覆寫，在已簽證的演習裡新開一個預推局，就會靜靜改掉被鎖住的
那張表——然後**該演習的每一局都會因為雜湊不符而拒起**，而且沒有任何操作留下痕跡。

改成簽證期間**只擋覆寫、不擋新建**：新範本不改變任何既有值；而新建確實會讓雜湊變，
但那是「有人在演習中新增裝備」，本來就該被開局比對抓到——靜靜跳過新建反而會讓增援單位
拿不到武器（那是 WP-B2 已經踩過的坑）。

## 解鎖是狀態改變，不是繞過

`DELETE /exercises/{id}/seal`。沒有它，一場被忘記的演習會讓全域武器庫**永遠唯讀**
（`active_seal` 看的是 phase，而 phase 只能往前推、推不動就卡住）。
留一條有稽核痕跡的路，好過讓人去改 DB。**這不是護欄的 bypass**——
它改變的是「有沒有簽證」這個事實本身，而且會進稽核軌跡；
被禁止的是在寫入端點上加 `force=true` 那種東西（紅線 3 的精神）。

## 測試證據

`core/tests/unit/test_parameter_seal.py`（14）：雜湊穩定性與涵蓋範圍、快照往返、
403 凍結 + 指名演習、ai.* 不受影響、相同值重送放行、解鎖留痕、REVIEW 解鎖、
篡改拒起、簽證視圖提前示警、散局不受影響、**seed 不覆寫已簽證的表**。

全關卡：pytest 1650、mypy 244、ruff、OpenAPI、schema-sync 23 tables / 222 columns、
前端 lint/typecheck 綠。golden 未動（簽證完全不在 replay 路徑上——
`tests/replay/harness.py` 是純記憶體 Kernel，不碰 DB）。

## 未做並明記

- `docs/PARAMS.md` 的 P 層還有 25+ 個**硬編模組常數**沒有進 `SimParams`（移動耗損、
  偵測門檻、天氣效應係數…）。它們改不了、也就鎖不了。簽證只涵蓋**目前真的可調**的子集，
  在 `build_seal_payload` 的 docstring 明說這個界線——比宣稱「R+P 全鎖」誠實。
- 前端還沒有簽證 UI（屬 B1c 的 lobby 演習分頁）。
