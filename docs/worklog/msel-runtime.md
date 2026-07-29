---
task: WP-B2          # SPEC_V2 §6 WP-B2（MSEL 排程執行引擎與白軍誘導迴圈）
status: IN_PROGRESS  # B2a/B2b 完成；B2c（白軍 UI）與 MSEL golden 未做
started: 2026-07-31T09:10+08:00
updated: 2026-07-31T10:40+08:00
agent: Opus 5
---

# WP-B2 MSEL 執行引擎（B2a：DSL + 執行器 + 接上 tick；B2b：SPAWN_UNITS）

## 動手前 MSEL 整個是死的

不是「功能不完整」，是**三層都斷開**：

1. `MselEngine.check()` 只回 `list[LedgerEvent]`——注入頂多在帳本上留一筆，改變不了世界。
2. 活執行期傳給 Kernel 的是 `NoOpTriggerChecker`——那個引擎**從來沒有被呼叫過**。
3. `create_session_from_scenario` **從不持久化 `loaded.msel`**——就算接上了，
   執行期也讀不到任何腳本事件。

三個斷點各自獨立，任何一個沒補都等於整條鏈不通。這正是 SPEC_V2 說的「演習系統的心臟缺位」。

## 已完成（B2a）

### 條件 DSL 擴充

新增 `unit_in_polygon`（bbox 表達不了「河岸以北」）、`contact_established`、`manual`、
`after_ticks_of`、`held_for`、`not`。

**`held_for` 與 `after_ticks_of` 需要跨 tick 的記憶**，而條件評估是純函數——
故記憶放在 `TriggerContext` 帶進去（`fired_at` / `held_since`），由引擎維護。
「成立→中斷→再成立」會**重新計時**：那才是「持續 N tick」，不是「累計成立過 N 次」。

**`manual` 永遠不自動成立**。時間到就自動發的話，那就不是「白軍動態取捨」了。

每個新型別都同時進 `_CONDITION_FIELDS`——想定資產的錯誤要在**載入時**報出精確路徑
（`x.all.of[1]`），不是跑到一半才靜默失效。`not`/`held_for` 的 `of` 是單一條件而非陣列，
型別搞混也在載入時說。

### 判斷與副作用分開

- `msel_runtime.evaluate_msel(...)`：**純函數**，吃條目+脈絡+記憶，吐「該執行哪些注入」。
- `msel_actions.make_applier(...)`：I/O 層，把注入套用到世界。

分開的理由是可測性，也是紅線 2 的形狀。

### 注入型別

- **`MODIFY_UNIT`**（白軍軟裁決的機器化出口）：**雙寫熱狀態與 DB**。
  只寫熱狀態的話 runner 一重啟就被 `seed_combat_state` 用 DB 舊值蓋回去——
  那是 BL-4 那個回滾 bug 的同一個坑，這裡不重踩。
- **`MESSAGE`**：把一則狀況送進某陣營/席位的信文匣（白軍誘導迴圈的「狀況發佈」）。
  受眾以 `observer_faction` 標，寄件者填 `msel:{entry_id}`——**放一個看得出來源的字串
  比放某個真實使用者誠實**：這是系統發的狀況，不是誰寫的信。
- **`PAUSE`**：與白軍控制台**共用同一個 Redis 旗標**。兩套暫停就是兩套會打架的狀態。
- **`WEATHER_OVERRIDE`**：落一筆 `MSEL_INJECT_UNSUPPORTED`。**明確說「還沒接」**——
  靜靜什麼都不做會讓想定作者以為天氣真的改了。
- 沒有 `action` ＝ 純事件注入，那是 MSEL 最原始的用法。

### 一則注入壞掉不得讓整局停擺

`kernel.run_tick` 對 trigger 槽**沒有任何防護**——一個例外會讓 runner 崩潰、
3 秒後被 SimManager 重建，在想定資料有問題時變成無限重啟迴圈。
故每則注入各自 try/except，失敗落 `MSEL_INJECT_FAILED`。

### 記憶撐得過重啟

`MselMemory` 可序列化，並且**已接進 checkpoint 信封**（`extras["msel"]`，
與 RNG 位置同一個地方），續跑時以 `restore_msel_memory` 灌回。

`MselEngine._fired` 是純記憶體的 `set`，每次 runner 重啟就把所有 `once` 條目重新武裝
——**D+2 的增援會在每次重啟時再來一次**。那是已知缺陷（PROGRESS / live-checkpoint），
這裡沒有重蹈，且有測試釘住（`test_msel_memory_survives_a_checkpoint`）。

### 脈絡從熱狀態組，不從 DB

活模擬只寫熱狀態；用 DB 組脈絡的話「紅軍推進到北岸」永遠不會成立，而且沒有任何徵兆。
同 BL-3 `has_observer_on` 踩過的坑。

## 已完成（B2b：SPAWN_UNITS 增援生成）

驗收條件的主角：「D+2 紅軍增援一個營於北岸生成」。

### 兩個前置各自是一個坑

**① 單位 id 必須決定性**。用 `uuid4()` 的話，重播會生出**不同的 id**——
於是重播裡「增援 3 號被擊毀」那筆事件指向一個不存在的單位，整段時間軸對不起來。
改為 `sha256("msel-spawn:{entry_id}:{index}")` 派生，順便讓生成**冪等**：
白軍重複扣板機、或重啟後記憶沒還原，都不會生出兩批部隊。

**② `WeaponResolver` 的快取是 runner 啟動當下的世界**（SPEC 明列的陷阱）。
沒有補查的話，MSEL 生出來的增援會**出現在地圖上、下得了令、卻一發都打不出去**，
而且沒有任何錯誤訊息。加了惰性補查：`weapons_for` 查不到就現查現快取，
查不到武器的單位也記著（`_known_empty`）免得每 tick 白查一次 DB。
順手把 `_build` 的迴圈主體抽成 `_load_unit` 供兩邊共用——兩份載入邏輯會漂移。

### 生成的單位要播進熱狀態

地圖、裁決、MSEL 脈絡讀的都是熱狀態。只寫 DB 的話那支部隊在模擬裡等於不存在。

## 未完成（本卡剩餘）

- [ ] **B2c 白軍 UI**：待命注入清單（`MselRuntime.pending()` 已備）+ 一鍵扣發 + skip/delay。
      後端的 `fire_manually`/`skip` 已實作，缺 REST 端點與面板。
- [ ] 含 MSEL 的 golden 案例（SPEC 要求）。

## 中斷續作指引

- **下一步第一件事**：B2c 白軍待命注入面板。後端已備
  （`MselRuntime.pending()` / `fire_manually` / `skip`），缺 REST 端點與 UI。
- 既有局零行為變更：無 MSEL 的 session，`MselRuntime.check()` 第一行就回 `[]`。
