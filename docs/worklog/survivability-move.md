---
task: WP-C10.5       # SPEC_V2 §6 WP-C10 最後一張（陣地變換 survivability_move）
status: DONE
started: 2026-07-31T07:45+08:00
updated: 2026-07-31T08:30+08:00
agent: Opus 5
---

# WP-C10.5 陣地變換（survivability_move）

## 目標摘要

規格（SPEC_V2 §WP-C10）：

> **陣地變換**：砲兵射擊 N 輪後 `survivability_move`（自動位移 1–2km，想定開關）
> ——反砲兵雷達（遠期）預留。

意思是：一門砲在同一個陣地上打久了會被反砲兵火力找到，所以打完幾次任務就要換位置。
這是 WP-C10 的最後一張卡。

## 開卡就注意到的一件事

`SEED_ARTILLERY` 裡的 `emplace_ticks`、`rounds_per_mission`、`mobility.can_self_move`
**全 repo 沒有任何地方讀**——只在 `seed_weapons.py` 定義過。

這跟 C10.2 動手前 `dispersion_cep_m` 的處境一模一樣：資料早就在 schema 與種子裡，
只是沒有消費者。本卡很可能就是它們的消費者，尤其 `can_self_move`——
**牽引砲與自走砲對「換陣地」這道命令的反應本來就不同**。

## 核心決定：位移是**下一道 MOVE 令**，不是改座標

「直接把熱狀態的 lat/lng 寫過去」看起來省事得多。survey 查出四個具體理由說明它不行：

1. **會被復原**：`seed_combat_state` 每次 runner 啟動都**無條件**以 DB 座標覆蓋熱狀態的
   lat/lng——那兩個欄位是整段裡唯一沒有「缺鍵才補」保護的。只寫熱狀態的位移，重啟就跳回去。
2. **會瞬移**：一個 STATE_DIFF 直接跳 1.5 km，沒有地形、沒有油耗、沒有行軍耗損。
3. **會撞車**：裁決在 tick 最前面、移動在後面。同 tick 內單位若還有 MOVE 令，
   移動子系統會從 DB 重讀座標把它走回去。
4. **會繞過唯一的可達性閘門**：`path_reachable` 只長在 `OrderService.submit` 那條路上。

下 MOVE 令四件事全部免費解決。代價是多一次 gateway 呼叫、令列上多一筆——
**後者其實是好處**：自動位移要看得見才追究得了。

## 其餘決定

### 計數放熱狀態，不放 DB

`missions_since_displacement` 在熱狀態上。checkpoint 會整包帶走、rollback 會整包還原。
放 `TacticalUnit` 欄位的話**回滾不會還原它**（`rollback` 還原熱狀態與 Order，不還原
unit 欄位）——計數會帶著「未來的次數」活下來，那比「崩潰時最多掉一個 checkpoint 間隔」更糟。

**計的是任務次數不是發數**：發數由下令者自填、`rounds_per_mission` 又沒有任何程式讀，
兩者都不是穩定的單位。設定鍵因此叫 `missions_before_move` 而不是 `rounds`。

被物理擋下的射擊（`_reject`）不計數——沒打出去就沒有暴露。

### 想定層開關：`WargameSession.survivabilityMove` JSON 欄

與 ROE／機動覆寫同一套：contract → loader → 開局快照一份 → runner 啟動讀一次。
**不用 SimParams**：那是整個部署共用的單一 `SystemConfiguration` 列，兩個並行的想定會互相影響，
與規格的「想定開關」矛盾。**不用 Boolean 欄**：裝不下 N。

順帶把 `survivability_move` 加進 `scenario_to_dict`。⚠ 那個函式是手寫白名單，
`indirect_fire_requires_approval` 與 `request_quotas` **至今都沒被寫出來**——
匯出再匯入會靜靜丟掉它們。既有缺陷，記進 Backlog，不在本卡順手修；
但新增的鍵不能再多一個受害者。

### 牽引砲不排程

只有 `TRACKED`/`WHEELED` 會被排。迫砲之類的會由機動解析 fallback 成 `FOOT`，
於是「走」1.5 km 要 18 個 tick 且不耗油——那不是模型化人力搬砲，那是 fallback 在替我們亂編。
repo 裡**沒有 TOWED_GUN 範本、也沒有任何程式讀 `logistics.transport.can_tow`**，
為牽引砲寫的分支會是沒有測試資料的虛構。

`is_fixed` 的單位同樣跳過：想定作者標了就是說這門砲不動，想定開關不該蓋過那個宣告
（而且送出去也一定被 `ORDER_UNIT_FIXED` 打回）。

### 下令者是 `system-{faction}`

不可登入帳號，沿用 AI 迴路的做法。**與火力計畫刻意不同**：那裡的當責者是寫計畫的人
（人的意圖），這裡沒有人的意圖，是想定開關造成的自動反應，掛在誰頭上都是假的。

### 獨立 RNG stream

`"survivability"`。方位抽樣的**次數會隨地形變動**（不可達就換一個方位重抽，最多 3 次），
放在共用 stream 上等於讓地形決定後續每一發砲彈的落點。

## 兩個接受下來的代價

1. **位移會流血**：走正規移動路徑 → 吃油、有行軍耗損（TRACKED 約 0.03/km，
   1.5 km ≈ 0.045 戰力）、會發 `MOVE_ATTRITION` 事件。這是刻意的——
   shoot-and-scoot 本來就該付出東西，不然它是免費的無敵技。
2. **位移不出去會留下 3 筆 REJECTED 令**：`OrderService.submit` 的既有語義是「不可行也要落庫」。
   令列上會多三筆，但那是**真實發生過的事**：這門砲試了三個方向都出不去。
   靜靜地探測反而會讓「砲被困住」沒有任何痕跡。失敗另發 `SURVIVABILITY_MOVE_BLOCKED`，
   而且**計數照樣歸零**——不歸零的話被困住的砲會每個 tick 重試、每次一趟 gRPC，
   永久負載且畫面上什麼都不會發生。

## 測試證據

- `uv run pytest` → **1502 passed / 8 skipped**（+16）
- `uv run mypy` 226 clean、`ruff`、schema-sync（20 tables / 196 欄）、scenario schema 綠
- **golden 未動**：三支 golden 都接 NoOp 裁決/移動，碰不到火力路徑；
  且設定缺席時排程器整個不動作

## 中斷續作指引

- **本卡完成。WP-C10（計畫火力與 call-for-fire 作業鏈）五張子卡全數結案。**
- 明確**未做**、留給後續卡：
  - **`emplace_ticks`**（進入陣地後的待命時間，打完就跑之後不能立刻再開火）——
    要新增 `WeaponProfile` 欄位 + 在 `fire_wiring` 加一條「就位了沒」的分支，是另一個機制。
  - `rounds_per_mission` 仍然沒有消費者（不是門檻、也對不上火力路徑計的東西）。
  - 反砲兵雷達——規格自己標了「遠期預留」。
  - 牽引砲 + 牽引車（需要先有 `TOWED_GUN` 種子與 `can_tow` 的讀取端）。
- 未修的既有缺陷（已入 PROGRESS Backlog）：`scenario_to_dict` 白名單漏掉兩個想定鍵、
  `armory.vue` 的 `formToBaseStats` 會把 `mobility` 整包換掉而丟失 `fuel_capacity`
  （本卡的資格判定正好讀那些欄位）、rollback 後 DB 座標與熱狀態不同步。
