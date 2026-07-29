---
task: WP-C1          # SPEC_V2 §6 WP-C1（壓制與姿態系統）
status: DONE
started: 2026-07-31T11:20+08:00
updated: 2026-07-31T15:40+08:00
agent: Opus 5
---

# WP-C1 壓制與姿態（Suppression & Posture）

## 掛點早就留好，系統一直缺席

`EnvSnapshot.shooter_suppression_modifier` 與 `target_posture_modifier`
**從交戰真實化時代就恆為 `1.0`**，而且 `resolve_engagement` 一直有在乘它們——
兩個欄位在裁決公式裡、在事件的 `ai_decision` 裡都看得到，只是永遠是 1。

後果不是「少一個功能」，是**兩件戰術事實表現不出來**：

- **砲兵的主要用途表現不出來**。真實的火力支援多半不是為了殲滅，是為了讓對方抬不起頭。
  沒有壓制，砲兵在模型裡就只剩下一個效率很差的殺傷工具。
- **防禦方的準備工作毫無意義**。掘壕、構工、選陣地——全部不影響任何數字。

## 已完成（後端）

### 純函數：`adjudication/suppression.py`

累積（依武器類別）、衰減、姿態狀態機與各自的修正係數。

**衰減率選 0.7 不是 0.85**：1 tick = 1 分鐘，0.85 要 **29 分鐘**才清得掉滿壓制，
那讓一次砲擊的效果長得像戰損。真實的壓制在火力一停就開始鬆動——抬頭、重新據槍是分鐘級的事。
0.7 ⇒ 半衰期約 2 分鐘、13 分鐘完全恢復。

（這個數字是被測試逼出來的：我原本寫 0.85 並在 docstring 聲稱「約 14 tick 衰到 0.1」，
測試斷言 14 tick 後歸零——一跑就紅。0.1 ≠ 0，**docstring 是對的、我的斷言是錯的**，
但那一紅讓我去算了真正的時間尺度，才發現 29 分鐘不合理。）

**低於 0.01 直接歸零**：留一個永遠除不盡的小數會讓熱狀態每 tick 都在變，
於是每 tick 對每個曾被壓制的單位推一次 STATE_DIFF。

### 姿態轉換要時間

HASTY 即時 / DEFENSE 30 分 / DUG_IN 4 小時，**期間仍算前一級**。
宣告掘壕的那一秒就享有掘壕防護，會讓「挖工事」變成一個免費按鈕。

兩個容易寫錯的地方各有一條測試：
- **重複下同一道令不重置計時**——否則反覆下令會讓工事永遠挖不完。
- **移動打回 MOVING**——挖到一半的洞帶不走。

### 中性預設 ⇒ 既有局位元不變

熱狀態的 `suppression`/`posture` 缺鍵時分別讀作 0 與 MOVING，修正剛好是 1.0。
既有局（那些鍵都不存在）與 golden 因此完全不動——**實測 golden 未重錄**。
這正是 SPEC_V2 §WP-C 說的「加保真」與「不破壞既有局」解耦。

### 接線：三個位置各做一件事

1. **累積**在裁決命中後（`EngagementAdjudicator._apply`）。**不是每 tick 掃描**——
   壓制的來源是具體的一次命中，掃描式的模型分不清「被打三次」與「被打一次但很久」。
2. **衰減 + 姿態收斂**在 `pre_tick`（與火力排程同位置）。只寫真的變了的單位。
3. **移動**在 `_advance_unit`：速度乘壓制修正、姿態打回 MOVING。

### POSTURE 令終於會做事

在此之前它是 NoOp——令收得下、狀態機也走得完，**就是沒有任何效果**。
現在有 `PosturePayload`（含 pattern 驗證）並在 `pre_tick` 執行。

姿態令**沒有裁決階段**（不產生戰損、不抽隨機、不需要物理判定），
所以走 pre_tick 而不是 Kernel 的裁決槽——塞進去只會讓那條路徑多一個與交戰無關的分支。

### 砲兵路徑原本整條漏掉（本次補上）

接線做完後跑驗收條文，第一次就紅——而且紅得很有價值：

1. **`AreaFireAdjudicator` 完全沒有累積壓制**。壓制只掛在 `EngagementAdjudicator._apply`
   （直射命中）。也就是說，「砲兵用來壓制」這句話的**砲兵那條路徑**根本沒接上。
2. **`resolve_area_fire` 完全不看姿態**。掘壕與露天的傷亡一模一樣（118.717 vs 118.717）。
   工事最該擋的就是砲擊，這等於把「為什麼要挖散兵坑」整個弄反。
3. **壓制範圍不等於殺傷範圍**。原本只有挨了戰損的單位會被壓制，但砲彈在你旁邊 100 m
   炸開沒傷到你，你照樣得趴下。現在 `SUPPRESSION_RADIUS_MULT = 3.0`，
   `AreaFireResult.suppressed` 逐單位帶「有幾發落進**它的**壓制半徑」——
   齊放外緣的單位不該與正中心同等壓制。

`suppressed` 刻意**不入帳本事件**：那是給接線層用的中間量，塞進 `ai_decision`
會改掉每一則 AREA_FIRE_RESOLVED 的雜湊。

### 驗收實測

20 發 155mm（5 輪 × 4 發齊放，輪距 2 分鐘）打一個滿編 120 的步兵連，
CEP 100 m、殺傷半徑 50 m：

| 姿態 | 傷亡 | 殘餘戰力 | 落彈當下的射擊效能修正 |
|---|---|---|---|
| DUG_IN | 0.64 | 119.36 / 120 | **0.40** |
| DEFENSE | 0.90 | 119.10 / 120 | 0.40 |
| HASTY | 1.09 | 118.91 / 120 | 0.40 |
| MOVING（露天） | 1.28 | 118.72 / 120 | 0.40 |

**驗收條文成立**：殲滅極慢（20 發打掉半個人），射擊效能卻只剩四成。
掘壕的傷亡剛好是露天的一半（DUG_IN 修正 0.5），構工終於有意義。

效能刻意量在**最後一輪落彈的當下**而不是其後的間隔之後——驗收問的是
「被砲擊的時候還打不打得動」。停火後的恢復是另一條測試（13 分鐘清乾淨）。

> ⚠ **絕對殺傷量偏低是 WP-C10.2 的 `_loss_for` 校準問題，不是本卡的**。
> 20 發 155mm 對露天步兵連只造成 1.28 戰力損失，直覺上太少；但那條公式自己就標了
> 「v0 佔位」。本卡只保證**相對關係**正確（掘壕≈露天的一半、壓制遠大於殺傷）。
> 已記入 PROGRESS Backlog。

### 其餘完成項

- **係數進 `SimParams`**：`suppression_decay` / `suppression_fire_penalty` /
  `suppression_move_penalty`（SPEC「每一項的係數 MUST 進 SimParams」）。
- **契約 + 前端**：`UnitView` 增 `suppression`/`posture`；COP 單位卡壓制條（橘紅斜紋，
  刻意與綠/黃/紅的效能條有別，避免被讀成戰損）與姿態徽章（MOVING 不顯示——
  每張卡都掛一個「行進」只是雜訊）；下令面板的 POSTURE 令 UI。
- **fog**：`/units` 的壓制度與姿態**只給友軍（己方＋盟軍）**，他方一律中性值。
  看得到敵軍被壓制多少等於一份免費的即時戰果評估，那正是 WP-C10.4 花整張卡在擋的。
  最要緊的一條是 **STUB_GATEWAY 的 e2e affordance**：它讓 faction 過濾整條 SQL where
  消失，是唯一一條「敵軍單位真的會出現在作戰方回應裡」的路徑——壓制度不跟著放行。
  （做過 mutation test：把 fog 拿掉，該條測試確實轉紅。）
- **AI context**：己方壓制/姿態進 prompt，且**講後果不只講數字**
  （「0.5」對 LLM 沒有意義，「射擊效能剩約 70%」才推得出「先撤出被壓制區」）。
  中性值時 prompt **位元不變**——`ReplayClient` 按 prompt 雜湊重播，
  prompt 一動所有已錄的 golden 自主場次全部作廢。
- **聚合裁決**：`AggregateForce` 增 `suppression`/`posture`（**逐方各自**，不是放進
  `AggregateEnv`——多方混戰裡每支部隊被壓制的程度都不一樣，攤成全場一個值就錯了）。
  係數**只在非中性時才進 `coefficients`**，否則會改掉既有局每一則
  AGGREGATE_ENGAGEMENT_RESOLVED 的序列化內容，連帶改掉 ledger 雜湊鏈。

### golden 案例 `suppression_defense_60`

SPEC_V2 明列本卡須有 golden。新增的想定把 C1 的四件事一次跑進 stateHash：
面射擊落點抽樣、姿態的轉換要時間、壓制在半徑內的累積、每 tick 的衰減。

姿態刻意用 **DEFENSE（30 tick）而不是 DUG_IN（240 tick）**：60 tick 的視窗內收斂得完，
於是「未就位仍算前一級」與「就位後才享有防護」兩種行為都進得了同一個 hash
（開火 tick 排在 5/10/15 與 35/40/45，剛好跨過就位點）。

**做過 mutation test**：把 `POSTURE_MODIFIER[DEFENSE]` 由 0.7 改成 0.65 → golden 立刻轉紅。
不是「有一個 hash 檔」就叫釘住了。

其餘既有 golden（empty_100 / rng_walk_100 / order_replay_60）**完全未重錄**——
中性預設守住了「加保真不破壞既有局」。

### 容器實測（非只有測試綠）

`docker compose up -d --build frontend` 後，往一個真 session 的熱狀態注入
`suppression=0.62 / posture=DUG_IN`，逐項確認：

- `GET /state`（COP 的單位來源，**不是** `/units`——`state.py` 複用 `list_units`，
  所以欄位一次到位）回 `"suppression":0.62,"posture":"DUG_IN"`。
- 單位卡：姿態徽章「掘壕固守」＋ tooltip「完整工事（×0.5，需 4 小時）」；
  壓制條寬度 62%、標籤「壓制 62%」、tooltip 換算成後果
  「射擊效能 −37%、速度 −31%（停火後每分鐘衰減）」。
- 下令面板：令型多出「姿態（掘壕/防禦）」，四個選項各自帶修正與耗時；
  實際送出 → `已下令（已驗證）`，DB 出現一筆 POSTURE VALIDATED。
  （該 session 的 runner 沒在跑——17 筆 ENGAGE、13 筆 MOVE 同樣卡在 VALIDATED——
  所以沒有觀察到執行；執行路徑由 `test_suppression_wiring` 覆蓋，
  `sim_runtime` 的 pre_tick 接線也確認在位。）

驗證用的注入與那筆測試令**已還原/取消**。

## 完成

本卡所有項目（含 SPEC 明列的 SimParams、契約/前端、AI context、聚合裁決、golden）皆已完成。

## 中斷續作指引

- 本卡已完成，無續作項。
- 全關卡狀態：pytest 1596 綠、mypy 234、ruff、schema sync 20/197、
  前端 lint/typecheck 綠、golden 4 案例（新增 1）。
- 唯一移交出去的東西：面射擊的**絕對**殺傷量偏低（WP-C10.2 `_loss_for` 的 v0 校準），
  已記入 PROGRESS Backlog。
