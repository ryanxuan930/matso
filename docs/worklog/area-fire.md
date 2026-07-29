---
task: WP-C10.2       # SPEC_V2 §6 WP-C10（面目標射擊，FirePlan 的前置）
status: DONE
started: 2026-07-31T01:45+08:00
updated: 2026-07-31T04:20+08:00
agent: Opus 5
---

# WP-C10.2 面目標射擊（打座標）

## 為什麼這張卡會冒出來

原本要做 C10.2「FirePlan + 排程」，動手前發現一個**規格沒點破的前提缺口**：

火力計畫的目標是**座標**（`targets: [{latlng, ...}]`），而「攻擊準備射擊 20 分鐘」
本來就是打預劃位置，不管當下有沒有人在那裡。但引擎的 `ENGAGE` **一律要 `target_unit_id`**
——表達不了「打這個點」。

所以 FirePlan 不是缺一層排程包裝，是缺一個能力。使用者裁示（2026-07-30）：**新增面目標射擊**。

## 已完成：裁決純函數

`core/app/adjudication/area_fire.py`——紅線 2 的純同步純函數，不碰 DB/熱狀態/牆鐘。

- **散布**：CEP（含 50% 落點的圓半徑）→ Rayleigh 抽樣落點。
  一律走 `DeterministicRNG`（紅線 1）：**同一顆種子必得同一個落點**，決定性重播才成立。
  `dispersion_cep_m` / `lethal_radius_m` **早就在 weaponeering schema 與種子資料裡**，
  只是過去沒有裁決路徑用到——這次把它們解析進 `WeaponProfile`。
- **齊射逐發抽落點**：`rounds=4` 是四個獨立落點累加，不是一發乘四。
  用一發乘 N 會讓散布消失，等於打得比實際準。
- **衰減**：中心滿額 → 殺傷半徑邊緣為 0 的線性遞減。不是真實爆震曲線，
  但**單調且可解釋**，比拍腦袋的階梯好；細緻化（彈種/掩蔽/俯角）留給保真卡。
- **不分敵我**：落彈半徑內的單位一律受影響，含友軍。這不是疏漏——
  火力協調之所以要有核准鏈與禁射區，正是因為砲彈不會挑人。有測試釘住
  （`test_friendly_units_are_also_hit`）。誤傷語意細緻化屬 WP-C9。

10 個測試。pytest 1400、mypy 218、ruff 綠。

## 已完成：令型與准入

- **`OrderType.FIRE_MISSION` + `FireMissionPayload`**（目標座標、發數、選用武器/核准單）。
- **席位**：`SEAT_ORDER_TYPES[FSO_FIRES]` 加上 FIRE_MISSION。
  B5.1 把它做成單一 registry 的用意在這裡兌現——新增令型只改一張表。
  （COMMANDER 是 `frozenset(OrderType)`，自動涵蓋。）
- **預檢**：單位須有曲射武器、目標須在射程內。
  **刻意不檢查 LOS**——間瞄火力打的就是看不見的地方，那正是它存在的理由。
- **火協 gate 也綁 FIRE_MISSION**：面射擊本身即曲射，一律要核准單。
  不套的話等於**用新令型繞過火協**——與 B5.3 那個「不指名武器就繞過」是同一類洞：
  新增一條路徑時忘了套既有的閘門。3 個測試釘住。

## 已完成：引擎接線

`core/app/engine/fire_wiring.py`（放 engine/ 不放 adjudication/——後者依紅線 2 必須是純函數，
既有的 `adjudication/adjudicator.py` 會讀 DB 是歷史遺留，不該再往上疊）。

- **`FireMissionOrderSource`**：撈 VALIDATED FIRE_MISSION → EXECUTING。通信閘門與 ENGAGE 同紀律
  （OFFLINE 收不到、DEGRADED 延遲）——**叫不到火力正是通信中斷最直接的後果**，不該例外。
- **`AreaFireAdjudicator`**：選武器 → 重查射程 → 扣彈 → 蒐集目標 → 裁決 → 落戰損。
- **Kernel 只吃一個 order_source / 一個 adjudicator**，故補了兩個組合器
  （`ChainedOrderSource` / `DispatchingAdjudicator`，`engine/subsystems.py`）。
  順序固定 + 各來源自身確定性 → 整串確定性；既有局沒有 FIRE_MISSION 令，golden replay 不受影響。
- **獨立 RNG stream `"area_fire"`**：散布抽樣次數隨發數變動，與交戰共用 stream 的話，
  打一次砲就會擾動所有交戰的隨機序列。舊 checkpoint 沒這個鍵，`restore_rng` 會略過（不需重錄）。

### 蒐集目標：不做半徑預篩

落點是 Rayleigh 抽樣，**尾巴無上界**——任何「殺傷半徑 + N 倍 sigma」的預篩都是猜的，
猜小了就是把遠處的傷亡悄悄吃掉，而且不會有任何徵兆。直接把全部有座標的單位交給純函數
依實際落點篩。真的成為熱點時該補空間索引，不是一個魔術半徑。

### 順手補到的三個洞

1. **核准單對 FIRE_MISSION 沒被兌現**。`orders/service.py` 那段只判 `isinstance(..., EngagePayload)`，
   而 `FireMissionPayload` 不是它的子類——**同一張核准單可以無限次掛在面射擊令上**。
   預檢擋得住「沒核准單」，擋不住「一張單用一百次」。已補，測試釘住。
2. **曲射武器類別有兩份**（precheck 一份、接線要再寫一份）。已抽成
   `adjudication/weapon.INDIRECT_CATEGORIES` 共用。判定一律看 `category` 而**不看
   `baseStats.indirect_fire`**：後者是使用者可在軍械庫自填的旗標，漏填時會變成
   「預檢說可以打、裁決卻找不到武器」——令通過卻毫無效果，且沒有任何錯誤訊息。
3. **逐發落點原本放在 `detail`**。那個欄位刻意不入 hash chain（可含牆鐘值），
   證據性欄位放那裡等於可以竄改而不觸發 verify。已移到 `ai_decision`。

另外把戰損**封頂在目標當前戰力**：齊射累加很容易超過殘存戰力，不封頂的話帳本上的
`damage_calc` 會比實際被扣掉的還多，AAR 的傷亡統計就是假的。

### 測試怎麼確認不是綠得虛假

`test_friendly_on_the_aim_point_takes_losses_too` 用**變異測試**驗過：把 `_gather_targets`
改成跳過同陣營單位後，該測試與 `friendly_losses` 那條確實會紅——而純函數那邊的
`test_friendly_units_are_also_hit` 照樣綠燈。這正是接線層需要自己一條測試的理由。

## 已完成：前端（COP 點地圖下火力任務）

`orderType` 加第三種 `FIRE_MISSION`：點「設定落點」→ 點地圖 → 送出。

- **落點標記與移動目的地刻意不共用**（MapCanvas 新增 `firePoint` prop + `fire-aim-*` 圖層）：
  黃色「走到這裡」與紅色「往這裡開砲」混成同一個記號，點錯的代價是打自己人。
- **誤傷警語常駐**，不是 hover 才出現的提示。有 e2e 釘住它一定看得到。
- **核准單下拉只列 APPROVED**：EXPENDED 一張只能兌現一次，列出用過的只會讓人選到
  必被預檢打回的那張。送出成功後重抓一次——核准單在令被收下時就扣掉了。
  （這同時補上了 B5.2 留下的「沒有 UI 可以挑核准單」缺口。）
- `ORDER_TYPE_LABELS` 補 `FIRE_MISSION: 火力任務`（原本指令列顯示的是生的 enum 值）。

### e2e 怎麼證明不是只有讀數對

`queryRenderedFeatures({layers:['fire-aim-ring']})`——只回**已繪製**的特徵。
把圖層名改成不存在的名字後該測試確實會紅，證明這條斷言不是空的。
（同 WP-D6.1 學到的：in-app 瀏覽器 harness 的 rAF 停擺，地圖驗證只能用真 Playwright。）

E2E 種子加一門砲兵 `ARTY`（155 榴）——沒有曲射武器的話只能驗到「被預檢擋下」，
證明不了整條路徑走得通。

## 已知取捨（不在本卡修）

**面射擊的戰果會即時回饋給射方，不需要觀測。** `AREA_FIRE_RESOLVED` 的 `damage_calc`
會被 `build_event_envelope` 帶進 WS 戰況 feed，射方陣營立刻看得到總傷亡——
但間瞄火力打的是看不見的地方，沒有前觀就不該知道打中了什麼。
**這正是 C10.4（BDA 回報帶迷霧誤差）存在的理由**，留給該卡處理，不在這裡半修。
（受害方不會收到此事件：`event_audience` 只標 initiator 的陣營，對方是從自己的
STATE_DIFF 看到戰力下降——那是對的。）

## 撞見的既有紅燈（**不是本卡造成的**）

`platform/e2e/orders.spec.ts` 原有 3 條測試在動手前就已經是紅的——動手前先 `git stash`
在 HEAD 上跑過一次確認（**注意 `reuseExistingServer` 會留著上一輪的 uvicorn 與它的
sqlite 檔控制代碼，不先殺掉 :8100/:3100 量到的是上一輪的世界**）：

| 測試 | 實際原因 |
|------|----------|
| `單位列表載入真單位` | 冷啟第一條測試：斷言時單位清單還沒回來（`Received: 0`），是測試自身的競態 |
| `下 MOVE 令全流程` | 斷言 `'MOVE'`，但指令列顯示的是中文 `移動`（型別標籤中文化後就沒跟上） |
| `下 ENGAGE 令` | B1 只有步槍（600m），R1 在 7km 外 → `ORDER_OUT_OF_RANGE`；種子的幾何與測試的「可行」期待矛盾 |

三條各自是獨立的小修（且第三條要先決定「該改種子距離還是改測試期待」，不是筆誤），
依紅線 5 記入 PROGRESS Backlog，不在本卡順手改。本卡新增的 2 條 e2e 綠。

## 中斷續作指引

- **本卡（C10.2）已完成**：裁決核心 / 令型准入 / 引擎接線 / 前端，四段各自綠燈。
- 下一張：**C10.3**（FirePlan + at_tick/on_call 排程）；BDA 為 C10.4
  （上面「已知取捨」那段就是 BDA 卡存在的具體理由）。編號重排見 SPEC_V2 §WP-C10 的表。
- 指令列目前顯示不出火力任務的落點座標——`OrderResponse` 只有 `target_unit_id`/`target_h3`，
  要顯示座標得先動契約。不影響操作（落點畫在地圖上），列入後續。
