---
task: WP-A2          # SPEC_V2 §6 WP-A2（任務級下令與準則分解器）
status: IN_PROGRESS
started: 2026-07-31T19:00+08:00
updated: 2026-07-31T19:00+08:00
agent: Opus 5
---

# WP-A2 任務級下令與準則分解器

## 目標摘要

[IST160 p.4–5] 的核心論證：成熟系統下的是**任務**（Attack(axis, objective, limit lines)），
由準則庫展開成路徑/梯隊/交戰/脫離；一人可指揮整旅。

MATSO 現在**人與 LLM 都在微操三種低階令**——LLM 每個心跳要重新推理「下一步走哪」，
呼叫頻率高、幻覺面積大。**把分解交給符號層正是 Neuro-Symbolic 的本義**
（SPEC_V2 §3 的第 3 條原則：任務級指揮的分解器是符號層，LLM 只選任務與參數）。

## 切卡（規格自己建議 4 張）

1. 契約 + `decomposer.py` 純函數 + 分解快照測試。
2. `mission_runtime.py` 接進 Kernel + 事件 + golden。
3. LLM 詞彙表 + G3 擴充 + 自主推演實測。
4. COP 下令 UI + AAR 任務時間軸。

## 執行紀錄

### 開工前的偵察推翻了規格的一個判斷

規格寫「**golden：重錄（新增令型）**」。動手前派 5 個平行 agent 掃過去，其中一個逐檔追下來
**推翻了它**：`core/tests/replay/` 的四個案例都是**手搭的純記憶體 Kernel**
（`scenarios.py` 自建 order source 與 adjudicator），**不碰 `OrderType`、不碰 DB、不走 `sim_runtime`**。
新增一個列舉成員、一個分解器、一個 mission 子系統，沒有任何一條路會改到那四個雜湊。

→ 改採 **WP-C1 用過的那一招：新增第五個 golden 案例**。SPEC_V2 §6 WP-A2 已同步更正。
**重錄會摧毀 golden 的唯一價值**——那四個雜湊有用，正是因為它們沒有被人為重設過。

另記一個看起來像 golden 壞掉、其實不是的情況：若 `mission_runtime` 以**必填** kwarg 進
`Kernel.__init__`（有 9 個建構點），四個案例會噴 `TypeError` 而不是雜湊不符。
那要補 NoOp 預設，**不是**去跑 `rerecord_golden.py`。

### 卡 1 完成（契約 + 分解器純函數 + 測試）

| 檔案 | 動作 | 說明 |
|---|---|---|
| `contracts/core_api.yaml` | 改 | `OrderType` 加 MISSION；`MissionType`/`MissionPhase`/`MissionPayload`；`OrderResponse` 加 `parent_order_id`/`mission_type`/`mission_phase` |
| `core/app/orders/mission.py` | 新增 | 載荷與階段（**純資料 + 純函數**）；逐任務型的 params typed model |
| `core/app/orders/decomposer.py` | 新增 | `step(mission, state, unit, world_view, *, tick) -> MissionStep`（**純同步純函數**）|
| `core/app/orders/validator.py` | 改 | `_PAYLOAD_MODELS[MISSION] = MissionPayload` |
| `core/app/orders/precheck.py` | 改 | `_precheck_mission` 目標可達性（**重用** `_precheck_move`）|

**測試**：`test_decomposer.py`（16）、`test_mission_payload.py`（14）。

#### 兩個 fail-open 的洞（scout 找到，本卡一起補）

1. **`run_precheck` 對未知 payload 型別掉進 `else: checks = []`，而 `all([]) is True`**——
   MISSION 令會**無條件通過預檢**。那不會報錯，只是靜靜放行，沒有任何測試會自然發現。
2. **`_PAYLOAD_MODELS` 沒登錄的令型會靜靜略過 payload 驗證**（`_parse_payload` 讓它以裸 dict
   通過；RECON/RESUPPLY 至今就是這樣）。壞掉的 params 會等到 Kernel tick 之中的分解時才炸，
   而 `run_tick` 對子系統的例外**沒有任何防護**——一個 raise 讓 runner 崩潰後被每 3 秒重建一次。

#### 迷霧陷阱做成靜態約束

SPEC_V2 對本卡點名的陷阱是「分解器讀的 world_view 必須走迷霧投影」。
`decomposer.py` 的 import 白名單由**測試**釘住（`test_decomposer_imports_nothing_that_could_see_ground_truth`）：
只准 `typing` 與自己的純資料模組，`app.models` / `app.state` / `sqlalchemy` 一律禁止。
讓「有沒有偷看」變成**讀簽名就能回答**的問題，比事後稽核可靠。

地形**不走 world_view**：地形是公開地理不是秘密，路徑規劃仍由 `PhysicsGateway` 在預檢處理。
兩者一旦共用同一個參數，「這裡有沒有洩漏」就不再是讀簽名能回答的問題。

#### 兩個刻意的行為（scout 的敵情分析改變了設計）

1. **對 contact 下 ENGAGE 是對的，即使那個 contact 是鬼**。`IntelContact` 沒有存活性欄位
   ——敵人死了或走了仍留在名單上。打一個已經不在那裡的目標**正是迷霧下該有的行為**；
   為了「修掉」它去查 DB 核對，那就是陷阱本身。
2. **階段推進只看己方單位狀態，不看「敵人清光了沒」**。理由同上：contact 不會消失，
   以「無敵蹤」當佔領條件的話任務**永遠到不了 HOLDING**。有一條測試專門釘這件事。

### 卡 2–4 未做（下一步）

- 卡 2：`mission_runtime` 接進 Kernel（**沒有 pre-movement 槽位**，要新增 Protocol +
  9 個 Kernel 建構點給 NoOp 預設）+ `parent_order_id` migration + 取消母令連帶取消子令
  （`cancel` 現在只動一列，沒有任何 cascade）+ 新增第五個 golden。
- 卡 3：LLM 詞彙表。⚠ **`contracts/ai_output.schema.json` 的 order_type enum 必須先加 MISSION**，
  否則 G1 schema 驗證會擋掉整個決策（不只那一道令），OPFOR 重試兩次後 fallback 成零令。
  且 `orders_bridge.tactical_order_to_request` 的 `else: return None` 會讓 G3 靜靜剔除 100% 的
  MISSION 令——症狀看起來會像「LLM 不肯用任務令」。
- 卡 4：COP 下令 UI + AAR 任務時間軸。
- ⚠ **G4 護欄洞**：`_STRIKE_ORDER_TYPES = frozenset({"ENGAGE"})` 連 FIRE_MISSION 都沒包，
  MISSION 更沒有。SPEC_V2 §WP-A3 已明列「ENGAGE/MISSION」。屬卡 2/3 範圍，先記在這裡。

### 卡 2 前半完成（Kernel 槽位 + 執行期 + 第五個 golden）

| 檔案 | 動作 | 說明 |
|---|---|---|
| `core/app/engine/subsystems.py` | 改 | `MissionPlanner` Protocol + `NoOpMissionPlanner` |
| `core/app/engine/kernel.py` | 改 | 任務槽位**在 movement 之前**；**NoOp 預設** |
| `core/app/orders/mission_runtime.py` | 新增 | 每 tick 推進、階段事件、記憶（可進 checkpoint）|
| `core/tests/replay/scenarios.py` | 改 | 第五個 golden `mission_seize_60` |

**任務跑在 movement 之前**：這一 tick 決定往哪走，移動這一 tick 就走出去。放在後面的話，
分解出的新 MOVE 要等下一 tick 才生效，每次階段轉換都白白慢一拍。

**槽位給 NoOp 預設而非必填**：repo 有 9 個 Kernel 建構點。改成必填會讓四個 golden 噴
`TypeError`——那看起來像 golden 壞掉，而「golden 紅了」最容易招來的錯誤反應就是去跑 rerecord。

**每道任務各自 try/except**：`kernel.run_tick` 對子系統例外**沒有任何防護**，一個 raise 會讓
runner 崩潰後被 `SimManager` 每 3 秒重建一次，形成無限重啟迴圈。一道壞任務不該拖垮整局。

**階段寫 `ai_decision` 不寫 `detail`**：`detail` 刻意不入 hash chain（非證據性診斷欄），
而任務階段是 AAR 任務時間軸要用的事實。

### golden 差點變成一份沒有意義的雜湊——兩個各自獨立的錯誤

**第一個：只記終狀態的 golden 抓不到漂移。**
第一版只把位置與姿態寫進 stateHash。移動是漸近收斂的——跑滿 60 tick 之後，
不論抵達容差設多少終點都一樣。實測把 `ARRIVAL_TOLERANCE_M` 120→300，**雜湊完全沒變**。
要釘住的是「任務照這個節奏走過這些階段」，所以改成把**各階段首次進入的 tick** 寫進熱狀態。
（當下階段也不行——那同樣會被終狀態吃掉。）

**第二個：`__pycache__` 讓連續好幾次的 mutation test 全部是假的。**
`120.0` 與 `300.0` **位元組長度相同**，而我用 `cp` 還原檔案時 Python 沒有讓 pyc 失效，
於是行程裡的常數與磁碟上的檔案長期不一致——`decomposer.__file__` 指著寫著 120 的檔案，
`ARRIVAL_TOLERANCE_M` 卻是 300。連帶後果是 **golden 是在被突變過的原始碼下錄的**：
差一點就把一份「釘住錯誤基準」的雜湊提交進去。

清掉 `__pycache__` 重錄後，兩個突變（容差 120→300、佔領後姿態 DEFENSE→HASTY）
**各自都讓 `mission_seize_60` 轉紅，而其餘四個 golden 不動**。

教訓寫在這裡給下一個人：**mutation test 之後要清 `__pycache__`**，
尤其當突變前後的字面值長度相同時。長度相同的字面值替換是這個陷阱最容易觸發的形式。
