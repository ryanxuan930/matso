---
task: WP-C10.3       # SPEC_V2 §6 WP-C10（FirePlan + at_tick/on_call 排程）
status: DONE
started: 2026-07-31T04:40+08:00
updated: 2026-07-31T06:10+08:00
agent: Opus 5
---

# WP-C10.3 火力計畫（FirePlan）+ at_tick / on_call 排程

## 目標摘要

把「預劃目標」變成實體：`FirePlan{targets:[{id, latlng, rounds, schedule}]}`。
`at_tick` 到時自動打；`on_call` 由席位一鍵呼叫。打的方式就是 C10.2 做好的
`FIRE_MISSION` 令——**本卡不新增任何物理**，只補「什麼時候、由誰、對哪個預劃點」下那道令。

驗收（SPEC_V2 §WP-C10）：預劃 H-20 分的攻擊準備射擊自動執行。✅

## 規格有一句話不成立

> 「`at_tick` 到時由 MSEL 執行器（WP-B2 複用）自動生成 ENGAGE 令」

動手前查證，三個獨立的理由：

1. `MselEngine.check()` 回的是 `list[LedgerEvent]`（`scenario/triggers.py:123`）——
   它**結構上產生不了令**，注入的事件只是帳本上的一筆紀錄。
2. kernel 的 trigger 槽在 `run_tick` 的**最後**才跑（`kernel.py:121`），而令的 drain 在
   最前面（`:115`）——在那裡生的令**必定慢一個 tick**。
3. 活執行期根本沒接它：`sim_runtime` 傳的是 `NoOpTriggerChecker()`。順手接上去會把所有
   已載入想定的 MSEL 條目一起喚醒，那是本卡範圍外的行為變更（紅線 5）。

**改走 `run_paced(pre_tick=…)`**：它在 `kernel.run_tick()` **之前**跑，而 `run_tick` 的第一步
就是 drain——所以在 tick N 落庫的令會在**同一個 tick N** 被裁決，這正是「H-20 準備射擊」
要的準時語義。而且 `run_paced` 已經把 pre_tick 包在 try/except（`runtime.py:110`），
排程器出錯不會把整個 runner 拖進重啟迴圈；trigger 槽沒有這層保護。

**SPEC_V2 的那句話已在本卡一併更正。**

## 四個刻意的決定

### 1. 不繞過任何閘門（紅線 3）

排程執行與人手呼叫共用同一個 `fires.service.fire_target` → `OrderService.submit`。
所以本局要求火協時，沒掛核准單的預劃目標**一樣打不出去**（有測試釘住）。

兩條路徑共用一個函式不是為了少寫程式碼，是為了讓它們**不可能在權限上分岔**——
「排程走的那條忘了套 gate」正是這種功能最典型的洞。

代價講清楚：`expend_request` 在**令被收下時**就兌現核准單，一張只能用一次。
所以 `fireRequestId` 是**逐目標**欄位；10 個目標的計畫在要求火協的局裡需要 10 張核准單。
（同一張掛在第二個目標上會被擋，有測試。）

### 2. payload 帶 `fire_plan_target_id`

`OrderService._find_active_duplicate` 比對的是 payload **原始 dict**：同一門砲對同一座標
同樣發數的兩個預劃目標會被判為重複——**回既有的令、假裝成功（200）、只打一發**。
帶上目標 id 才會是兩道令，順便給 AAR 追溯。

以**變異測試**驗過：拿掉這個鍵，`test_two_identical_targets_produce_two_orders` 與
`test_one_approval_cannot_cover_two_targets` 兩條立刻紅（後者更有意思——去重讓第二個目標
回報 FIRED，把「核准單已用掉」整個蓋掉了）。

### 3. `at_tick <= tick`，不是 `== tick`

runner 會暫停、崩潰重啟、回滾。只認相等的話，錯過的那一刻就**永遠不補打**，
而且不會有任何徵兆——預劃火力靜靜地不見。寧可遲到；遲多久看 `fired_at_tick` 就知道。

### 4. 執行狀態落 DB，不落行程記憶體

`MselEngine._fired` 是 `set[str]`（`triggers.py:121`），不在 checkpoint 信封裡——
每次 runner 重啟就把所有 `once` 條目重新武裝。那是已知的既有缺陷
（PROGRESS.md、`live-checkpoint.md`）。準備射擊照抄那個做法會**打兩次**。

## 自動下令的 issuer 是計畫建立者

沒有「系統」這個下令者：`validate_order` 要求 `issuer_id` 解析得到本局的
`SessionParticipant`。兩個選項是「另造一個假帳號」（AI 迴路的做法）或「用計畫作者」。

選作者，因為預劃火力的當責者本來就是寫這份計畫的人；而且陣營／指揮範圍／席位的檢查
都因此維持真實——假帳號的 `seat_role` 是 NULL，等於把那些檢查一起繞掉了。

作者已被移出本局 → 目標判 `FAILED` 並記原因，**不拿別人的身分硬送**。

## 刻意沒做的事

- **on-call 端點不另加席位檢查**。席位權限在 `OrderService.submit` 已經判過
  （`SEAT_ORDER_TYPES[FSO_FIRES]` 含 FIRE_MISSION）。在端點再判一次會比 codebase 裡
  其他所有閘門都嚴——`seat_role` 為 NULL 的既有參與者會被鎖死，而那個 NULL 語義是
  B5.1 刻意保留的。前端隱不隱藏按鈕是 UX。
- **不自動挑砲**（「就近可達砲兵」）。目標明寫 `shooterUnitId`。自動指派今天是地雷：
  `_pick_weapon` 挑射程最遠的曲射武器而**不看彈藥**，所以「有彈的砲」這個查詢會與裁決
  當下實際用的武器不一致；`WeaponResolver` 在 runner 啟動時建一次、沒有重建路徑，
  局中新部署的砲兵永遠選不到。那屬 C10.4 的臨機火力鏈，在那裡才是必要的。
- **不做想定層的火力計畫**。`create_session_from_scenario` 從不持久化 `loaded.msel`，
  `scenario_to_dict` 是手寫白名單（已經默默丟掉 `request_quotas`），`POST /scenarios`
  又是 create-only——四個會安靜掉資料的介面。只做 COP 建立。
- **`ammo_type` 沒進 FirePlanTarget**。規格的目標形狀有列，但引擎**根本不消費它**：
  全 repo 只有預檢的一行說明文字用到（`precheck.py:695`），連 ENGAGE 都不會把它帶進裁決。
  存一個沒有人讀的欄位只會讓人以為它有作用。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| `contracts/core_api.yaml` | 修改 | FirePlanView/TargetView/FireSchedule + 4 個端點（契約先行） |
| `db/prisma/schema.prisma` + migration | 修改 | FirePlan / FirePlanTarget + 3 個 enum |
| `core/app/fires/service.py` | 新增 | 建立/查詢/取消 + `fire_target`（唯一的副作用入口） |
| `core/app/fires/scheduler.py` | 新增 | `due_targets` / `run_due_fire_missions` |
| `core/app/api/fire_plans.py` | 新增 | 4 端點，列表**後端過濾陣營** |
| `core/app/sim_runtime.py` | 修改 | 排程掛進 `pre_tick`；tick 取自迴圈自己的 `sim_clock` |
| `platform/app/components/cop/FirePlanPanel.vue` | 新增 | COP 小工具 |
| `platform/app/composables/useFirePlans.ts` | 新增 | API 包裝 |

## 測試證據

- `uv run pytest` → **1447 passed / 8 skipped**（+24：service 14、scheduler 10）
- `uv run mypy` → 223 files clean；`ruff`、OpenAPI、schema-sync（20 tables / 195 欄）綠
- 前端 `npm run lint` / `npm run typecheck` 綠；容器 core+frontend 已重建
- 新增 2 支 e2e（`fire-plan.spec.ts`）：建計畫 → on-call → **指令列真的出現「火力任務」**
  （走真後端，所以這同時證明 on-call 沒有繞過 `OrderService`）；誤傷警語常駐

`orders.spec.ts` 原有 3 條紅燈與本卡無關（成因見 PROGRESS Backlog，WP-C10.2 已量測確認）。

## 陷阱（給後續維護者）

- **`at_tick` 是請求不是保證**：drain 會套通信閘門，射手 OFFLINE 時該令留在 VALIDATED
  直到通聯恢復——準備射擊可能遲到 40 個 tick。令沒有 TTL。`orderId` 存在目標上，
  所以「遲了多久」看得出來，但**本卡沒有做逾時作廢**（記入 Backlog）。
- **預檢過了不代表打得出去**：`_precheck_fire_mission` 只查座標/有曲射武器/射程，
  **不查彈藥**。裁決當下的失敗（無彈/超射程）會讓令以零毀傷 COMPLETED——
  所以 `FIRED` 的語義是「令送出去了」，不是「打中了」。UI 標籤寫「已下令」。
- **預檢讀 DB 座標、裁決讀熱狀態**：兩者可以差好幾公里。排程時的射程檢查是近似值。
- **FIRE_MISSION 沒有禁射區保護**：`run_precheck` 只在 `EngagePayload` 那條分支套
  `_precheck_no_strike`。所以火力計畫可以砲擊一個 `ENGAGE` 會被拒的 NO_STRIKE 區。
  這是既有缺口（C10.2 就存在），**不在本卡半修**（紅線 5）——已記入 PROGRESS Backlog。

## 中斷續作指引

- **本卡已完成**：實體 / REST / 排程 / 前端四段各自綠燈。
- 下一張：**C10.4**（BDA 回報帶迷霧誤差 + 散布係數掛觀測者）。C10.2 worklog 的
  「已知取捨」那段就是那張卡存在的具體理由；本卡又多一個相關缺口：
  `CALL_FOR_FIRE` 到現在**完全沒有前端**（`REQUEST_KIND_LABELS` 漏了它，
  `submitRequest` 寫死 `params: {}` 而後端要求 `target_lat/target_lng`）。
- 未做但已知：令的 TTL／逾時作廢、換一門砲重試、FIRE_MISSION 的禁射區保護、
  自動挑砲、想定層火力計畫。全部記在 PROGRESS Backlog。
