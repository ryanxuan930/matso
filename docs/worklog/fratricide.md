---
task: V2.1 WP-C9
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C9 友軍誤傷語意

## 目標摘要

[JCATS-A p.5–6]：成熟系統「命令照輸入執行、後果照裁定」——錯誤的火力計畫打到自己的補給點，
照裁。加想定開關 `allow_fratricide`：開啟時對友軍的 ENGAGE 改為強警告＋照常裁決，
並記 `FRATRICIDE` 事件供 AAR 追究。

## 開工前先派 6 個平行 scout 掃現況——掃出來的東西改變了這張卡的形狀

規格把 C9 寫成「加一個開關把 precheck 打開」。實際上：

**1. 「關」的那一邊本來就是破的。**（→ 先切出 C9a 修掉，見 `9778772`）
`api/deps.py` 的 `get_order_service`——**人類指揮官下的每一道令**走的那一條——從來沒有傳過
`relations`。`run_precheck` 於是退回 `FactionRelations()`＝全 HOSTILE，而
`is_hostile("BLUE","GREEN")` 在那份預設矩陣裡是 **True**。所以「不可打盟軍」那條規則
只擋得住打**自己陣營**。AI 路徑（`orders_bridge.py` 有傳）反而擋得住——恰好倒過來。

fail-open 的典型形狀：預設值看起來保守（「全部當敵人」），但套進一條「非敵對就拒絕」的
規則裡，它是最寬鬆的那個值。

**2. 三條路徑本來就不對稱。**

| 路徑 | 誤傷現況 |
|------|----------|
| `ENGAGE` | 預檢 ROE 分支擋住非敵對目標 |
| `FIRE_MISSION` | **完全沒有陣營檢查**——打自己人今天就會產生真實傷亡 |
| `MISSION` | 只透過它派生的 ENGAGE 子令間接受擋 |

只把開關接進 ENGAGE 的預檢，等於做出「開了才會誤傷」的假象。

**3. 面射擊不受開關影響，那是刻意的。** 規格明寫「區域武器的濺射本就該傷及半徑內友軍」。
砲彈不挑陣營；把面射擊也關掉會讓「攻擊準備射擊落短」這種最經典的誤傷情境變成不可能發生。
**開關管的是「故意瞄準友軍」，不是「砲彈落在友軍身上」。**

**4. 開關若照規格直接套上去，會順便把「攻擊中立方」放行。** 原本那條是
`not is_hostile(...)`，一個分支同時涵蓋自己陣營、ALLIED 與 NEUTRAL。攻中立是另一件事
（戰略決定，不是訓練用的誤傷情境），一起放行是無聲的範圍擴張。

**5. 又一個真的 bug：`friendly_losses` 用字串比較。** `area_fire.py` 原本判
`by_id[uid].faction == shooter_faction`，於是**聯軍誤傷（BLUE 打到 GREEN 盟軍）不會被標成
友軍傷亡**——AAR 上看起來像正常戰果。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/adjudication/fratricide.py | 新增 | 純裁決：`is_friendly`（走關係矩陣）、`blocks_engagement`（三態：敵對放行／中立一律拒／友軍看開關）、`fratricide_victims` |
| core/app/orders/precheck.py | 修改 | ROE 分支改走 `blocks_engagement`；`_allow_fratricide()` **每次下令現讀**；放行時附 `fratricide_warning` |
| core/app/adjudication/area_fire.py | 修改 | `is_friendly_faction` 可注入（未注入→退回字串相等，既有呼叫端不變） |
| core/app/engine/fire_wiring.py | 修改 | 注入關係矩陣；`_fratricide_events()`：**每個受害者各一筆** |
| core/app/state/broadcaster.py | 修改 | WS allowlist 加 `cause`/`shooter_faction` |
| core/app/aar/replay.py | 修改 | `FRATRICIDE` 進 `BOOKMARK_TYPES` |
| contracts/scenario.schema.json | 修改 | `allow_fratricide`（root 是 `additionalProperties:false`，硬閘門） |
| core/app/scenario/{loader,dump}.py | 修改 | 欄位 + `_build` + `create_session_from_scenario` + **手寫匯出白名單** |
| core/app/models/tables.py、db/prisma | 修改 | `allowFratricide BOOLEAN NULL`（migration `20260730120000_c9_allow_fratricide`） |
| platform/app/composables/useCopFeed.ts | 修改 | COP 戰況列的誤傷文案 |
| core/tests/unit/test_fratricide.py | 新增 | 18 條：純語義、預檢接線、想定九層、事件受眾 |

## 測試證據

- `uv run pytest -q` → **1813 passed, 8 skipped**（golden **未重錄**——開關預設關）
- ruff / mypy(254) / schema-sync(23 tables / 224 columns) / buf / 前端 lint+typecheck → clean
- 活 DB 遷移：先 `mariadb-dump` 備份（65.7 MB）→ `npm run migrate:deploy`（**不是 migrate dev**）
- 突變測試 7 個**全數被抓**：

| # | 突變 | 結果 |
|---|------|------|
| M1 | 開關也放行中立 | 2 failed |
| M2 | 友軍改回字串比較 | 2 failed |
| M3 | 放行但不留警語 | 2 failed |
| M4 | 開關關掉也放行盟軍 | 2 failed |
| M5 | dump 白名單漏掉開關（BL-2 原案） | 1 failed |
| M6 | `FRATRICIDE` 不註冊 AAR 書籤 | 1 failed |
| M7 | WS allowlist 漏掉 `cause` | 1 failed |

## 決策與陷阱

**`FRATRICIDE` 一個受害者一筆，不是一筆總結。** `event_audience` 由 `initiator_id`/
`target_id` 的陣營推導受眾，**兩者都取不到陣營時回 `None` ＝全域廣播**。做成總結事件
（受害者塞進 `ai_decision`、`target_id` 留空）的話，敵軍會立刻知道對面在自相殘殺。
一個受害者一筆並填 `target_id`，受眾自然收斂成「射手陣營 + 受害者陣營」。有測試釘住。

**證據放 `ai_decision` 不放 `detail`。** 前者入雜湊鏈，後者刻意不入（可被竄改而不觸發
`verify_chain`）。誤傷是要追究責任的事。

**警語走 `PrecheckCheck(passed=True)`。** 不影響 feasible，但會出現在 `PrecheckResult.checks`
讓前端顯示。靜靜放行等於沒做這張卡——這條路徑存在的意義就是「你確定嗎」。
`_precheck_engage` 有五個 return 點，**每一個都要帶上警語**：漏一條就變成「有些情況會警告、
有些不會」。

**開關每次下令現讀，不快取。** 白軍可能局中改（同 WP-A3 禁射格集的紀律）。

**欄位 nullable 且無 default。** NULL＝未宣告＝維持既有語義。NOT NULL + default 會回頭改掉
每一個進行中的既有局。

**匯出白名單是手寫的**（`dump.py`）——BL-2 修的是「補上漏掉的三個鍵」，不是讓它變泛用。
新增想定層設定必須同時改那裡，否則匯出再匯入會靜靜拆掉設定。有測試釘住。

## 中斷續作指引

- **下一步第一件事**：C9 後端完成。前端 affordance 未做（見未竟項第 1 條）。
- **未竟項**（已記入 PROGRESS Backlog）：
  1. **前端仍是一道獨立的鎖**：`cop.vue` 把盟軍濾出 ENGAGE 下拉、也拒絕點擊友軍為目標。
     開關打開時後端放行，但**操作員根本點不到那個目標**。需要 affordance + 確認對話框。
  2. **AI 結構上仍不可能誤傷**：`worker.py` 在 LLM 看到敵情之前就用 `is_hostile` 濾過，
     分解器也只從那份清單挑目標。開關對 AI 完全無效。
  3. **直射濺射（同格/鄰格友軍按距離衰減承傷）未做**——那是**新能力**不是新係數：
     `Target`/`EnvSnapshot` 完全沒有 lat/lng（唯一的幾何是 `range_m`＝射手→目標距離，
     拿它當衰減依據是靜悄悄的錯）。而且 `lethal_radius_m` 只存在於武器契約的 artillery
     `$def`，KINETIC 範本一律解析成 0.0 → 直射濺射對既有範本完全沒有效果。
     要做得先改契約（紅線 4）。
  4. `useScenarioEditor.ts` 的 `exportScenario` 是**第二份手寫白名單**，且已經在漏
     `request_quotas`/`indirect_fire_requires_approval`/`survivability_move`——
     用前端編輯器存一次想定就會拆掉那三個設定。與本卡無關但同源，已記 Backlog。
