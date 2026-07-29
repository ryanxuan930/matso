---
task: WP-C10.2       # SPEC_V2 §6 WP-C10（面目標射擊，FirePlan 的前置）
status: IN_PROGRESS
started: 2026-07-31T01:45+08:00
updated: 2026-07-31T02:10+08:00
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

## 未完成（本卡剩餘）

- [ ] 前端：COP 點地圖下火力任務

## 已知取捨（不在本卡修）

**面射擊的戰果會即時回饋給射方，不需要觀測。** `AREA_FIRE_RESOLVED` 的 `damage_calc`
會被 `build_event_envelope` 帶進 WS 戰況 feed，射方陣營立刻看得到總傷亡——
但間瞄火力打的是看不見的地方，沒有前觀就不該知道打中了什麼。
**這正是 C10.3（BDA 回報帶迷霧誤差）存在的理由**，留給該卡處理，不在這裡半修。
（受害方不會收到此事件：`event_audience` 只標 initiator 的陣營，對方是從自己的
STATE_DIFF 看到戰力下降——那是對的。）

## 中斷續作指引

- **後端已完整接線並綠燈**；剩下只有前端「COP 點地圖下火力任務」。
- 前端要做的最小版：選定砲兵單位 → 地圖點目標 → 帶 `{target_lat,target_lng,rounds}` 送
  `POST /sessions/{id}/orders`（order_type=FIRE_MISSION）。本局要求火協時還要能挑一張
  已核准的 FIRE_SUPPORT 申請單（目前沒有任何 UI 可以挑，見 PROGRESS Backlog）。
