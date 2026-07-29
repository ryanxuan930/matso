---
task: WP-C10.4       # SPEC_V2 §6 WP-C10（BDA 回報 + 散布係數掛觀測者）
status: DONE
started: 2026-07-31T06:20+08:00
updated: 2026-07-31T07:35+08:00
agent: Opus 5
---

# WP-C10.4 BDA 回報（帶迷霧誤差）+ 散布係數掛觀測者

## 目標摘要

兩件事，共同的主題是**「有沒有人在看」要有後果**：

1. **BDA 回報**：落彈後由觀測單位回報戰果，且**帶誤差**——BDA 是情報不是 ground truth。
   沒有觀測就沒有 BDA。
2. **散布掛觀測者**：射擊陣營對落點沒有視線時，散布（CEP）加倍。
   驗收條件寫得很明確：「前觀死亡後 on-call 任務失去觀測修正（散布加倍）」。

## 這張卡要補的是 C10.2 自己記下的洞

`area-fire.md` 的「已知取捨」寫著：`AREA_FIRE_RESOLVED` 的 `damage_calc` 會被
`build_event_envelope` 帶進 WS 戰況 feed，**射方不需要任何觀測就立刻看到總傷亡**。
間瞄火力打的是看不見的地方——那正是這張卡存在的具體理由。

## 切卡：4a / 4b

survey 之後把這張拆成兩張。**4a 先出**，因為它有具名的驗收條件，而且是 4b 的前提——
BDA 若與一個精確的真實傷亡數字同時抵達，那個估計值就只是裝飾。

| 卡 | 範圍 | 狀態 |
|----|------|------|
| **C10.4a** | 觀測判定 + 散布掛觀測者 + 關閉 damage 洩漏 | ✅ 本次 |
| **C10.4b** | `BDA_REPORT` 事件（帶迷霧誤差）+ feed 呈現 | ✅ 本次 |

## 已完成（4a）

### 觀測判定放在裁決層，用已經蒐集好的目標清單

`AreaFireAdjudicator._gather_targets()` 本來就會從熱狀態撈出落點附近**每一個有座標的單位**
（含陣營與當前戰力）。觀測者候選就在裡面——不必再查一次 DB，而且**熱狀態的戰力才看得出誰還活著**
（DB 的座標欄位在單位死後照樣留著，且沒有存活旗標）。

**沒有重用 `has_observer_on`**（C10.1 的 API 路徑函式）：它把死掉的單位也算成觀測者、
位置讀的是活模擬從不寫的 DB 欄位、而且兩個失敗模式指向相反方向。改它會連帶改掉
call-for-fire 的行為——那是另一張卡（已記入 Backlog）。

### 三個狀態，不是 bool

`OBSERVED` / `UNOBSERVED` / `UNKNOWN`。**`UNKNOWN` 走 1.0（fail open）**：
地形服務掛掉不該讓全場砲兵默默變不準——把系統故障演成戰術事實是最難查的一種錯，
現象在精度、原因在基礎設施。而且 `STUB_GATEWAY`／開發環境本來就沒有真 gateway，
fail open 讓既有行為完全不動。

例外**絕不往上拋**：`kernel.run_tick` 與 `run_paced` 對裁決都沒有防護，
一個 `TerrainUnavailableError` 會讓 runner 崩潰、3 秒後被 SimManager 重建，
在服務中斷期間變成重啟迴圈。

### 探測有上限

tick 預算 200ms，每次 LOS 是一趟 gRPC（死線也是 200ms）。候選依距落點遠近排序後
**最多探 8 次**，並限制在 15 km 觀測距離內——只看 LOS 不看距離的話，40 km 外的單位
會被當成前觀（那個距離上視線「通」是幾何事實，但看不到彈著修正）。

### 射手不排除在觀測者之外

砲能直接看到落點就是直射，它當然看得見自己的彈著。真正的間瞄是打看不見的地方——
那種情況下砲離落點遠或被地形擋住，自然不會成為候選。**這是刻意的，有測試寫明理由**
（端到端測試把砲放在 18 km 外，超出觀測距離）。

### 關掉 damage 洩漏——**兩個邊界**

`feed_damage(event_type, damage_calc)` 一份規則，兩處呼叫：
`broadcaster.build_event_envelope`（WS 戰況 feed）與 `ai_loop.world_view._event_summary`
（AI briefing）。**只補其中一個的話，人看不到但 LLM 指揮官仍握有完美戰果評估**——
那種不對稱比全部洩漏更難察覺。

送 `None` 而不是 `0`：0 會被讀成「打了但沒傷到」，那是另一個假情報。
帳本上的 `damage_calc` 不動（AAR 要真的）。直射的 `ENGAGEMENT_RESOLVED` 不在迷霧名單裡
——打得到就看得到，那是刻意的差別。

### 決定性

`dispersion_mult=1.0` **位元不變**：`x * 1.0` 在 IEEE-754 恆等於 `x`，
且 `0.0 * k == 0.0` 所以 cep<=0 的早退路徑（不抽樣）也維持原樣。
抽樣次數不隨倍率變動 → `area_fire` stream 完全不動 → golden 不需重錄（實測 1471 全綠）。

## 執行紀錄

- `06:20` 開卡。SPEC_V2 §WP-C10 卡片表更新（C10.3 ✅、C10.4 ←）。
- `06:22` 起 survey workflow（觀測查詢成本／intel 資料模型／C2 REPORT 路徑／
  目前的洩漏點／決定性與 golden）。
- `06:45` 4a 實作完成。端到端測試第一次跑是紅的——砲兵放在 10 km 外，
  自己就成了觀測者。那不是 bug 是**模型正確、場景寫錯**：把砲移到 18 km 外
  （射程內、觀測距離外）才是間瞄的常態，並補一條測試把「射手可以是自己的觀測者」寫明。

## 測試證據

- `uv run pytest` → **1471 passed / 8 skipped**（+24：觀測 18、迷霧 6）
- `uv run mypy` 223 clean、`ruff`、schema-sync 綠
- **golden replay 未動**——這正是 `dispersion_mult=1.0` 位元不變的驗證

## 已完成（4b）

`core/app/adjudication/bda.py`——純函數，模組存在的理由只有一句話：
**觀測者回報的戰果不等於真實戰損**。回傳真值的 BDA 不是 BDA，只是把帳本換個名字再印一次。

### 事件形狀：每個留空的欄位都堵一個洞

| 欄位 | 值 | 不這樣寫會怎樣 |
|------|-----|----------------|
| `damage_calc` | `None` | `aar/stats.py` 對**每一種**事件都做 `total_damage += damage_calc`，估計值會被加在真值上，總戰損變成兩倍多一點的胡說 |
| `target_id` | `None` | 真實單位身分流進 AI briefing；而且 `event_audience` 會改按所涉單位推導受眾，覆蓋 `observer_faction` 的意圖 |
| `observer_faction` | 射方 | **少了它 `event_audience` 退回全域廣播**——挨打的一方會收到別人對自己的戰果評估 |
| 逐單位明細 | 不給 | 逐單位 BDA 等於把敵軍編成表交給射方（`SENSOR_CONTACT` 被排除在 AI briefing 外正是同一個理由）。真實的 BDA 本來也是「那一片大概掉了多少」 |

### 沒有觀測就沒有回報——**不是回報 0**

0 會被讀成「打了但沒傷到」，那是另一種假情報。什麼都不發，射方就只知道
「砲打出去了」——那正是他沒有前觀時實際擁有的資訊。`UNKNOWN` 同理：
不加倍散布（fail open），但也不憑空生一份評估。

### 誤差帶寫在事件裡

`error_band: 0.30` 跟著估計值一起下發，前端渲染成「約 −N（估計 ±30%）」。
**不用 `confidence: "MEDIUM"` 這種常數標籤**——今天沒有任何東西會讓它變動，
一個永遠相同的欄位只是雜訊，還會讓人以為背後有模型。
（觀測距離/光學/煙塵對誤差的影響屬後續保真卡。）

估計值四捨五入到**小數一位**，與帳本 `damage_calc` 的三位刻意不同：
一眼看得出這是估計，不是量出來的數。

### 獨立的 `"bda"` RNG stream

與落點共用一條的話，「這次有沒有前觀」會決定抽樣次數——於是**前觀死不死會改變後續
每一發砲彈的落點**。狀態相依的抽樣次數正是 `rng.py` 的 docstring 警告的耦合。
舊 checkpoint 沒有這個鍵，`restore_rng` 會略過（不需重錄，同 C10.2 的先例）。

`bda_rng=None` → 不發 BDA：既有呼叫端零行為變更。

### 前端 feed

`AREA_FIRE_RESOLVED` 現在有文案了，但**只說「彈落了」不說打死幾個**——
4a 的 `feed_damage` 已經讓後端根本不下發那個數字，前端也不該憑空生一個。
沒有觀測時額外標「（無觀測，散布加倍）」，讓操作員看得見自己為什麼打不準。

`BDA_REPORT` 永遠帶「約」與誤差帶。

## 測試證據（全卡）

- `uv run pytest` → **1486 passed / 8 skipped**（+39：觀測 21、迷霧 6、BDA 12）
- `uv run mypy` 224 clean、`ruff`、schema-sync 綠；前端 lint/typecheck 綠
- **golden replay 未動**（4a 的 `mult=1.0` 位元不變 + 4b 走獨立 stream）
- 容器 core+frontend 已重建

## 中斷續作指引

- **C10.4 兩張子卡皆已完成**。下一張：**C10.5**（陣地變換 `survivability_move`）。
- 本卡發現但未修（已入 PROGRESS Backlog）：`has_observer_on` 的三個缺陷、
  **AAR REST 可繞過整套火力迷霧**（`GET /aar/export` 回整包 `ai_decision`，
  局中就能拿到 `losses_by_unit` 與每一發落點）、面射擊戰損在 AAR 沒有歸屬。
  第二項特別要記——不記的話這張卡會宣稱「沒有觀測就沒有戰果」，而一個 REST 端點正在打臉。
- **盟軍觀測者未納入**：SPEC 寫「任一友軍」，關係矩陣也讓盟軍互相可見，
  但 C10.1 就已經只認自己陣營。兩處一起改才有意義，未在本卡動。
- `ENGAGEMENT_RESOLVED` 的 `target_health_after` 刻意保留：直射打得到就看得到，
  那不是漏掉的洩漏。
