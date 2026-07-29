---
task: WP-C10.4       # SPEC_V2 §6 WP-C10（BDA 回報 + 散布係數掛觀測者）
status: IN_PROGRESS
started: 2026-07-31T06:20+08:00
updated: 2026-07-31T06:55+08:00
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
| C10.4b | `BDA_REPORT` 事件（帶迷霧誤差）+ feed 呈現 | 待 |

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

## 中斷續作指引

- **4a 已完成**。下一步：**C10.4b**——`BDA_REPORT` 事件（帶迷霧誤差）+ feed 呈現。
  4a 已經把 `observation` 判定與 `dispersion_mult` 落在 `ai_decision` 上，4b 直接取用。
- 4b 的設計要點（survey 已定）：新 ledger 事件而非 C2 信文（信文沒有系統寄件者，
  且 C2Panel 只在 mount 時抓一次、沒有 WS 訂閱）；`observer_faction` 標受眾；
  `damage_calc=None`（否則 `aar/stats.py` 會把估計值加進 `total_damage`）；
  `target_id=None`（否則真實單位身分會流進 AI briefing）；
  **新開 `"bda"` RNG stream**（在 `area_fire` stream 上抽會擾動後續落點，
  而且抽樣次數取決於前觀死沒死——狀態相依的抽樣次數正是 `rng.py` 警告的耦合）。
- **不要**在 4b 之前給 `AREA_FIRE_RESOLVED` 加漂亮的 feed 文案：目前前端只印生的型別字串，
  洩漏僅止於線上；加了文案就會把它變成螢幕上的完美 BDA。
