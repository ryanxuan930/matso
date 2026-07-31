# 後端做了、前端看不到／操作不了 —— 盤點

> 6 個 agent 平行查證，2026-07-31。**一律唯讀**，證據為檔案:行 / rg 結果 / 對執行中服務的 curl。
> 後勤（C7：`supply` / `SUPPLY_POINT` / 補給水位 / 整補）不在本盤點內——當時正由另一條 workflow 修改。

共 **89 項**：完全沒有 UI 39、完整可用 25、只覆蓋一部分 15、只有間接路徑 8、看得到改不了 2

## 兩個被推翻的前提

- 「後端發 124 種事件、前端只有 78 條標籤」**不成立**——那個數字來自壞掉的指令（`rg -oh` 的 `-h` 在 ripgrep 是 `--help`）。
  實測後端 47 種、前端 52 條，`test_frontend_event_labels.py` 以 AST 雙向鎖死。**事件型別的中文化沒有缺口。**
- 「實作端有 135 個 route」過期——以執行中服務的 `/openapi.json` 為準是 **67 條路徑**。

兩者都提醒同一件事：**盤點要以跑起來的系統為準，不要相信靜態計數**。


---

## 修正排程

依「**使用者影響 × 工作量**」分批。批次是照**共用的程式路徑**切的，不是照單項——
同一批的東西一起改才不會來回動同一個檔。

### P0 · 止血：想定編輯器會**靜默刪掉**資料　　`S`

**這是唯一一條現在就在毀壞使用者資料的。**
`useScenarioEditor.ts` 的 `importScenario` 參數型別只有 `{scenario, orbat, msel}`、
`exportScenario` 的回傳也只有這三段，而 `GET /scenarios/{id}` 回的是完整 bundle。
於是**任何人用編輯器打開一份出貨想定、改個名字存回去，交戰規則（roe）、地形覆寫
（overrides）、全部單位的武器彈藥（equipment）就沒了**——畫面顯示「已存到伺服器」，零警告。
四份 example 想定**全部**帶這三樣東西。

修法：兩個函式帶上 bundle 的其餘鍵（passthrough 即可，不必先做 UI）。
再加一條 roundtrip 測試釘住「開了再存不掉東西」。

### P1 · 事件回饋的最後一哩　　`M`

兩件事在同一條路徑上，一起做：

1. **`LedgerEvent.detail` 一個字都不轉發。** `build_event_envelope` 用 15 鍵白名單過濾，
   而 `aar/export.py` 的匯出也沒帶（實測 185 筆事件的鍵集合裡沒有 detail）。
   於是油料殘量、卡住的地形格、觸雷的障礙名稱、耗損是行軍磨的還是硬穿代價——
   **即時看不到、AAR 看不到、匯出檔也沒有**。
   最能說明問題的證據：`useCopFeed.ts` 的 `REASON_LABELS` 有五條翻譯
   （OUT_OF_FUEL / IMPASSABLE_TERRAIN / MARCH / FORCED_CROSSING / TARGET_GONE）
   **在 feed 上永遠不可能被觸發**——有人認真寫了翻譯，只是線沒接上。
2. **四種帳本事件只寫 DB、不進串流。** `ORDER_REJECTED`、`ORDER_RESTRICTED_FIRE_OVERRIDE`、
   `REQUEST_SUBMITTED`、`REQUEST_DECIDED` 的 sink 是 `LedgerWriter`，而 `state/ledger.py`
   全檔沒有一行 redis。前端四條中文標籤備好了，一次都不會被渲染。
   ⚠ 其中兩種是這一輪剛加的——**加了帳本事件卻沒接串流，是我自己留下的缺口**。

### P2 · 下令可操作性　　`S × 7`

- **席位不過濾下令選單**：後端有權威表（`SEAT_ORDER_TYPES`）、送出才擋
  （`ORDER_SEAT_DENIED`）。作戰官看得到「火力任務」，填完發數按送出才被彈回。
  修法最省：把 `SEAT_ORDER_TYPES[my_seat]` 算好塞進 participants 的「我」那一段，
  前端拿它 disable 選項（**不要**在前端再寫一份硬編碼表）。
- **九個送不出的 payload 欄位**（後端收得下、引擎讀得到、面板沒有輸入框）：
  `FIRE_MISSION` 的 `ammo_type`(發煙) / `ttl_ticks` / `weapon_id`、
  `ENGAGE.fire_request_id`、`FORMATION.column_spacing_km`、
  `MISSION/DEFEND.orientation_deg`、`MISSION/MOVE_MARCH.spacing_km`、
  `ENGINEER.radius_m`、以及 `acknowledge_restricted` 在 FIRE_MISSION 路徑沒有渲染點
  （限制射擊確認寫死在 ENGAGE 的 `v-else` 裡，於是 **FIRE_MISSION 變成絕對禁射**）。
- **破障令實際上下不了**：要手打障礙標註 id，地圖點選不會填。
  C2 障礙工兵在人類席位上只完成一半。

> 一個值得記的模式：**AI 比人類能下的令更完整**。`orientation_deg`/`spacing_km`
> 在 `decomposer.py` 自動展開時用得到，人手動下卻編不了。同一局裡 AI 陣營的可用手段
> 比人類多，而畫面上看不出為什麼。

### P3 · 檢討可用性　　`S + M`

- **`/aar/missions` 是唯一一條完全沒接的業務端點**（67 條裡的 1 條）。
  curl 就有真資料，畫面零蹤影。任務級下令是最貴的功能，而「執行得好不好」的唯一量化證據
  目前沒有任何畫面。
- **令與申請單的「內容」沒有 renderer**：`OrderResponse.payload` / `.precheck`、
  `RequestView.params`、`MessageView.ref_id`。這一組加起來讓火協鏈
  （申請 → 核准 → 掛單射擊 → 檢討）**在畫面上是斷的**：核准者看不到申請內容、
  指令列看不到打哪裡、事後看不到為什麼被駁回。資料全在回應體裡，純前端工。
- **Ledger 查詢**：契約早就宣告 `GET /sessions/{id}/ledger`，**後端沒實作**（404），
  而 DB 裡有 21 萬筆真實事件。事後爭議裁決目前只能請工程師去撈 DB。

### P4 · 契約漂移（與 SPEC_V2 的 G5 合併）　　`M`

`_IMPL_ONLY` 11 條實作有、契約沒有——涵蓋自主主控台、AAR 重播/報告/匯出、
ORBAT 權限、地圖狀態編輯、LLM 連線測試。**這五塊的前端型別全部是人手抄的**，
後端一改欄位名就靜默變空白，而所有閘門都是綠的。
反方向 `_CONTRACT_ONLY` 7 條契約有、實作沒有，前端拿得到型別、按下去吃 404。

⚠ **G5 的既有驗收條件是壞的**（見 `docs/SPEC_V2_AUDIT.md`），接手前要先改掉。

### P5 · 統裁回饋　　`S / M`

寫入路徑幾乎沒有缺口，**讀取路徑到處是洞**：
- 推演局「現在是不是暫停中」沒有任何權威來源（Redis 有鍵、沒有 GET）——
  操作員會把暫停誤判成系統掛了。
- AI 陣營累計下了幾道令 / 離失控保護上限還有多遠（`total_submitted` 只存在於型別檔）。
- 回滾點挑選器：後端一次回 3799 筆，前端塞進一個原生 `select`。
- MSEL 待命清單只印得出 `msel-003` 這種 id 字串——統裁不知道那是「敵增援」還是「橋梁被炸」。
- `/metrics` 零消費端。

### P6 · 資產層（想定編輯器要能獨立產出一份能打的想定）　　`M / L`

- ROE section + 機動覆寫（併卡，共用同一段 bundle export/import 改動）
- **ORBAT 編裝**（`L`）——這是「編輯器能不能獨立產出一份能打的想定」的分水嶺
- 條件 DSL 補 `manual` / `after_ticks_of` / `held_for` / `contact_established`
  （白軍控制台的「扣發／跳過」按鈕已經做好了，只差劇本產不出對應的 manual 事件）

### 順手做掉（各一天內）

- `mission_phase` **反向的病**：前端 `OrdersPanel.phaseLabel()` 畫好了、契約也宣告了，
  但 `OrderResponse` 根本沒這個欄位——「前端畫好、後端不填」，與其餘各條相反。
- 陣營顯示名稱（`display_name`）輸入框、軍械庫油料三欄。
- 軍械庫的死欄位標示「未接引擎」：DRONE 整組九欄 + `rate_of_fire_rpm` /
  `penetration_type` / `guided` / `reload_ticks` / `countermeasure_resistance`
  在 `core/app/` 零消費端。使用者填了、存了、推演時毫無影響。
  比照編輯器對 WEGO/IGO_UGO 的做法加「（未實作）」後綴，成本極低。

### 兩條該寫進 HOW_TO 的紀律

1. **新增熱狀態鍵時，同步決定它的畫面歸屬（含「刻意不畫」）。**
   `broadcaster.py` 是 denylist（引擎每寫一個新鍵就自動外送），
   `useLiveState.ts` 是白名單（只讀九個）——**兩邊的成長方式相反，缺口只會愈開愈大**。
   C1、C3 的狀態欄位一寫進熱狀態就上了線，但沒人去加對應的讀取器。
2. **落帳與廣播是兩件事。** 寫 `LedgerWriter` 不等於進 WS 串流；
   要讓操作員看到就得另外 publish（見 P1 第 2 點）。

---

## 完全沒有 UI（39）


### [S] AAR「任務時間軸」——每道任務走過哪些階段（待命→機動→接戰→鞏固）、各花多久、失敗與否

*端點*

**後端**：端點實作於 core/app/api/aar.py:227-241 `GET /api/v1/sessions/{id}/aar/missions`（呼叫 core/app/aar/missions.py:75 `build_timelines`，走與其他 AAR 端點同一條 fog 投影）。活體驗證：`curl -s .../api/v1/sessions/e2e-orders/aar/missions` 回真實資料，例：`{"order_id":"096c1e3f...","mission_type":"DEFEND","legs":[{"phase":"PLANNED","duration_ticks":172640},{"phase":"MOVING","duration_ticks":33},{"phase":"CONSOLIDATING","note":"抵達防區"}]}`。DB 佐證：`select eventType,count(*) from TacticalEventLog` → MISSION_PHASE_CHANGED 8 筆、MISSION_ENDED 1 筆。

**前端**：全前端零呼叫端。以正規化字串比對抽出 platform/app 內所有 API 路徑字面量（68 條），`/sessions/{}/aar/missions` **完全不在其中**——這是後端 67 條路徑裡唯一一條前端從未觸碰的業務端點。AAR 頁 platform/app/pages/session/[id]/aar.vue 只載 replay / replay/states / stats / report 四支（該檔 34-40 行的 Promise.all），畫面區塊只有「統計 / 時間軸重播 / AI 敘事報告 / 匯出」。契約也沒收錄（core/tests/unit/test_contract_conformance.py 的 `_IMPL_ONLY` 有這條），所以連型別都拿不到。

**操作員因此**：檢討會上想回答「第 3 連的防守令從下達到真正進入陣地花了多久？哪一段卡住？」——後端已經算好了，統裁卻只能在時間軸上自己拖捲軸猜。任務級下令（奪佔/防守/掩護幕/行軍）是本系統最貴的一個功能，而它「執行得好不好」的唯一量化證據目前沒有任何畫面。

**怎麼補**：1) contracts/core_api.yaml 補 `GET /sessions/{id}/aar/missions` operation + MissionTimeline/MissionLeg schema（順手把 `_IMPL_ONLY` 這條刪掉）；2) 重生 platform/app/types/api.ts；3) platform/app/composables/useAar.ts 加 `aarMissions(id)`；4) platform/app/pages/session/[id]/aar.vue 在「統計」與「時間軸重播」之間插一段任務時間軸表（每列一道令：任務型、單位、各階段長條、errors/failed 標記），用既有的 MISSION_PHASE_LABELS 上中文；5) e2e 用 e2e-orders 這一局釘一條斷言。

**優先度**：最早做——它是唯一一條「後端完全做好、curl 就有資料、前端一行都沒接」的端點，投入產出比最高


### [S] 席位（參謀分工）→ 可下令型別的權威表——誰能下移動令、誰能下火力令

*端點*

**後端**：core/app/seats/__init__.py:25-51 `SEAT_ORDER_TYPES`：COMMANDER=全部（扣掉未實作）、S3_OPS={MOVE, MISSION, POSTURE, FORMATION, ENGINEER}、FSO_FIRES={ENGAGE, FIRE_MISSION}、S2_INTEL=∅、S4_LOG={RESUPPLY}、OBSERVER=∅。強制點在 core/app/orders/validator.py:145-151：`seat is not None and not seat_may_order(seat, order_type)` → 拋 `ORDER_SEAT_DENIED`。該檔案自己標明「**這裡是席位 → 可下令型別的唯一權威表**」。`rg SEAT_ORDER_TYPES core/ contracts/` → 只有 core/app/seats、core/app/orders/validator.py、單元測試；**沒有任何端點回傳它**。

**前端**：前端知道自己的席位（platform/app/pages/session/[id]/cop.vue:148 `mySeatRole`，來自 participants 的 `my_seat_role`），但只把它傳給 C2 面板（同檔 894 行 `<C2Panel :my-seat="mySeatRole">`）。下令面板 platform/app/components/cop/UnitsOrderPanel.vue:154-162 是**七個寫死的 `<option>`**（MOVE/ENGAGE/FIRE_MISSION/POSTURE/MISSION/FORMATION/ENGINEER），沒有任何 seat 相關判斷（`rg -n 'seat' UnitsOrderPanel.vue useCopOrdering.ts` → 零命中）。

**操作員因此**：作戰官（S3）在下令面板上看得到「火力任務（打座標）」，選好目標、填好發數、按送出——才被彈回「作戰官不得下「FIRE_MISSION」令」。火力支援協調官（FSO）反過來看得到「移動」卻下不了。在時間壓力下的推演裡，這種「選單給你、送出才擋」等於每個參謀都要靠踩雷學會自己的職掌。

**怎麼補**：最省的做法是不新增端點：把 `SEAT_ORDER_TYPES[my_seat]` 算好塞進 participants 的「我」那一段（core/app/api/participants.py 的 roster，欄位如 `my_allowed_order_types: string[]`，順帶帶上 `UNIMPLEMENTED_ORDER_TYPES`）→ 契約補欄位 → platform/app/components/cop/UnitsOrderPanel.vue 用它把 `<option>` 過濾掉（或 disable + tooltip 說明「此令型屬火力支援協調官」）。既有測試 core/tests/unit/test_order_validator.py 已有覆蓋守門，別重複一份前端硬編碼表。

**優先度**：早做——這是每一場演習每一個參謀都會撞到的日常摩擦，且修法很便宜


### [S] 任務結束的結局——後端記錄任務是在哪個階段結束、連帶取消了幾道子令

*事件回饋*

**後端**：core/app/engine/mission_wiring.py:166-172 MISSION_ENDED 的 `ai_decision = {mission_order_id, phase: state.phase.value, cancelled_sub_orders: cancelled}`。

**前端**：broadcaster.py:95-118 的白名單沒有 `phase`、沒有 `cancelled_sub_orders`、沒有 `mission_order_id`，三個鍵全被丟掉。useCopFeed.ts:82 只剩通用格式的「任務結束」四個字。

**操作員因此**：「第1營 任務結束」——是奪佔成功了、還是打到一半崩了被系統收掉？連帶取消了 3 道子令（部隊現在沒有令、停在原地）也沒人講。指揮官要重新下令，卻不知道剛剛那批子令是不是還在跑。

**怎麼補**：broadcaster.py:95-118 白名單加 `phase`（`cancelled_sub_orders` 若只是數字也可加）；useCopFeed.ts 為 MISSION_ENDED 補一條專屬敘述，套 `MISSION_PHASE_LABELS`（useOrders.ts:50 已有中文表）。

**優先度**：早做——一行白名單 + 一條敘述，而任務級下令是 V2.1 的招牌功能。


### [S] 戰況事件行末的「· 令型」後綴（如「… · 移動」）

*事件回饋*

**後端**：`payload.order_type` 只有兩條路會出現：core/app/api/orders.py:45（E2E stub 模式的 `publish_event`）與 core/app/orders/service.py:154（ORDER_REJECTED 的 `ai_decision`，而那條事件根本不進串流，見前述項目）。broadcaster.py:95-118 的白名單沒有 `order_type`。

**前端**：platform/app/composables/useCopFeed.ts:290 `const ot = payload?.order_type ? \` · ${orderTypeLabel(...)}\` : ''`，:301 把它接進每一行通用格式。實際活推演中 `ot` 恆為空字串。

**操作員因此**：影響很小（少一個後綴），但它是這個 repo 招牌病的一個標本：前端寫了一段「看起來會顯示令型」的程式碼，測試不會紅，畫面也不會壞，只是那個資訊永遠不出現。下一個人讀這段會以為它有在動。

**怎麼補**：兩選一：白名單補 `order_type`（後端已在 ORDER_REJECTED 帶了，順著上面「ORDER_REJECTED 進串流」那張卡一起做），或把 useCopFeed.ts:290,301 的死路徑刪掉並註明理由。

**優先度**：順手做——跟著 ORDER_REJECTED 那張卡一起處理，不值得單開。


### [S] 既有指令的逐項預檢理由（視線／射程／彈藥／ROE 各過了沒、細節是什麼）

*欄位*

**後端**：`GET /api/v1/sessions/{id}/orders` 每一筆都帶完整 `precheck`。實測（core :8000，session a3126ca2…）第一筆 ENGAGE 回：`"precheck": {"feasible": true, "checks": [{"name": "line_of_sight", "passed": true, "detail": "視線通暢（直線 0.1 km），最小餘隙 10m"}, {"name": "range", …}, {"name": "ammo", …}]}`。模型見 core/app/orders/schemas.py:148-167，來源 core/app/orders/service.py:283 `_to_response`。

**前端**：指令列只渲染狀態標籤。`platform/app/components/cop/OrdersPanel.vue:70-95` 的每一列只有 unit / order_type / target / T{issued}→T{resolved} / `orderStatusLabel(o.status)` + 取消鈕，沒有碰過 `o.precheck`。前端唯一會顯示 checks 的地方是 `UnitsOrderPanel.vue:549-557`，而它讀的是 `useCopOrdering.ts:112` 的 `precheck` ref——那個 ref 只在**送出當下**被填，`resetOrderForm()`（useCopOrdering.ts:189）換單位就清掉。

**操作員因此**：「為什麼這道令沒打出去」只在按下送出的那一瞬間看得到。重新整理頁面、換個單位、或交接班之後，指令列上就只剩一個紅色「已駁回」——參謀答不出是超射程、沒視線、還是沒彈，只能重下一次令看它再被打回來一次。統裁在覆盤時同樣看不到。

**怎麼補**：`platform/app/components/cop/OrdersPanel.vue` 每列加可展開的預檢明細（沿用 `UnitsOrderPanel.vue:549-557` 既有的 ✓/✗ 樣式），REJECTED 者預設展開；`platform/app/composables/useOrders.ts` 的 `OrderResponse` 型別已含 precheck，不需動後端與契約。

**優先度**：高——修起來最便宜的一條，而且直接解掉「令被吃掉、沒人知道為什麼」這個最常見的現場抱怨


### [S] 姿態轉換進度（宣告要掘壕之後，還要多久才真的享有掘壕防護）

*欄位*

**後端**：core/app/engine/suppression_wiring.py:57-62 `_write_posture` 每次都把 `posture` / `posture_target` / `posture_since_tick` 三個鍵一起寫進熱狀態；熱狀態走 denylist 外送（core/app/state/broadcaster.py:151-166 的 `_INTERNAL_FIELDS` 只擋回報座標等五個鍵），所以這三個鍵**全都會出現在 STATE_DIFF 裡**。轉換工時見 core/app/orders/schemas.py:91-98（HASTY 即時／DEFENSE 30 分／DUG_IN 4 小時）。

**前端**：只讀了三分之一。`platform/app/composables/useLiveState.ts:57-62` 的 `livePosture()` 只取 `posture`（已就位的那一級）；`rg -n 'posture_target|posture_since_tick' platform/app` → **零結果**（含 types/api.ts）。`UnitDetailCard.vue:82-86` 因此只顯示當前姿態徽章。

**操作員因此**：指揮官下了「進入掘壕」的令，資訊卡上還是寫「移動中」，而且四個小時內都不會變。他不知道令有沒有生效、也不知道還要多久——只能自己拿碼表算，或是反覆重下同一道令。反過來，敵火將至時他也答不出「我們現在掘到什麼程度、來不來得及」。

**怎麼補**：`useLiveState.ts` 加 `livePostureTarget()` / `livePostureSince()`；`UnitDetailCard.vue:82-86` 的姿態徽章在 target≠current 時改成「移動中 → 掘壕（還需 N 分）」，分母用該局 `tick_rate_ms` 與 `adjudication` 的轉換工時常數換算。

**優先度**：中——C1 壓制與姿態整張卡的操作回饋就缺這一塊


### [S] 單位當前的乘駐車狀態、隊形與行軍間隔

*欄位*

**後端**：core/app/engine/formation_wiring.py:62-71 把 `formation` / `mounted` / `footprint_m` / `column_spacing_km` 寫進熱狀態（→ 隨 STATE_DIFF 外送）。這些不是裝飾：`core/app/engine/movement.py:366` 用 `formation` 改行軍速度，`core/app/engine/fire_wiring.py:601` 用它算受彈面。

**前端**：只有「下令」那一半，沒有「顯示」那一半。`rg` 的命中全部集中在下令面板：`UnitsOrderPanel.vue:385`（隊形下拉）、`:396`（乘駐車下拉）、`useCopOrdering.ts:347-348,391-392`（組 payload）。`footprint_m`、`column_spacing_km` 在 `platform/app/` 內除了 types/api.ts 的契約說明字串外**零命中**；`UnitDetailCard.vue` 沒有任何一欄顯示現況。

**操作員因此**：下完乘車令之後，畫面上沒有任何地方告訴你這支部隊現在是乘車還是徒步、排的是縱隊還是楔形、行軍間隔拉了幾公里。這三件事直接決定它跑多快、以及一發砲彈能罩到幾個平台——指揮官在做「要不要拉開間隔」的取捨時，看不到自己上一個決定的結果。統裁在裁決一次砲擊前也查不到目標當時的隊形。

**怎麼補**：`useLiveState.ts` 加 `liveFormation()` / `liveMounted()` / `liveSpacing()`（同一條「patch 優先、退回初值」規則）；`UnitDetailCard.vue` 加一列「乘車 · 縱隊 · 間隔 2.0 km」。註：`/units` 的 `UnitView` 本身沒有這四欄，只能走串流；若要重連後也看得到，順便在 `UnitView` 補（那就變 M）。

**優先度**：中——C3 乘駐車/隊形做完了但看不見，等於半套


### [S] 申請單的內容（火力支援要打哪個座標、幾發、什麼時間；偵察要看哪裡）

*欄位*

**後端**：core/app/api/c2.py:104 `RequestView.params: dict[str, Any] = {}`，由 core/app/c2/service.py:55,72 原樣存回。前端自己的註解證明這裡確實裝著關鍵資料：`platform/app/composables/useC2.ts:97` —「`params` **不能寫死成 `{}`**：`CALL_FOR_FIRE` 的後端要求 `target_lat`/`target_lng`」。

**前端**：送得出去、看不回來。`rg -n params platform/app/components/cop/C2Panel.vue` → **零結果**。申請單清單 `C2Panel.vue:311-330` 只渲染 kind / status / requested_by / requested_seat / requested_at_tick / decided_by / decision_note，核准與駁回按鈕就長在同一個 `<li>` 裡（`:327-330`）。

**操作員因此**：火力支援官（或白軍）按下「核准」時，畫面上只有一行「火力支援 · 待決 · 三營參一 · T120」。他看不到申請的是哪個座標、幾發、用什麼彈——等於盲簽。火協的整個意義就是「有人在放行之前看過目標位置」，這一步在 UI 上被跳過了；申請人自己回頭查也看不到當初送了什麼。

**怎麼補**：`platform/app/components/cop/C2Panel.vue` 的申請單列加 params 摘要（依 `kind` 排版：CALL_FOR_FIRE → 座標+發數+彈種、RECON → 目標區），核准/駁回鈕移到摘要下方；座標可直接掛 `MapPointPicker.vue` 既有的「在圖上標示」動作。

**優先度**：高——這是安全性等級的缺口（核准動作沒有可見的審查對象），且成本 S


### [S] 信文與申請單的關聯（`ref_id`：這封核准信在講哪一張申請單）

*欄位*

**後端**：core/app/api/c2.py:63 `MessageView.ref_id`；契約說明（platform/app/types/api.ts:1570）明寫「REQUEST/APPROVAL 會帶 ref_id 指向申請單」。發信端已經在填：`platform/app/composables/useC2.ts:71` `ref_id: opts.refId || null`。

**前端**：寫得進去、讀不出來。`rg -n ref_id platform/app --glob '!types/api.ts'` 只有 `useC2.ts:71` 這個寫入點；信文列表 `C2Panel.vue:255-280` 完全沒有渲染或連結它。

**操作員因此**：信文區跳出一封「已核准」，收信人得自己往下捲、憑時間與人名去猜它對應哪一張申請單。單子一多（一場推演幾十張火協）就對不起來了，而火協的責任鏈正是要靠這條對應關係。

**怎麼補**：`C2Panel.vue` 的信文列在 `m.ref_id` 存在時加一個可點的引用標籤，點了捲動/高亮申請單分頁裡的那一列（兩份清單已經在同一個元件內，不需新 API）。

**優先度**：低中——單獨看是小事，但和上一條（params 看不到）疊起來，整條火協鏈在畫面上就是斷的


### [S] 武器類別（直射／曲射／飛彈）

*欄位*

**後端**：core/app/api/units.py:98 `WeaponView.category`，由 core/app/api/units.py:309 從 `EquipmentTemplate.category` 填入。這一欄在後端是有物理後果的：曲射才有最小射程死角、曲射交戰才需要掛已核准的 FIRE_SUPPORT 單（core/app/orders/schemas.py:69-70 的 `fire_request_id` 註解）。

**前端**：`rg -n 'category' platform/app/composables/useCopOrdering.ts platform/app/components/cop/UnitsOrderPanel.vue platform/app/components/cop/UnitDetailCard.vue` → **零結果**。武器下拉 `UnitsOrderPanel.vue:503,525-528` 只印 name / max_range_m / min_range_m / 活彈量；火協申請單下拉（`:318`）無論選的是步槍還是榴砲都照樣出現。

**操作員因此**：下令者在武器下拉裡看不出哪一門是砲、哪一支是槍——只能從「有最小射程」這個間接線索去猜。於是「為什麼這一發要火協核准、那一發不用」變成一條要靠背的規則，而不是畫面上看得到的事實。

**怎麼補**：`UnitsOrderPanel.vue` 的武器選項前加類別徽章（沿用 `platform/app/composables/useWeaponVocab.ts` 既有的類別中文對照）；火協單下拉改成只在選中曲射/飛彈類武器時出現。

**優先度**：低中——不阻塞作業，但它會讓火協規則從「口耳相傳」變成「畫面上寫著」


### [S] 原子快照回報的觀測視角（`observer_faction`）

*欄位*

**後端**：core/app/api/state.py:58,90-95 `StateSnapshotView.observer_faction`，由 relations handler 推導（全知未指定 `as_faction` → None ＝ god view）。

**前端**：`rg -n observer_faction platform/app --glob '!types/api.ts'` → **零結果**。同一支回應的 `comms_posture` 有讀（cop.vue:324）、`last_seq`/`tick` 有讀（sessionStream.ts:65-66），就這一欄跳過。前端改用自算的 `platform/app/composables/useCopUnits.ts:63-65`（`viewpoint || myFaction`）。

**操作員因此**：影響輕微（自算值目前與後端一致），但它是一份**沒有校驗的自算**：白軍切視角、或參與者身分與 token 角色不一致時，畫面認定的「我是誰在看」與後端實際套用的迷霧規則可能對不上，而畫面上沒有任何東西會揭穿這件事。

**怎麼補**：`platform/app/stores/sessionStream.ts` 的 `pullSnapshot()` 把 `snap.observer_faction` 存進 store；`useCopUnits` 改以它為準（自算值退成 fallback），不一致時在 `CopHeader.vue` 出一個警示。

**優先度**：低——目前無症狀，但它是「畫面自己猜迷霧規則」的唯一殘餘，值得在下一次動 COP 時順手接上


### [S] 發煙任務——用砲兵/迫砲在指定座標放煙幕遮蔽視線（不產生傷亡）

*下令*

**後端**：`core/app/orders/schemas.py:80-82`（`FireMissionPayload.ammo_type`，註解明寫 `SMOKE` → 落點生成煙幕不產生傷亡）；`core/app/engine/fire_wiring.py:279` `if (order.ammo_type or "").upper() == "SMOKE": return self._emplace_smoke(...)`；整支 `core/app/engine/smoke_wiring.py`（煙幕存成 `MapFeature(kind="SMOKE")`、會隨風漂移、會被 purge）；契約 `contracts/core_api.yaml:1344` 的 OrderRequest.payload 描述已寫明 `ammo_type=SMOKE＝發煙任務`。

**前端**：`platform/app/composables/useCopOrdering.ts:420-427` 的 FIRE_MISSION payload 只組 `target_lat/target_lng/rounds/fire_request_id`，**完全沒有 ammo_type**；`UnitsOrderPanel.vue:273-327` 火力任務區塊沒有任何彈種控制項。`rg -n "SMOKE|發煙|煙幕" platform/app --glob '!types/api.ts'` 只命中 `useCopFeed.ts:49 SMOKE_EMPLACED: '施放煙幕'`（事件標籤）與 `InjectActionForm.vue:142`（風速提示）。

**操作員因此**：指揮官想「先放一道煙再渡河/脫離」——做不到。系統煙幕模型是完整的（會擋視線、會漂移、會散），但戰場上唯一放得出煙的只有白軍手動畫標註或 API 直送。事件流裡還印著「施放煙幕」這個標籤，參謀會以為有人放了煙，其實沒有任何席位按得出來。

**怎麼補**：`UnitsOrderPanel.vue` FIRE_MISSION 區塊加一個「任務種類：殺傷／發煙」切換（或彈種下拉，取該單位曲射武器的 `ammo_types` 中含 SMOKE 者），`useCopOrdering.ts:420` 的 payload 帶 `ammo_type`；發煙時把「發數＝持續時間」的語意寫進提示，並把落彈警語換成「不產生傷亡、雙面遮蔽」。

**優先度**：很早——S 級改動就解鎖一整個已完工的子系統（煙幕），投報比是本視角最高的一項。


### [S] 火力任務打進限制射擊區時的下令者確認（acknowledge_restricted）

*下令*

**後端**：`core/app/orders/schemas.py:43-46`（`OrderRequest.acknowledge_restricted`）；`core/app/orders/precheck.py:179-186` `_precheck_fire_mission_no_strike` **對 FIRE_MISSION 的落點同樣做 RESTRICTED_FIRE 判定並吃 acknowledged 旗標**（:151 「目標位於限制射擊區——已由下令者明確確認」）；`core/app/orders/service.py:110-117` 確認後寫 `ORDER_RESTRICTED_FIRE_OVERRIDE` 進 Ledger。

**前端**：確認核取方塊 `data-testid="restricted-ack"` 位於 `UnitsOrderPanel.vue:489-498`，**在 `:463 <template v-else>` 之內——那是 ENGAGE 專屬分支**；FIRE_MISSION 走 `:273 v-else-if`，整段沒有這個核取方塊。`useCopOrdering.ts:127-132` 的 `restrictedBlocked` 是共用 computed，但沒有任何 FIRE_MISSION 的渲染點消費它。

**操作員因此**：面目標射擊只要落點沾到限制射擊區，就變成事實上的絕對禁射區——跟 NO_STRIKE 沒有差別。錯誤訊息還一直叫下令者「確認仍要射擊請重送並勾選確認」，而那個核取方塊在火力任務面板上不存在。統裁想演練「明知是管制區仍決定射擊、事後在 AAR 追究」這個科目，人類席位做不出來（AI 反而做得到）。

**怎麼補**：把 `restricted-ack` 那段 `<label>` 從 ENGAGE 的 `v-else` 提到共用位置（送出按鈕之前），條件維持 `ordering.restrictedBlocked`；`useCopOrdering.ts` 的 watch 除了 `targetUnitId` 之外，`firePoint` 變更時也要把 `restrictedAck` 退回 false（換落點就重新確認）。

**優先度**：早——單檔搬一段模板，卻補回一條完整的 ROE 演練科目與 AAR 追究鏈。


### [S] 行軍間隔（column_spacing_km）——拉開縱隊間距換取被砲擊時的被動防護

*下令*

**後端**：`core/app/orders/schemas.py:110-118`（`FormationPayload.column_spacing_km`，0<x≤50，且三欄至少擇一）；`core/app/engine/formation_wiring.py:112-126` 讀 payload 並傳給 `set_formation`；同檔 :66-71 換算 `footprint_m`（面射擊讀的受彈面）；`core/app/adjudication/formation.py:134-149` `column_footprint_m`；測試 `core/tests/unit/test_formation_wiring.py`。契約 `core_api.yaml:1344` 也明寫 FORMATION 三欄。

**前端**：`useCopOrdering.ts:388-394` 的 FORMATION payload 只組 `formation` 與 `mounted`；`UnitsOrderPanel.vue:382-405` 只有兩個下拉。`rg -n "column_spacing" platform/app --glob '!types/api.ts'` 零命中。前端 `canSubmit`（:81）也只認這兩欄。

**操作員因此**：「拉開車距通過砲擊區」是最基本的行軍指揮動作，指揮官下不了。系統其實會依間隔算受彈面（整營擠在一起 vs 拉開，同一發砲彈罩到的平台數不同），但這個取捨只有 MISSION/MOVE_MARCH 分解器自動用得到，人下 FORMATION 令永遠是預設值。

**怎麼補**：`UnitsOrderPanel.vue` FORMATION 區塊加一個「行軍間隔（km）」數字輸入（留空＝不變更），`useCopOrdering.ts:388` 條件帶 `column_spacing_km`；提示寫「拉開間隔＝單發砲彈罩到的平台變少」。

**優先度**：中——S 級，且與下一項（MOVE_MARCH spacing_km）可以同一張卡做掉。


### [S] 防禦正面（MISSION/DEFEND 的 orientation_deg）——防區只對正面 ±90 度內的敵接戰

*下令*

**後端**：`core/app/orders/mission.py:61-66`（`DefendParams.orientation_deg`，0–360）；`core/app/orders/decomposer.py:309-312` 註解「過去收得下、一次都沒被讀過」現已接線 → `_in_arc(contacts, ..., p.orientation_deg)`（:326-337）；測試 `core/tests/unit/test_decomposer.py:271-279`。契約 `core_api.yaml:1326-1330` 明寫這一欄的語義。

**前端**：`useCopOrdering.ts:407-419` DEFEND 只組 `{ area, area_radius_m }`；`UnitsOrderPanel.vue:368-379` 只有半徑輸入。`rg -n "orientation_deg" platform/app --glob '!types/api.ts'` 零命中。

**操作員因此**：參謀下「面向東方防守」下不出來——所有防守任務都是 360 度全向接戰，等於每個防禦陣地都自帶環形工事。想演練「翼側暴露」「敵從背後迂迴」這類科目，任務級下令做不到（只能改用低階令自己微操）。

**怎麼補**：DEFEND 分支加方位輸入：最省事是數字欄（度，正北 0，留空＝全向），較好的是在標定防區後讓使用者再點一下地圖決定正面方向（`cop.vue:434-443` 已有多點收集的手感可複用）。payload 條件帶 `orientation_deg`。

**優先度**：中——這是任務級下令的招牌參數之一，做不出來會讓 MISSION 看起來比實際笨。


### [S] 行軍間距（MISSION/MOVE_MARCH 的 spacing_km）

*下令*

**後端**：`core/app/orders/mission.py:75-79`（`MarchParams.spacing_km`，預設 0.5）；`core/app/orders/decomposer.py:436-448` 第一步就送一道 `_formation_column(p.spacing_km, "行軍縱隊展開")`（:99-103 payload 帶 `column_spacing_km`）；測試 `core/tests/unit/test_decomposer.py:305-324`。契約 `core_api.yaml:1332-1335` 有寫。

**前端**：`useCopOrdering.ts:407-419` MOVE_MARCH 只組 `{ route: path }`；面板 MISSION 區塊只有 `mission-radius` 一個數字輸入，且它 `v-if="missionNeedsPoint"`（`UnitsOrderPanel.vue:368`），MOVE_MARCH 連那個輸入都看不到。

**操作員因此**：下行軍任務時無法宣告車距，一律吃 0.5 km 預設。同一條路上「疏開行軍」與「密集行軍」在畫面上、在下令上都沒有差別——而系統其實會照間距算受彈面。

**怎麼補**：MISSION 區塊把數字輸入依 mission_type 分流：SEIZE/DEFEND 顯示半徑、MOVE_MARCH 顯示「行軍間距（km）」，`useCopOrdering.ts:417` 的 `{ route: path }` 補 `spacing_km`。

**優先度**：中——與 FORMATION 的 column_spacing_km 同一個概念，兩者合一張卡。


### [S] 面目標射擊指名射擊武器（FIRE_MISSION 的 weapon_id）

*下令*

**後端**：`core/app/orders/schemas.py:79`（`FireMissionPayload.weapon_id`）；`core/app/engine/fire_wiring.py:107-108` `weapon_template_id`、:167 `wid = payload.get("weapon_id")`、:186-190 註解「None＝取射程最遠的曲射武器」。契約 `core_api.yaml:1344` FIRE_MISSION 有列 `weapon_id?`。

**前端**：`useCopOrdering.ts:420-427` FIRE_MISSION payload 不帶 `weapon_id`；武器下拉 `data-testid="engage-weapon"`（`UnitsOrderPanel.vue:500`）在 ENGAGE 分支內，火力任務區塊沒有任何武器選擇。

**操作員因此**：混編砲兵（例如同時有 81 迫與 155 榴）下火力任務時，系統一律用射程最遠的那門——想用迫砲省下榴彈砲的彈藥做不到。彈藥消耗與火力分配的決定權從 FSO 手上被拿走了，而畫面上不會說是哪一門在打。

**怎麼補**：FIRE_MISSION 區塊複用既有的 `loadWeapons()` 結果，過濾出曲射類武器做成下拉（預設「自動：射程最遠」），payload 條件帶 `weapon_id`；順帶顯示該武器的 `rounds_per_mission` 與剩彈（`liveAmmo` 已有）。

**優先度**：中偏後——不會卡死流程，但影響火力節約這類演練評分點。


### [S] 推演局『現在是不是暫停中』的權威狀態

*統裁與治理*

**後端**：core/app/sim_control.py 的 `session_pause_key`；core/app/api/control.py:122 `client.set(session_pause_key(session_id), "1")`、:124 RESUME 刪鍵。狀態確實存在 Redis，但 **沒有任何 GET 回傳它**：core/app/api/state.py:48 `StateSnapshotView` 只有 tick/last_seq/observer_faction/comms_posture/units/contacts/map_features/relations，沒有 paused。

**前端**：platform/app/pages/session/[id]/cop.vue:605 `sessionPaused` 是從事件流裡最後一則 SESSION_CONTROL 推導的，同檔 :600-603 註解自承「暫停前就離線的人重連後不會看到橫幅…要根治得由後端在 session 摘要／狀態快照帶出 `paused` 旗標」。白軍控制台更慘：platform/app/pages/session/[id]/white-cell.vue:363-365 註解「沒有任何端點讀得回暫停旗標」，表頭只有 SimClockBar（tick），整頁沒有任何暫停指示。

**操作員因此**：統裁按下暫停、重整一次控制台，就再也看不出這局到底停了沒有——只能盯著時鐘看它有沒有跳。剛加入或斷線重連的參謀更看不到暫停橫幅，會把『部隊不動』讀成系統壞了，然後打電話問統裁。

**怎麼補**：core/app/api/state.py 的 StateSnapshotView 加 `paused: bool`（由 Redis pause key 讀，Redis 掛掉退 false）→ 契約 → cop.vue 的 `sessionPaused` 改吃快照值（事件流保留為即時更新）、white-cell.vue 表頭加一顆暫停狀態燈。

**優先度**：最高——單日可完成，而且修的是統裁按了鈕卻不知道有沒有生效這種第一線的信任問題。


### [S] AI 陣營累計下了幾道令（含離失控保護上限還有多遠）

*統裁與治理*

**後端**：core/app/api/autonomy.py:130-133 回 `total_submitted`，同處註解明寫「累計送出令數。**與 last_submitted 一起帶**：後者只說『上一週期』，白軍要判斷『這個 AI 到底有沒有在動』看的是累計值」。core/app/ai_loop/worker.py:393 `if total_submitted > max_total_orders:` —— 這個值就是失控保護（SimParams.ai_max_orders，預設 500）的判定依據。

**前端**：`rg -n total_submitted core/ platform/` → platform 側**只命中 platform/app/types/api.ts:1492**（自動產生的型別）。platform/app/composables/useAiStatus.ts:82-101 的 `AiStatusDetail` 有 cycles / lastSubmitted / thinkingFor / countdown，就是沒有 total_submitted；autonomy.vue:271-274 的狀態列也沒有。

**操作員因此**：白軍盯著 AI 決策狀態，看得到「累計決策 137 次、上一次下達 3 道」，但看不到這個 AI 總共下了幾道令。當它悄悄撞到 500 道的失控上限被系統停掉時，畫面上只會變成『離線』，沒有任何跡象顯示是被上限擋的還是 LLM 掛了。

**怎麼補**：useAiStatus.ts 的 AiStatusDetail 加 `totalSubmitted`（後端已經在送，契約也已有欄位），autonomy.vue 狀態列與 cop.vue 的 AI 狀態列一起顯示；順便把 SimParams.ai_max_orders 帶進來算成「已用 137/500」，接近上限時變色。

**優先度**：高——純前端一日內可完成，而且補的是『AI 為什麼突然不動了』這個最常被誤判成當機的情境。


### [S] 演習排程（起訖日期、各階段預定時程）

*統裁與治理*

**後端**：core/app/exercise/schemas.py:14 `CreateExerciseRequest.schedule: dict[str, Any]`、:60 `ExerciseView.schedule`；服務層原樣存取。實測 `curl .../api/v1/exercises` 回 `"schedule":{}`。

**前端**：`rg -n 'schedule' platform/app/components/ExercisePanel.vue platform/app/composables/useExercises.ts` → 零命中。useExercises.ts:58 建立演習時 `body: { name }`，schedule 從來不送；ExercisePanel.vue 的卡片也不顯示它。

**操作員因此**：演習專案面板上看得到階段（PREP/EXEC/…）與整備勾稽，但看不到這場演習排在哪幾天、各階段預定何時完成。參謀排時程還是得另外開一份文件，然後兩邊對不起來。

**怎麼補**：約定 schedule 的最小結構（start_date / end_date / phase_targets）寫進契約 → ExercisePanel.vue 建立表單加起訖日期、卡片顯示排程與「距下一階段預定日還有幾天」→ useExercises.ts 的 createExercise 帶上 schedule（另需一個 PATCH 讓建立後可改）。

**優先度**：低——不影響推演正確性，但對『演習專案』這個定位來說是明顯的空欄位；可與稽核註記那條併一張卡做。


### [S] 階段推進的理由註記（寫進稽核軌跡）

*統裁與治理*

**後端**：core/app/exercise/schemas.py:20 `AdvancePhaseRequest.note: str | None`；core/app/exercise/service.py:178 `detail={"note": req.note} if req.note else {}` —— note 會進 ExerciseAuditLog 的 detail。

**前端**：platform/app/composables/useExercises.ts:65 `advancePhase(id, phase, note?)` 有這個參數、:68 也會送 `note`，但 platform/app/components/ExercisePanel.vue:328 唯一的呼叫點是 `advancePhase(ex.id, nextPhase(ex.phase))` —— **從不帶 note**。

**操作員因此**：稽核紀錄看得到『誰在幾點把演習推進到 EXEC』，看不到為什麼。實際上最需要留痕的正是例外情況（『整備會議 #3 由副指揮官代行，經核准提前進場』），現在只能事後靠記憶補述。

**怎麼補**：ExercisePanel.vue 的「推進階段」改成先跳一個小輸入框收理由（可留空）再送出；稽核清單（:412-422）把 `a.detail.note` 顯示出來。前端單檔改動，後端與契約都不用動。

**優先度**：低——半天的事，但它是治理留痕鏈上唯一還在斷的一節。


### [M] AI 呼叫稽核紀錄——每一次 LLM 決策的角色、adapter、prompt 雜湊、請求/回應、延遲、token 數、護欄裁決結果

*端點*

**後端**：資料表 core/app/models/tables.py:423-439 `AIInvocationLog`（role / adapter / promptHash / request / response / latencyMs / tokensIn / tokensOut / guardrailResult）。寫入端 ai/matso_ai/inference/invocation_log.py `InvocationLogWriter`，經 core/app/ai_loop/decider.py:258-275 `_make_role_manager` 接上（該檔 233 行註記 WP-F3「`audit=True`（預設）→ 每一次都落 AIInvocationLog」）。

**前端**：**後端連端點都沒有**：`curl /openapi.json` 列出的 67 條路徑無任何 invocation / ai-log 路徑；contracts/core_api.yaml 也沒有。前端只有一個孤零零的中文標籤 platform/app/composables/useLabels.ts:160 `AIInvocationLog: 'AI 呼叫紀錄'`，沒有任何頁面用它。附帶事實：DB 現況 `select count(*) from AIInvocationLog` = 0（0 筆），需先確認寫入路徑在活局中真的有觸發，再談曝露。

**操作員因此**：AI 打了一手爛棋，統裁想回頭問「它當時看到什麼、我方護欄有沒有攔、是哪個模型回的、花了幾秒」——這些後端全部存了，但畫面上一個字都看不到。演習後的爭議（「那是 AI 亂下的還是系統算的」）目前無法用系統本身佐證，只能翻 container log。

**怎麼補**：先跑一局 AI_FULL 確認 AIInvocationLog 真的有寫入（0 筆很可能代表 F3 接線在活執行期沒生效，那是另一張卡）。確認有資料後：contracts/core_api.yaml 加 `GET /sessions/{id}/ai-invocations`（分頁 + role/faction 過濾，限白軍/統裁，比照 core/app/api/msel.py 的 `_require_white_cell`）→ core/app/api/ 新增 handler → AAR 頁或白軍控制台加一個可展開的紀錄表（列出 role / 模型 / 延遲 / token / 護欄結果，點開看 request/response JSON）。

**優先度**：中等——先驗證有沒有資料再排，不然做出來是空表


### [M] 戰況帳本（Ledger）查詢——整局逐事件的完整紀錄，可過濾/搜尋/翻頁

*端點*

**後端**：`TacticalEventLog` 表現有 **211,479 筆**（`select count(*) from TacticalEventLog`），分佈：UNIT_MOVED 175602、SENSOR_CONTACT 35384、ENGAGEMENT_RESOLVED 230、TICK_OVERRUN 142、UNIT_ARRIVED 83、MOVE_ATTRITION 15、MOVE_ROUTE_PLANNED 11、MISSION_PHASE_CHANGED 8、AREA_FIRE_RESOLVED 5、SESSION_CONCLUDED 3、ROLLBACK 1、BDA_REPORT 1、FRATRICIDE 1、MISSION_ENDED 1。契約 contracts/core_api.yaml 早就宣告了 `GET /api/v1/sessions/{id}/ledger`，但**後端沒有實作**——活體驗證：`curl -o /dev/null -w '%{http_code}' .../sessions/e2e-orders/ledger` → **404**（core/tests/unit/test_contract_conformance.py 的 `_CONTRACT_ONLY` 也列了這條，註解自承「契約裡躺了很久，從來沒實作」）。

**前端**：前端**有型別**（platform/app/types/api.ts 由契約生成）卻沒有任何呼叫端，按下去也只會吃 404。實際能看到帳本的路徑只有兩條，且都是聚合過的：(a) 活局的 WS 事件流（platform/app/stores/sessionStream.ts，只有近期、不可回溯搜尋）；(b) AAR 的 `/aar/replay`（core/app/api/aar.py:91-105 只回每 tick 的 `event_types` 字串陣列與書籤，不回事件內容）。

**操作員因此**：統裁想查「第 412 tick 到底誤傷了誰、BDA 回報寫了什麼」——那一筆 FRATRICIDE、那一筆 BDA_REPORT 就躺在資料庫裡，畫面上卻沒有任何地方查得到單一事件的細節。演習後的爭議裁決只能請工程師去 DB 撈。

**怎麼補**：補實作而非刪契約：core/app/api/ 新增 ledger handler（分頁 + eventType/tick 區間/initiator 過濾，投影一律走 core/app/api/aar.py:65 的 `_visible_events` 同一條 fog 路徑，別另開第二套規則）→ 對齊 contracts/core_api.yaml 既有的 operation 形狀 → 前端在 AAR 頁加「帳本」分頁（虛擬捲動表格，21 萬筆不能一次撈）。

**優先度**：中等——資料量大、fog 投影要小心，但它是所有事後爭議的最終依據


### [M] 插件（terrain / weather / comms）的連線狀態、契約版本與能力清單

*端點*

**後端**：core/app/plugins/manifest.py 實作了 `GetManifest` 握手：檢查 `contract_version` 主版本（`CORE_CONTRACT_MAJOR`）與 `capabilities`（`TERRAIN_REQUIRED_CAPABILITIES = (GetElevation, CheckLos, GetPath, GetCellBatch)`），不相容就拋 `PluginHandshakeError`。DB 有 `PluginRegistry` 表（core/app/models/tables.py:454，現況 0 筆）。契約宣告了 `GET /api/v1/admin/plugins` 與 `POST /api/v1/admin/plugins/{name}/toggle`，但**後端未實作**（`/openapi.json` 無此路徑；test_contract_conformance.py 的 `_CONTRACT_ONLY` 列了這兩條）。

**前端**：前端有型別（由契約生成）但零呼叫端。畫面上唯一沾邊的是 platform/app/pages/system-settings.vue 顯示 `/system/config` 的 readonly 區塊——core/app/api/system.py:118-121 只給 `terrain_grpc_target` / `weather_grpc_target` 兩個**字串位址**，沒有連線狀態、沒有版本、沒有能力清單。

**操作員因此**：地形插件掛了或換成版本不合的，統裁在畫面上完全看不出來——只會發現「射界怪怪的」「路徑規劃很慢」，而那時錯的物理事實已經寫進帳本和 AAR 了。開演前的裝備檢查沒有任何畫面可以確認「三個插件都在、版本都對」。

**怎麼補**：決定實作或刪契約，別讓它繼續當殘骸。要做：core/app/api/ 新增 system 子端點（或按契約走 `/admin/plugins`）回每個插件的 target / 可達性 / contract_version / capabilities（呼叫 core/app/plugins/manifest.py 既有的握手，加短 deadline 與快取，別每次打 gRPC）→ platform/app/pages/system-settings.vue 的「系統資訊」區塊加三張插件狀態卡。toggle 建議直接從契約刪掉（air-gapped 環境沒有動態掛載插件的需求）。

**優先度**：偏後——開演前檢查表的價值，但目前三個插件都在 compose 裡固定起，實務上不常出事


### [M] 人工下令被預檢擋下（ORDER_REJECTED）與限制射擊區知情放行（ORDER_RESTRICTED_FIRE_OVERRIDE）——後端會落帳，帶下令者、失敗檢查項、落點座標

*事件回饋*

**後端**：core/app/orders/service.py:143-166 建 ORDER_REJECTED（`ai_decision` 含 `issuer_id`/`order_type`/`failed_checks`/`reason`/`target_lat`/`target_lng`）、:113-128 建 ORDER_RESTRICTED_FIRE_OVERRIDE，兩者都送進 `self._event_sink`。而 `event_sink` 在 core/app/api/deps.py:144 注入的是 `LedgerWriter(default_session_factory())` ——`core/app/state/ledger.py` 全檔沒有任何 redis/publish（`rg -n 'redis|publish' core/app/state/ledger.py` 零命中）。也就是說這兩種事件**只寫 DB，永遠不進 WS 串流**。同一個病在 core/app/api/c2.py:523-538、576-590：REQUEST_SUBMITTED / REQUEST_DECIDED 走 `_ledger()`（c2.py:542-549，同樣是 LedgerWriter），也不進串流。

**前端**：platform/app/composables/useCopFeed.ts:107-109 為 `ORDER_REJECTED`／`REQUEST_SUBMITTED`／`REQUEST_DECIDED` 都備好了中文（「指令被拒（預檢未過）」「提出申請」「申請已核覆」），:51 也備好了 `ORDER_RESTRICTED_FIRE_OVERRIDE`。四條翻譯在活推演中**一次都不會被渲染**。

**操作員因此**：同陣營的其他席位對「隊友剛剛下了一道令被系統擋掉」毫無感知——只有下令者本人的畫面跳預檢紅字。作戰官連下三次砲擊令都被禁射區擋下，指揮官在旁邊完全不知道，還在等那個火力。統裁更看不到（白軍控制台吃的是同一條串流）。

**怎麼補**：讓 `OrderService`／c2.py 的落帳同時推串流：最小改法是在 core/app/api/deps.py:139-146 包一層同時寫 Ledger 與 `stream.publish.publish_event` 的 sink（受眾用下令單位的陣營，比照 `broadcaster.event_audience`）；c2.py 的 `_ledger()`（:542）同樣處理。注意 REQUEST_* 已有席位受眾語義，沿用 `_push` 的 faction/seat 參數。

**優先度**：早做——它讓「四席位分工可追究」這個設計目標在**演習當下**成立，而不只在事後檢討成立。


### [M] 戰況事件的歷史補送——後端 Redis ring buffer 保留最近 5000 則事件供重連補齊

*事件回饋*

**後端**：core/app/state/broadcaster.py:27 `RING_CAPACITY = 5000`；core/app/api/ws.py:94-111 收 HELLO{last_seq} 後呼叫 `plan_resume`。但 core/app/stream/backfill.py:25-27：`if last_seq is None: return ResumePlan(resync=False, backfill_after_seq=None, ...)` ——**新客戶端一律不補送**。

**前端**：platform/app/stores/sessionStream.ts:93 `ws.send(JSON.stringify({ last_seq: lastSeq.value }))`，而首次連線 `lastSeq` 為 null（:117 註解自承失敗時退回 null＝當新 client 不 backfill）。platform/app/pages/session/[id]/cop.vue:586-588 再 `slice(-20)`。

**操作員因此**：參謀中途接手席位、或只是按了 F5，戰況事件欄就是空的一片「（尚無事件）」——過去半小時打了什麼全部歸零，要枯等下一則事件進來。伺服器上那 5000 則就在那裡，沒人去拿。同一個根因也讓 cop.vue:589-603 自己註解過的那個問題成立：**暫停橫幅在重整後消失**，於是重連的人看到時鐘不動、單位不動，分不出是被白軍暫停還是系統掛了。

**怎麼補**：（a）core/app/stream/backfill.py:25-27 讓 `last_seq is None` 也補送 ring 尾端 N 則（如 200），或在 HELLO 增加 `backfill: n` 參數（契約 contracts/ws_protocol.md 要同步改）。（b）暫停狀態不該靠補送事件推導——在 session 摘要／狀態快照回一個 `paused` 旗標（cop.vue:601-603 的註解已經指出這是正解），前端橫幅改讀它。

**優先度**：早做——「重整一下就什麼都沒了」是操作員最常撞到、也最容易被誤判成系統故障的一條。


### [M] 重大事件的主動提示（友軍誤傷、單位被摧毀、燃料耗盡）

*事件回饋*

**後端**：後端已把最該被看到的事件標好了：core/app/aar/replay.py:25-27 把 FRATRICIDE 列為書籤（註解寫「檢討會最該停下來看的一格」）；broadcaster.py:112-115 特地把 `cause`/`shooter_faction` 放進白名單，就是為了讓誤傷在 COP 上有內容。

**前端**：platform/app/pages/session/[id]/cop.vue 只有兩種橫幅：暫停（:589-615）與勝負（:617-）。誤傷、單位被摧毀、燃料耗盡一律只是 EventsPanel 裡的一行字，塞在 useCopWidgets.ts:57 定義的 300×148 小視窗中。cop.vue:159 雖有 `useToasts()`，但只餵給裝備管理（:553）與地圖編輯（:564），沒有任何事件會觸發 toast。

**操作員因此**：發生友軍誤傷——這是演習中最該立刻喊停的一件事——畫面上只是右側小視窗裡多一行「⚠ 友軍誤傷（交戰）第1連 → 第3連」，一秒後被下一則偵獲接觸推走。統裁很可能整場都不知道發生過。單位被打到 0% 也一樣沒有任何提示。

**怎麼補**：定義一份「必須打斷使用者」的事件型別集（FRATRICIDE、`target_health_after <= 0` 的 ENGAGEMENT_RESOLVED、MOVE_HALTED_FUEL、MISSION_ENDED、MSEL_PAUSE），在 cop.vue 監看串流時餵給既有的 `useToasts()`；誤傷再加一條與暫停橫幅同級的橫幅。

**優先度**：中等——但建議排在 SENSOR_CONTACT 洗版之後，否則 toast 也會被洗版。


### [M] 敵情接觸的位置誤差半徑——後端逐級算出「這個接觸的位置可能差多遠」（DETECTED 可到公里級、IDENTIFIED 縮到百公尺級），畫面上一個圓圈都沒畫

*欄位*

**後端**：core/app/intel/schemas.py:20 `ContactView.error_radius_m`；契約註解（platform/app/types/api.ts:1969）明寫「誤差半徑隨 fidelity 縮小」，且 WP-C5 敵情粗化時會把它放大到 h3 res-6 格尺度（約 3km）。curl `GET /api/v1/sessions/{id}/intel` 回應體帶此欄。

**前端**：只轉了型別、沒有任何渲染端。`platform/app/composables/useIntel.ts:49` 把它映成 `errorRadiusM`，`platform/app/composables/useUnits.ts:108` 宣告在 `Contact` 型別上——然後就沒有了。`rg -n errorRadiusM platform/app --glob '!types/api.ts'` 只回這兩處 + `useCopUnits.ts:32-35` 的假資料。地圖特徵產生器 `useUnits.ts:390-401`（`unitsToFeatures` 的 contact 迴圈）完全沒用到它，資訊卡也沒顯示。

**操作員因此**：情報官在圖上分不出「這是一個位置可能差三公里的模糊回報」和「這是已確認、誤差兩百公尺的目標」——兩者畫成一模一樣的一個符號。照著模糊接觸下 FIRE_MISSION 就是打空，而畫面沒有給過任何「這個點不可信」的提示。粗化（斷聯時全軍敵情位置被量化到 3km 格）在畫面上更是完全無感——只有 `comms_posture` 那行小字說「敵情圖粗化中」，圖上看起來卻精準如常。

**怎麼補**：`platform/app/composables/useUnits.ts` 的 `unitsToFeatures` 增一層 contact 誤差圓 GeoJSON（circle polygon，依 `errorRadiusM`），`platform/app/components/map/MapCanvas.vue` 加對應的 fill/line layer + `platform/app/components/map/LayerToggles.vue` 的圖層開關；`UnitDetailCard.vue`（或敵情 hover）順帶顯示「±N m」。

**優先度**：高——這是唯一能讓操作員看出「敵情有多不可信」的視覺線索，而斷聯粗化正靠它表達


### [M] 令的實際內容與下令者（`payload` / `issuer_id`）——FIRE_MISSION 打哪個座標幾發、ENGINEER 在破哪張障礙、MOVE 走哪條自訂路徑、工兵幾時完工、誰下的令

*欄位*

**後端**：core/app/orders/service.py:297-298 `issuer_id=order.issuer_id, payload=dict(payload)`——兩欄都已在回應體裡（實測 GET /orders 回應含 `"issuer_id": "af8…"` 與完整 payload）。payload 裡還藏著引擎寫回去的作業狀態：core/app/engine/obstacle_wiring.py:207-215 的 `_work_until_tick`（工兵完工 tick）、core/app/engine/mission_wiring.py:126 的 `_mission_state`（任務階段機）。

**前端**：零消費端。`rg -n issuer_id platform/app --glob '!types/api.ts'` → 無結果；`rg -n '\.payload' platform/app` 的命中全是 WS envelope 與注入表單，沒有一處是 OrderResponse。`platform/app/composables/useCopFeed.ts:191-199` 的註解甚至還寫著「`OrderResponse` …**不回令載荷**。所以 FIRE_MISSION 的落點座標、ENGINEER 的作業…」——那段註解已經過期，但它描述的畫面現況仍然成立。指令列 `OrdersPanel.vue:70-95` 只印 `orderTargetLabel(o)`（useCopFeed.ts:205-208：只看 target_unit_id / target_h3，兩者對 FIRE_MISSION 皆為 null）。

**操作員因此**：（釐清：後端補欄那一半已經做完，這裡列的是**沒有人去畫**的那一半。）指令列上一道火力任務顯示成「砲兵營 · 火力任務」就沒了——打哪裡、幾發、用哪張火協單，一律看不到；破障令只顯示「執行中」，工兵還要挖 45 分鐘這件事後端算得出來卻不告訴任何人；四席位演習裡「這道逆襲令是作戰官還是指揮官下的」在畫面上依然無解。

**怎麼補**：`platform/app/composables/useCopFeed.ts` 的 `orderTargetLabel` 依 order_type 讀 payload（FIRE_MISSION → 座標+rounds、ENGINEER → action+obstacle_type、MOVE → waypoints 數）；`OrdersPanel.vue` 每列加下令者（`issuer_id` → 需 `useParticipants` 對映成人名）與工兵 ETA（`payload._work_until_tick` 減當前 tick）；順手把 useCopFeed.ts:191 那段過期註解改掉。

**優先度**：高——AAR 與現場都靠它，而且資料早就在回應裡，純前端工


### [M] 任務時間軸（每道 MISSION 令走過哪些階段、各階段花了多久、評估失敗幾次）

*欄位*

**後端**：端點實作齊全：core/app/api/aar.py:230-243 `GET /{session_id}/aar/missions`，回 core/app/aar/missions.py:55-72 `MissionTimeline.to_dict()`——`order_id` / `mission_type` / `unit_id` / `failed` / `errors` / `legs[{phase, from_tick, to_tick, duration_ticks, note}]`。實測 `curl .../aar/missions` 回 200 `[]`（端點活著）。

**前端**：整支端點沒有任何呼叫端。`platform/app/composables/useAar.ts:154-169` 只包了 `aar/replay`、`aar/replay/states`、`aar/stats`、`aar/report`、`aar/export` 五支，沒有 missions。`rg -n 'duration_ticks|from_tick|legs' platform/app --glob '!types/api.ts'` 在 AAR 情境下零命中。順帶一提這支端點**不在 contracts/core_api.yaml 裡**（`rg 'aar/missions' contracts/core_api.yaml` 無結果），所以連型別都生不出來。

**操作員因此**：任務級下令是 A2 那張大卡的主打功能，但檢討會上完全沒有素材：「這道攻擊任務從計畫到接敵拖了多久」「部隊卡在哪一個階段」「分解器評估失敗過幾次」——後端全部算好了，AAR 頁面上一個字都沒有。統裁只能回去翻原始事件流。

**怎麼補**：先補契約（contracts/core_api.yaml 加 `/sessions/{id}/aar/missions` + `MissionTimeline`/`MissionLeg` schema，重生 types/api.ts），再在 `platform/app/composables/useAar.ts` 加 `aarMissions()`，`platform/app/pages/session/[id]/aar.vue` 加一個甘特式的階段條區塊（可沿用該頁既有的 tick 軸）。

**優先度**：中——不影響推演進行，但少了它，任務級下令這個功能在檢討環節等於不存在


### [M] 曲射交戰掛火協核准單——對「敵單位」下交戰令時附上已核准的 FIRE_SUPPORT 申請

*下令*

**後端**：`core/app/orders/schemas.py:69-70`（`EngagePayload.fire_request_id`）；`core/app/orders/precheck.py:231-293` `_precheck_fire_approval` 對 **ENGAGE 與 FIRE_MISSION 同等適用**——想定開了 `indirect_fire_requires_approval` 時，指名曲射武器或「未指名武器但單位持有任何曲射武器」都要求核准單，否則回 `fire_approval` 失敗。契約 `core_api.yaml:1344` 的 ENGAGE 描述只有 `{target_unit_id,weapon_id?,ammo_type?,fire_policy?}`——**連契約都沒宣告這一欄**（`PROGRESS.md:298` 已記過這條漂移）。

**前端**：`useCopOrdering.ts:428-434`（ENGAGE 的 fall-through payload）只送 `target_unit_id / weapon_id / ammo_type / fire_policy`；`fire_request_id` 只在 FIRE_MISSION 分支帶（同檔 :425）。`approvedFireRequests` 下拉也只渲染在 FIRE_MISSION 區塊（`UnitsOrderPanel.vue:310-323`）。

**操作員因此**：開了火協管制的演習裡，FSO 對著看得見的敵人下交戰令會被系統打回「本局曲射火力需火協核准」，而畫面上沒有任何地方能掛那張核准單——他手上明明有一張已核准的申請。唯一的繞法是改打座標（火力任務）或指名一把直射武器，等於管制一開就把「用迫砲直接接戰」這個動作從人類席位拿掉。

**怎麼補**：契約 `OrderRequest.payload` 的 ENGAGE 描述補 `fire_request_id?`（先行）→ 重生 `types/api.ts` → `useCopOrdering.ts` 把 `loadFireRequests()` 的觸發條件從「切到 FIRE_MISSION」擴到「切到 ENGAGE」，ENGAGE payload 條件帶 `fire_request_id`；`UnitsOrderPanel.vue` 的 ENGAGE 區塊複用同一個核准單下拉（建議只在該單位持有曲射武器時顯示）。

**優先度**：早——這是「後端會擋、前端無解」的死路，只要有一局開了火協開關就會當場卡住 FSO。


### [M] 火力任務時效（ttl_ticks）——發令後幾個 tick 內仍有效，逾時作廢

*下令*

**後端**：`core/app/orders/schemas.py:85-88`（`ttl_ticks`，1–100000，省略＝永不過期）；`core/app/engine/fire_wiring.py:115` `ttl_ticks: int = 0`、:174 `ttl_ticks=ttl_of(payload)`、:248-251 逾時 → `_reject(..., "EXPIRED", f"逾時作廢（時效 {order.ttl_ticks} tick，已過 {late} tick）")`；`core/app/orders/ttl.py` 整支。`rg -n "ttl_ticks" contracts/core_api.yaml` **零命中——契約完全沒宣告這一欄**，所以 `types/api.ts` 也沒有它。

**前端**：`useCopOrdering.ts:420-427` 沒有 ttl 相關 ref、payload 不帶；`UnitsOrderPanel.vue` 火力任務區塊沒有時效輸入。全 repo 前端 `rg -n "ttl_ticks" platform/app` 零命中。

**操作員因此**：通信中斷時火力任務會留在待執行；等射手恢復通聯，這發彈仍會打到幾十個 tick 前的戰場——那裡可能已經是我軍。真實作業裡這種任務是作廢、由火協重新指派，但下令者沒有任何欄位可以宣告「這道任務 10 分鐘內有效」。

**怎麼補**：契約 `OrderRequest.payload` 的 FIRE_MISSION 描述補 `ttl_ticks?` → 重生型別 → 面板加「時效（tick，留空＝不過期）」數字輸入，payload 條件帶入。順手在指令列把 EXPIRED 的拒絕原因顯示出來。

**優先度**：中——通信降級的局才咬人，但契約缺欄本身就該補（前端連型別都拿不到）。


### [M] 依席位（seat_role）決定下令面板顯示哪些令型

*下令*

**後端**：`core/app/seats/__init__.py:25-49` 是席位→可下令型的唯一權威表（S3_OPS 五種、FSO_FIRES 兩種、S2_INTEL/OBSERVER 空集合＝不能下任何令）；`core/app/orders/validator.py:34,147` 消費它並拋 `OrderSeatDeniedError`。

**前端**：`cop.vue:148` 有 `mySeatRole`、:369 從 `/participants/me` 取回，但 `rg -n "mySeatRole" cop.vue` 顯示它**只被傳給 C2Panel**（:894）。`UnitsOrderPanel.vue:154-162` 的令型下拉是寫死的七個 `<option>`，不接受任何席位參數；`canSubmit`（:71-90）也不看席位。

**操作員因此**：情報官與觀察員坐下來看到完整的七種令型下拉，選了、標定了、按送出，才被系統回一句權限不足；火力支援協調官看得到「移動」「任務」但一律下不了。參謀不知道自己的席位到底能做什麼，只能靠試錯——而每一次試錯都會在指令列留下一筆被拒紀錄。

**怎麼補**：契約 `/participants/me` 已回 `my_seat_role`（`core_api.yaml:200`）→ 前端加一份與 `app/seats` 對齊的席位→令型對照（或更好：後端新增一個 `allowed_order_types` 欄位隨 me 一起回，避免兩邊各寫一份會漂移），`UnitsOrderPanel` 依此過濾 `<option>`，全空時直接顯示「本席位為唯讀」。

**優先度**：中——不修不會壞，但四席位演習的分工體驗全靠它，而且是「兩邊各寫一份」風險最低的時候（表還小）。


### [M] 想定的交戰規則（ROE）：各陣營的預設火力政策 + 禁用武器清單

*想定與編裝*

**後端**：契約 `contracts/roe.schema.json`（default_fire_policy：FREE/SMALL_ARMS_ONLY/ANTI_ARMOR_HOLD；weapon_restrictions：forbid_categories/forbid_templates/reason）。存檔端點收得下：`core/app/api/scenarios.py:47` `roe: dict[str, Any] | None`。載入器解析：`core/app/scenario/loader.py:229` `_roe_from_dict`、`:385-397` 含陣營存在性驗證。隨局落地：`loader.py:526` `roe=loaded.roe or None` → WargameSession.roe。執行期真的生效：`core/app/orders/roe.py:91 parse_roe` / `:127 load_session_roe`，`core/app/engine/fire_wiring.py:227 _roe_banned`（被禁武器逐武器篩掉記 HELD/ROE），下令端拒 `ORDER_ROE_VIOLATION`。四個出貨想定全都有 roe.yaml（`scenarios/examples/*/roe.yaml`）。

**前端**：`rg -n 'roe|fire_policy|weapon_restriction' platform/app/ --glob '!types/api.ts'` → 劇本編輯器零命中；命中的只有 AAR 文案、COP 下令面板的**單次**令面 fire_policy（`useCopOrdering.ts:433`）。`useScenarioEditor.ts:217-221` exportScenario 的回傳型別只有 `{scenario, orbat, msel}` 三段，根本沒有 roe 的出口。

**操作員因此**：統裁沒辦法在劇本裡宣告「這一場不准用飛彈」「紅軍只准輕兵器」。要設交戰規則只能請人手寫 roe.yaml 丟進 package 目錄，白軍在畫面上完全看不到這一場有沒有武器管制、管了什麼、理由是什麼——而 AAR 檢討時「為什麼那門砲一直沒開火」的答案就藏在這裡。

**怎麼補**：`platform/app/pages/scenario-editor.vue` 加一個「交戰規則」section：陣營 × 預設火力政策的下拉表 + 禁用武器清單（陣營／類別多選／範本名多選／理由必填）。`platform/app/composables/useScenarioEditor.ts` 的 ScenarioModel 加 `roe`，exportScenario 回傳第四段、importScenario 讀 `bundle.roe`。`platform/tests/scenario-editor.test.ts` 補 roundtrip 斷言。

**優先度**：高——它是護欄 G4 的另一半，而且是唯一「後端已完全接線、前端一個入口都沒有」的安全機制。


### [M] 想定級地形通行覆寫（overrides/mobility_matrix.json：這一場的地形×機動類別速度調整）

*想定與編裝*

**後端**：契約 `contracts/mobility_matrix.schema.json` + 預設值 `contracts/mobility_matrix.json`。載入器：`core/app/scenario/loader.py:230 _mobility_from_dict`（bundle 路徑）、`:477` （package 目錄路徑，檔名常數 `MOBILITY_OVERRIDE_FILE = 'mobility_matrix.json'`），**局部覆寫深合併於預設**。隨局落地 `loader.py:528` `mobility_overrides=loaded.mobility_overrides or None`，runner 與移動預覽端共用。四份出貨想定都有 `scenarios/examples/*/overrides/mobility_matrix.json`。

**前端**：`rg -n 'mobility_matrix|mobilityMatrix' platform/app/ platform/tests platform/e2e` → 只有 `types/api.ts:2194` 這一個自動產生的型別，沒有任何使用端。編輯器沒有 overrides section，exportScenario 也沒有 overrides 出口。

**操作員因此**：「這一場雨後泥濘，履帶車越野速度打七折」這種想定設定，統裁在畫面上調不了，也看不出這一場到底套了什麼調整。開局後部隊走得比預期慢，沒有人查得出是想定覆寫還是引擎預設。

**怎麼補**：編輯器加「地形通行覆寫」section：以 `contracts/mobility_matrix.json` 的預設值當底，畫成地形×機動類別的表格，只把**被改過的格**寫進 overrides（維持 loader 的局部覆寫語義）。ScenarioModel 加 `mobilityOverrides`，export 產出 bundle 的 `overrides.mobility_matrix`。

**優先度**：中——影響的是保真度不是安全，但它和 ROE 走的是同一條「bundle 兄弟鍵」路徑，兩張卡一起做可以共用 export/import 的改動。


### [M] 系統健康監控：推演引擎跑得順不順、幾局在跑、幾個 AI worker、tick 有沒有超時

*統裁與治理*

**後端**：core/app/main.py:130 `@app.get("/metrics", include_in_schema=False)`；core/app/metrics.py:160-194 定義 8 個指標（matso_tick_duration_ms / matso_tick_overrun_total / matso_tick_total / matso_ws_fanout_total / matso_llm_latency_ms / matso_guardrail_blocked_total / matso_io_latency_ms / matso_active_sessions / matso_ai_workers）。實測 `curl -s http://localhost:8000/metrics` 回真實數字：matso_tick_total 343506、matso_active_sessions 4、matso_tick_overrun_total 5、matso_ai_workers 0。

**前端**：`rg metrics platform/app/ --glob '!types/api.ts'` → 零命中。`rg -n 'prometheus|grafana|9090' ops/compose/docker-compose.yml` → 零命中；compose 服務只有 mariadb/redis/qdrant/terrain/weather/comms/core/frontend/tileserver。也就是說這個端點在整個系統裡沒有任何消費端。

**操作員因此**：演習跑到一半畫面不動，統裁分不出是『白軍按了暫停』、『引擎在追不上進度』還是『後端掛了』——只能盯著地圖上的圖標猜。也沒有任何地方看得出這台機器上同時有幾局在跑、AI 決策程序起來了沒有。

**怎麼補**：新增 `/api/v1/system/health`（把 metrics.REGISTRY 的關鍵值整理成 JSON，走既有 is_omniscient 閘門）→ 契約 contracts/core_api.yaml → 在 platform/app/pages/system-settings.vue 加一個「執行狀態」區塊（執行中局數、AI worker 數、近期 tick 超時次數、平均 tick 時長、護欄攔截累計）。要做完整儀表板才另外掛 Prometheus/Grafana。

**優先度**：高——這是唯一能讓操作員自己判斷『系統還活著嗎』的東西，而目前完全沒有出口。


### [L] 交戰裁決係數——每一次交戰後端都算出命中率、擲骰值、地形遮蔽、天候、壓制、目標姿態的乘數

*事件回饋*

**後端**：活資料 curl（session a3126ca2，commander/exercise）：`GET /api/v1/sessions/{id}/aar/export?fmt=json` 的 ENGAGEMENT_RESOLVED 帶 `ai_decision = {status, p_hit: 0.5798…, roll: 0.4873…, hit, coefficients: {base_ph: 0.7505, terrain_cover: 0.7726, weather: 1.0, suppression: 1.0, target_posture: 1.0, cp_per_platform, strength_loss, strength_after}, target_health_after, target_strength_after}`。

**前端**：`rg -n 'p_hit|coefficients|terrain_cover|base_ph' platform/app --glob '!types/api.ts'` → 零命中。broadcaster.py:95-118 的白名單也不含 `p_hit`/`roll`/`coefficients`，所以連 WS 都下不來。唯一取得途徑是 AAR 頁的「匯出 JSON」按鈕（platform/app/pages/session/[id]/aar.vue:277）下載檔案自己開。

**操作員因此**：檢討會最常問的那句「為什麼這一發沒中」答不出來。指揮官在畫面上只看到「交戰未命中 第1連 → 敵戰車排」，看不到是天候壓下來、目標在掩體裡、還是自己被壓制。統裁想解釋「地形遮蔽讓命中率從 75% 掉到 58%」——這個數字系統每一發都算，但只有下載 JSON 檔用文字編輯器翻才看得到。

**怎麼補**：（a）契約新增逐事件查詢端點（如 `GET /sessions/{id}/aar/events?seq=`），回 `ai_decision` 全文；或在 `core/app/state/broadcaster.py` 白名單加 `p_hit`/`coefficients`（**只給射方陣營**，敵方不得知道我方的命中率計算）。（b）前端在 `EventsPanel` 事件列加「展開」，或在 AAR 書籤點下去時顯示一張係數表（base_ph → 各乘數 → p_hit → roll → 結果）。

**優先度**：第二順位——它是這套系統「可解釋性」的賣點，缺了它兵推的教學價值折損最大，但工作量也最大。


### [L] ORBAT 每個單位的編裝（equipment：帶哪些武器範本、幾件、初始彈藥）

*想定與編裝*

**後端**：`contracts/orbat.schema.json:106-133`：`equipment[] = {template, quantity, ammo}`，未知範本名於開局報錯並指出精確路徑。載入器建 EquipmentInstance（`core/app/scenario/loader.py:574` 一帶）。四份出貨想定的 orbat 全部大量使用（`rg -n 'equipment' scenarios/examples/*/orbat/*.yaml`，光 joint-defense/red.yaml 就 10 處）。

**前端**：`rg -n 'equipment' platform/app/pages/scenario-editor.vue platform/app/composables/useScenarioEditor.ts` → **零命中**。ORBAT TreeTable（`scenario-editor.vue:751-853`）的欄位是番號/編制/上級/兵科/固定/座標，沒有編裝欄。唯一的編裝 UI 是**開局後**的 COP 編裝管理面板（`platform/app/components/cop/EquipManagerPanel.vue` + `/sessions/{id}/units/{uid}/equipment`），它改的是那一局的實例，不回寫想定。

**操作員因此**：參謀在劇本編輯器裡編得出一支「裝甲營」，但編不出它帶什麼——番號、編制、兵科、位置全設好了，火力是空的（或落回開局旗標的預設配發）。要編出一份真的能打的想定，還是得請工程師手寫 orbat yaml。或者每開一局就到 COP 用編裝管理面板逐單位重配一次，下一局再配一次。

**怎麼補**：`scenario-editor.vue` 的 ORBAT TreeTable 加「編裝」展開列：以 `useEquipment.fetchEquipmentTemplates()` 拉範本清單做下拉（範本名 = orbat 的 `template`），每列 quantity / ammo。`useScenarioEditor.ts` 的 EditorUnit 加 `equipment`，export/import 對應。注意先做 roundtrip 保真那一項，否則做 UI 的同時舊資料還在掉。

**優先度**：高，但排在 roundtrip 保真之後——沒有編裝的想定等於沒有火力，這是「劇本編輯器能不能獨立產出一份可用想定」的分水嶺。


### [L] AI 呼叫稽核：某道 AI 下的令當時用了什麼提示詞、引用了什麼、延遲多久、當時是哪個 AI 模式

*統裁與治理*

**後端**：core/app/models/tables.py:424 `class AIInvocationLog`（__tablename__ "AIInvocationLog"）；ai/matso_ai/inference/invocation_log.py:32 落地寫入；ai/matso_ai/inference/role_manager.py:76 記錄當時 mode；core/app/ai_loop/decider.py:233 註解「`audit=True`（預設）→ 呼叫走 RoleManager，每一次都落 AIInvocationLog」。

**前端**：`rg AIInvocationLog core/app/api/` → 只命中 system.py 的兩行註解，**沒有任何端點讀這張表**。`rg -n 'invocation|guardrail' platform/app/ --glob '!types/api.ts'` → 只有 aar.vue:135 顯示 `stats.guardrail_blocks` 一個總數。

**操作員因此**：AI 陣營下了一道明顯不合理的令（例如把主力調去空地），講評時想回頭看它當時看到什麼、為什麼這樣判斷——查不到。目前唯一能講的只有『行動後檢討裡護欄攔截了 N 次』這個數字，說不出被攔的是哪一道、為什麼。

**怎麼補**：新增 `GET /api/v1/sessions/{id}/ai-invocations`（分頁、限 is_omniscient、預設不回完整 prompt 只回摘要+延遲+模式+引用數，另有 `/{invocation_id}` 取全文）→ 契約 → 在 platform/app/pages/session/[id]/autonomy.vue 或 aar.vue 加「AI 決策軌跡」清單，可展開看單次呼叫。

**優先度**：中高——SPEC_FULL §9.1 要求記錄就是為了可追溯，記了卻沒人看得到等於白記；但要等監控/暫停那兩條先補完基本可觀測性。


---

## 只覆蓋一部分（15）


### [S] C2 信文的正式文別——回報（REPORT）/ 申請（REQUEST）/ 核覆（APPROVAL），以及信文掛回申請單（ref_id）

*端點*

**後端**：core/app/api/c2.py:74-80 `SendMessageRequest` 收 `kind: MessageKind = FREE_TEXT` 與 `ref_id: str | None`；`MessageKind` 四值定義於 core/app/models/enums.py:104-110（FREE_TEXT / REQUEST / APPROVAL / REPORT）。送出後 kind 會落 DB 並隨 `C2_MESSAGE` 事件推播（core/app/api/c2.py:360-368）。

**前端**：送信路徑 platform/app/components/cop/C2Panel.vue:158-169 `doSend()` 只傳 `toSeat` / `toFaction`，**從不傳 kind 或 refId**——雖然 composable 支援（platform/app/composables/useC2.ts:55-76 有 `kind?` 與 `refId?` 參數，預設 `'FREE_TEXT'`）。收信端反而顯示得出四種文別（C2Panel.vue:260 `MESSAGE_KIND_LABELS[m.kind]`）。結果：畫面上永遠只會出現「一般信文」。

**操作員因此**：參謀想正式「回報」戰況、或用一封信「核覆」下級的申請，系統只送得出閒聊式的一般信文。指參程序磨練的重點就是文別分明的異步審批鏈，現在信文匣裡全部長一樣，AAR 也重建不出「誰在第幾分鐘正式回報了什麼」。

**怎麼補**：純前端：platform/app/components/cop/C2Panel.vue 的送信區加一個文別下拉（用既有的 `MESSAGE_KIND_LABELS`），`doSend()` 帶上 `kind`；在申請單卡片上加「回覆此申請」按鈕，帶 `refId: r.id` 送出 REQUEST/APPROVAL 類信文。後端與契約都不用動。

**優先度**：早做——零後端成本，直接補上一個已經付過錢的功能


### [S] AAR 匯出（JSON/CSV）——行動後檢討的原始資料

*事件回饋*

**後端**：core/app/aar/export.py:30-48 的 `_row()` 只組 `seq/tick/event_type/initiator_id/target_id/damage_calc` (+ 非匿名時的 `ai_decision`/`reasoning_chain`)，**沒有 `detail`**。而 core/app/aar/events.py:29-30 的 `AarEvent` 明明有 `detail` 欄且已從 DB 讀出來（events.py:43）。活體驗證：匯出 185 筆事件的鍵集合＝`['ai_decision','damage_calc','event_type','initiator_id','reasoning_chain','seq','target_id','tick']`，無 detail。

**前端**：platform/app/pages/session/[id]/aar.vue:277-279 三顆匯出鈕（JSON／CSV／CSV 匿名化）都走同一條路。前端沒有任何地方顯示 detail。

**操作員因此**：與上一項合起來看才知道嚴重性：`detail` 是**完全無路可達**的——即時看不到、AAR 頁看不到、連匯出檔都沒有。分析官拿到匯出檔想做「油料耗盡分佈」「哪些地形格擋住了機動」，資料在資料庫裡躺著，但沒有任何操作員能取得的管道。

**怎麼補**：core/app/aar/export.py:36-48 在非匿名分支加 `row['detail'] = e.detail`（匿名化維持省略，detail 含座標）；CSV 的 `_CSV_FIELDS` 同步加欄。

**優先度**：很早做——單檔一行的改動，卻讓一整類已經在資料庫裡的資料變成可用。


### [S] AAR 儀表板顯示事件型別（統計分布 + 重播時間軸「本 tick 事件」）

*事件回饋*

**後端**：core/app/api/aar.py:202-225 `/aar/stats` 回 `event_counts`（型別→次數）；:91-105 `/aar/replay` 回 `frames[].event_types`。活體驗證（session adda6b01）：`event_counts = {"AREA_FIRE_RESOLVED":4, "SENSOR_CONTACT":35347, "BDA_REPORT":1, "FRATRICIDE":1, "TICK_OVERRUN":17}`——鍵就是英文代號。

**前端**：platform/app/pages/session/[id]/aar.vue:143 `<li v-for="(n, t) in stats.event_counts">{{ t }}：{{ n }}</li>`、:180 `本 tick 事件：{{ tickEvents.join('、') }}` ——兩處都**直接印代號**。aar.vue:1-19 的 import 清單沒有 `useCopFeed`，`EVENT_LABELS` 那 52 條中文一條都沒用上。platform/tests/ui-wording.test.ts 的裸代號掃描只看樣板文字節點與含中文的字面量，`{{ t }}` 這種變數插值抓不到，所以測試是綠的。

**操作員因此**：檢討會上打開 AAR，事件分布欄寫的是「SENSOR_CONTACT：35347／FRATRICIDE：1」。統裁要當著長官的面把 FRATRICIDE 翻成「友軍誤傷」。而同一份中文表在 COP 上明明用得好好的。

**怎麼補**：aar.vue 匯入 `EVENT_LABELS`（或新開一個 `eventTypeLabel()` 放進 `useLabels.ts`，比照 `precheckLabel` 的查無原樣回傳紀律），:143 與 :180 兩處套上。順手在 platform/tests/ui-wording.test.ts 補一條「event_counts 的鍵不得直接插值」。

**優先度**：很早做——半天的事，而 AAR 是給長官看的那個畫面。


### [S] 重播書籤——後端替關鍵事件建書籤讓檢討時一鍵跳轉

*事件回饋*

**後端**：core/app/aar/replay.py:16-28 `BOOKMARK_TYPES` 含 `REINFORCEMENT` 與 `FORCE_COLLAPSE`。但 `rg -n 'REINFORCEMENT|FORCE_COLLAPSE' core/app` 只命中 replay.py 這兩行本身——**沒有任何地方發出這兩種事件**（`core/tests/unit/test_frontend_event_labels.py` 掃出的後端實際型別共 47 種，不含它們；EVENT_LABELS 也沒有，且該檔的 `test_label_table_has_no_entries_for_events_nobody_emits` 保證兩邊一致）。實際的增援事件叫 `MSEL_UNITS_SPAWNED`（core/app/scenario/msel_actions.py）。

**前端**：platform/app/pages/session/[id]/aar.vue:226-231 忠實渲染後端給的書籤清單——後端沒給，畫面就沒有。

**操作員因此**：檢討時想跳到「增援投入的那一刻」，書籤欄裡找不到。統裁只能手動拖時間軸猜。書籤功能看起來是好的（交戰、誤傷、回滾都跳得到），缺的正是白軍自己注入的那些轉折點。

**怎麼補**：core/app/aar/replay.py:16-28 把 `REINFORCEMENT`/`FORCE_COLLAPSE` 換成實際發得出來的型別（`MSEL_UNITS_SPAWNED`、`MSEL_PAUSE`、`SESSION_CONCLUDED`、`MOVE_HALTED_FUEL` 等）；加一條測試斷言 `BOOKMARK_TYPES ⊆ backend_event_types()`（該工具函式已存在於 core/tests/unit/test_frontend_event_labels.py:backend_event_types）。

**優先度**：順手做——測試工具現成，改的是一個 frozenset。


### [S] 申請核覆的通知——參謀提申請、指揮官核覆，兩端各自要收到對的提示

*事件回饋*

**後端**：core/app/api/c2.py:522 提出申請時 `_push(..., "C2_REQUEST", ..., seat="COMMANDER")` 正確。但 :576 **核覆完成時又推了一次同樣的 `C2_REQUEST` 給 COMMANDER 席位**（同一行程式碼複製過去，連註解「申請送到核覆者席位」都一字未改）。真正代表核覆結果的 `REQUEST_DECIDED`（:580-590）走的是 `_ledger()`，只寫帳本不進串流。

**前端**：useCopFeed.ts:102 `C2_REQUEST: '收到申請案，待核覆'`；:109 `REQUEST_DECIDED: '申請已核覆'`——後者永遠不會出現。

**操作員因此**：指揮官批准了一張火力申請，畫面上立刻又跳一則「收到申請案，待核覆」——他會以為又來一張新的，回去 C2 面板找卻找不到。而**提出申請的那個參謀完全收不到核覆結果的通知**，只能自己回面板重整看狀態。核覆流程的回饋是反的。

**怎麼補**：core/app/api/c2.py:576 改推一則指向申請者席位／陣營的核覆通知（新 event_type，或把 `REQUEST_DECIDED` 從 `_ledger()` 改成同時 publish），受眾用 `r.requested_seat`。前端 useCopFeed.ts 為它補上帶結果（核准／駁回）的敘述。

**優先度**：早做——這是四席位 CPX 每天都會走幾十次的流程，而現在它的回饋是錯的。


### [S] MISSION 令走到哪個階段（PLANNED / MOVING / ENGAGING / …）

*欄位*

**後端**：契約 contracts/core_api.yaml:1408 宣告了 `OrderResponse.mission_phase`，但 core/app/orders/schemas.py:162-181 的 `OrderResponse` **根本沒有這個欄位**（實作端不填）。真正的階段值在 `payload._mission_state.phase`（core/app/engine/mission_wiring.py:63,126 寫入），而 payload 已經隨回應下發。

**前端**：前端 UI 早就寫好在等，但等的是錯的欄位。`platform/app/components/cop/OrdersPanel.vue:57-66` 的 `phaseLabel()` 讀 `o.mission_phase`，函式上方的註解自己承認「所以在後端補上之前，這裡恆為空、什麼都不顯示」。樣板 `OrdersPanel.vue:79-81` 的 `.ord-phase` 標籤因此永遠不渲染。

**操作員因此**：下了任務級令之後，指令列上只有一個「執行中」；部隊到底還在行軍、已經接敵、還是在整補，指揮官看不出來——而任務級下令的整個賣點就是「我不管低階令，我看階段」。

**怎麼補**：兩條路二選一：(a) 後端 `_to_response` 從 `payload._mission_state.phase` 補出 `mission_phase`（契約已宣告，不必改契約，最小改動）；(b) 純前端讓 `phaseLabel()` 改讀 `o.payload?._mission_state?.phase`。建議 (a)——契約已經承諾了這個欄位，讓實作端追上比讓前端去挖底線欄位乾淨。

**優先度**：中高——S 級成本就能點亮一段已經寫好的 UI，而且是 A2 任務級下令這張大卡的門面


### [S] 任務級下令（MISSION）的四種任務型與幾何標定

*下令*

**後端**：`core/app/orders/mission.py:26-30` MissionType 四值（SEIZE/DEFEND/SCREEN/MOVE_MARCH），:53-79 四組 params 模型；`core/app/orders/validator.py:57` 在 submit 就依型別驗參數。

**前端**：四種任務型全在（`UnitsOrderPanel.vue:60-65,330-334`），幾何標定完整——SEIZE 先收 objective 再連點 axis、SCREEN/MOVE_MARCH 連點路線（`cop.vue:434-443`），半徑輸入 :368-379。**缺的是三個純數值參數**：`orientation_deg`（DEFEND）、`spacing_km`（MOVE_MARCH），以及路線點沒有「退一點」（MOVE 有 `undoWaypoint`，MISSION 只有整批清除 :348-354）。

**操作員因此**：任務型與「打哪裡」都下得出來，這一塊的骨架是好的；下不出來的是「怎麼做」的細部——面向哪個方向守、行軍拉多開。路線點多點一下只能整條重畫也很惱人（軸線常要 5–6 點）。

**怎麼補**：與前述 orientation_deg / spacing_km 兩項合併成一張卡；順手把 `undoWaypoint` 的同款按鈕加到 MISSION 幾何列。

**優先度**：中——骨架已在，補的是三個輸入框，一張卡能全部收掉。


### [S] 想定 roundtrip 保真：用編輯器開既有想定再存回去，不該掉東西

*想定與編裝*

**後端**：`GET /scenarios/{id}` 回的是**原封不動的整包 bundle**（`core/app/api/scenarios.py:107-116`：`json.loads(row.package_blob)`），所以 roe / overrides / 每單位 equipment 都在回應裡。`POST /scenarios` 也收得下這三者（`scenarios.py:45-49`）。後端自己的 dump 端已經因為這個病被修過三次（`core/app/scenario/dump.py:56-60` 的註解：「本函式是**手寫白名單**——沒列進來的鍵，匯出再匯入就會靜靜消失」）。

**前端**：`scenario-editor.vue:191` 用 `apiFetch<Parameters<typeof importScenario>[0]>('/scenarios/{id}')` 載入，而 `useScenarioEditor.ts:335-339` importScenario 的參數型別只有 `{scenario, orbat?, msel?}`——`roe` 與 `overrides` 連讀都沒讀。單位層同樣：`useScenarioEditor.ts:344-354` 只取 designation/unit_level/lat/lng/parent/fixed/branch 七個鍵，`equipment` 直接掉地上。scenario 頂層有 `passthrough` 機制（`:363-366`）救了頂層鍵，但它只掃 `bundle.scenario`，救不到 bundle 的兄弟鍵，也救不到 unit 層。

**操作員因此**：這是「安全機制的沉默失效」的完整版：任何人拿劇本編輯器打開一份出貨想定（四份全中招），只改一個名字按「存到伺服器」，那份想定的交戰規則、地形通行覆寫、以及**每一個單位帶的武器彈藥**就全沒了。存檔成功、畫面顯示「已存到伺服器」、沒有任何警告。下一場拿這份新想定開局，部隊會用預設配發的槍上場。

**怎麼補**：先做**結構性保真**再談 UI：`useScenarioEditor.ts` 的 ScenarioModel 加 `bundlePassthrough`（收 scenario/orbat/msel 以外的 bundle 鍵）與 EditorUnit 的 `unitPassthrough`（收七個已建模鍵以外的 unit 鍵，含 equipment），export 時先攤開再覆蓋——與現有 `passthrough` 同一套寫法。`platform/tests/scenario-editor.test.ts` 加一條「拿 scenarios/examples/joint-defense 的 bundle 跑 import→export，深比對不得少鍵」的測試。

**優先度**：最高——它比缺 UI 嚴重，因為缺 UI 只是做不到，這個是**會把已經做好的東西刪掉**，而且無聲。應該排在 ROE / 編裝 / 機動覆寫三張 UI 卡之前。


### [M] MSEL 狀況腳本的完整檢視——每一條狀況的觸發條件、注入內容、已發/已跳過/待命狀態

*端點*

**後端**：整份腳本持久化在 core/app/models/tables.py:141-143 `WargameSession.msel`（JSON），每條含 id / trigger / inject / once（core/app/scenario/session_msel.py:27-46 `load_session_msel`）。但唯一的讀取端點 core/app/api/msel.py:37-47 `GET /sessions/{id}/msel` 只回 `{"pending": [str, ...]}`——**只有還沒發的那些的 id 字串**，沒有標題、沒有觸發條件、沒有已發/已跳過的歷史。

**前端**：platform/app/pages/session/[id]/white-cell.vue:268-289、495-506：清單直接 `v-for="id in mselPending"` 印 id，旁邊兩顆「扣板機 / 跳過」。想定編輯器那一側是完整的（platform/app/pages/scenario-editor.vue + useScenarioEditor.ts:283-286 有 msel 的 export/import 與條件 DSL 編輯器），所以**寫得出來卻在演習中看不懂**。

**操作員因此**：統裁在演習進行中看到的是一串 `msel-003`、`msel-007`。他不知道 msel-003 是「敵增援出現」還是「橋梁被炸」，也不知道觸發條件是「紅軍推進到北岸」還是「tick 500」，更看不到剛才哪幾條已經發過了。要對照就得另開想定編輯器把腳本讀一遍——而白軍控制台的整個價值就在於「不用離開這個畫面」。

**怎麼補**：擴充 core/app/api/msel.py 的 GET：回完整清單（id / 觸發條件的人話描述 / 注入動作摘要 / 狀態 PENDING|FIRED|SKIPPED / 觸發 tick），資料源用 `load_session_msel` + Redis 的 pending/fired 集合；契約 contracts/core_api.yaml:2545 那條 operation 的 response schema 一起改（它目前只宣告 id 陣列）；前端 white-cell.vue 把清單換成表格，觸發條件的中文渲染可重用既有的 platform/app/composables/useConditionDsl.ts。

**優先度**：中等——白軍控制台是 V2.1 的招牌功能之一，目前這一塊的可用性最弱


### [M] 事件的「為什麼」——移動/工兵類事件把原因、座標、油量、地形格記在 LedgerEvent.detail

*事件回饋*

**後端**：core/app/state/broadcaster.py:83-118 `build_event_envelope` 只從 `event.ai_decision` 撈一份 15 鍵白名單（status/reason/reason_detail/target_health_after/from/to/mode/winners/observation/rounds/estimated_losses/is_estimate/error_band/cause/shooter_faction），**完全不碰 `event.detail`**。而 core/app/engine/movement.py:545-556 的 MOVE_HALTED_FUEL 把 `reason=OUT_OF_FUEL` / `fuel_remaining` / `fuel_burn_per_km` / `lat,lng` 全放在 `detail`；movement.py:653-663 MOVE_BLOCKED 的 `reason=IMPASSABLE_TERRAIN` + `cell` 同理；movement.py:724-735、763-775 MOVE_ATTRITION 的 `reason=MARCH` / `FORCED_CROSSING` + `distance_km` + `strength_before/after` 同理；movement.py:507-519 MINE_STRIKE 的障礙 `label` / `feature_id` 同理；core/app/engine/obstacle_wiring.py:245-248 ENGINEER_WORK_ABORTED 的 `reason=TARGET_GONE` 同理。

**前端**：platform/app/composables/useCopFeed.ts:21-24 檔頭已自承「LedgerEvent.detail 完全不下發」；:172-176 `whyOf()` 只讀 `reason_detail`/`reason`，:296 通用格式因此拿到空字串。反證：useCopFeed.ts:127-154 的 `REASON_LABELS` 裡有 `OUT_OF_FUEL` / `IMPASSABLE_TERRAIN` / `MARCH` / `FORCED_CROSSING` / `TARGET_GONE` 五條中文翻譯——它們**在 feed 上一次都不可能被觸發**，因為那些 reason 只存在於 detail。翻譯寫了，路徑不通。

**操作員因此**：戰況欄只會冒出「第1裝甲連 燃料耗盡，就地停止」「第2連 移動受阻」「第3連 行進耗損 −4.2」——沒有停在哪裡、剩多少油、卡在什麼地形、耗損是行軍磨的還是硬穿障礙付的代價。參謀要判斷「這支部隊還救不救得回來」「要不要改派別條路」，得自己去問後端或翻資料庫。這正是回報的那條線索，查證屬實。

**怎麼補**：（a）`core/app/state/broadcaster.py:95-118` 在白名單機制外，另加一段從 `event.detail` 取「可公開的敘述欄」（建議白名單而非整包轉發：`reason`/`profile`/`cell`/`label`/`fuel_remaining`/`distance_km`——`lat`/`lng` 不可轉發，那是機動情報，會穿透 fog of war）。（b）`platform/app/composables/useCopFeed.ts` 的 `formatEvent` 為 MOVE_HALTED_FUEL / MOVE_BLOCKED / MOVE_ATTRITION / MINE_STRIKE 各補一條專屬敘述（比照 ENGAGEMENT_RESOLVED 的寫法）。（c）在 `core/tests/unit/test_frontend_event_labels.py` 加一條：`REASON_LABELS` 的每個鍵都必須真的到得了 feed。

**優先度**：最早做——它是本視角唯一「已經有翻譯、只差一條線」的項目，投入產出比最高，且直接對到使用者的原始回報。


### [M] 戰況事件流的訊噪比——SENSOR_CONTACT 每次偵獲都發一則

*事件回饋*

**後端**：core/app/state/broadcaster.py:31 `_FEED_EXCLUDE = frozenset({"UNIT_MOVED", "TICK_OVERRUN"})` ——SENSOR_CONTACT 不在排除之列，全數推進 feed。活體證據：session adda6b01 的 `/aar/stats` 顯示 `SENSOR_CONTACT: 35347`，同場交戰類事件合計不到 10 則。

**前端**：EventsPanel.vue 無任何篩選或分類（見上一項）；cop.vue:586-588 只留最後 20 則。前端另有獨立的情報面板（platform/app/composables/useIntel.ts）在呈現接觸清單，也就是說同一份資訊已經有專屬去處。

**操作員因此**：戰況欄變成「偵獲接觸：敵戰車排／偵獲接觸：敵戰車排／偵獲接觸：…」一路洗到底，20 則的視窗被同一件事吃光。真正該被看見的交戰命中、友軍誤傷、燃料耗盡，冒出來不到一秒就被推出畫面。等於這個小工具在實際演習中是壞的。

**怎麼補**：（a）後端層：`_FEED_EXCLUDE` 加入 SENSOR_CONTACT（位置已由情報面板／STATE_DIFF 呈現，比照 UNIT_MOVED 的理由），或改為「同一組 observer→target 在 N tick 內只發一次」的節流。**排除清單一改就要同步改 useCopFeed.ts:119 的 `EVENT_TYPES_NOT_IN_FEED`**，`core/tests/unit/test_frontend_event_labels.py::test_hidden_event_list_matches_backend_feed_exclusion` 守著這條。（b）前端層：事件分類（交戰／機動／情報／系統）可勾選。

**優先度**：早做——這一項不修，前面所有「把事件說得更清楚」的改動都看不到效果，因為那些行根本停不住。


### [M] 白軍即時注入的事件——統裁在控制台手動打進戰場的臨時狀況

*事件回饋*

**後端**：core/app/api/inject.py:1-3 的檔頭寫「注入任意 MSEL/臨時事件到 **Ledger + WS stream**」，但 :48-56 的實作只呼叫 `publish_event(client, session_id, req.event_type, {...})` 推 Redis，**沒有任何 LedgerWriter**。

**前端**：platform/app/pages/session/[id]/white-cell.vue:176-181 的註解已經自承這件事（「不寫 Ledger」），並據此把注入表單限縮成 `live` 型態。COP 端收得到（走同一條串流），AAR 端則因為 `/aar/*` 一律讀 TacticalEventLog（core/app/aar/events.py:47-66）而完全讀不到。

**操作員因此**：統裁在演習中注入「橋樑遭破壞」，當下所有人都看到了；檢討會打開 AAR，這件事不存在——時間軸沒有、事件分布沒有、敘事報告不會提。於是「當時到底是誰讓部隊繞路的」在事後無從追究，而那正是統裁最需要被記錄的一類介入。

**怎麼補**：core/app/api/inject.py:48-56 在 publish 之外補一次 `LedgerWriter(default_session_factory()).append(...)`（比照 core/app/api/c2.py:542-549 的 `_ledger()` 寫法，落帳失敗以 suppress 包住、不擋住注入本身）。注意 `source: WHITE_CELL_INJECT` 要一起入帳，檢討時要分得出哪些是引擎裁決、哪些是統裁手動打的。

**優先度**：中等——注入是白軍控制台的核心動作，但要先確認自訂 event_type 入帳不會污染 AAR 統計。


### [M] MSEL 觸發條件與勝負條件的條件 DSL

*想定與編裝*

**後端**：`core/app/scenario/triggers.py:127-140` 的 `_CONDITION_FIELDS` 有 **12 種**：time / faction_eliminated / strength_below / unit_in_region / unit_in_polygon / contact_established / manual / after_ticks_of / held_for / all / any / not，`evaluate_condition`（:63-101）逐一實作，載入時 `validate_condition` 會驗必填欄位。其中 `manual` 有專屬的白軍扣板機端點（`core/app/api/msel.py`：`POST /sessions/{id}/msel/{entry_id}/fire`，註解自述「`manual` 唯一會成立的方式」）。

**前端**：`platform/app/components/ConditionBuilder.vue:28-35` 的 `TYPE_OPTIONS` 只有 **6 種**（time / faction_eliminated / strength_below / unit_in_region / all / any）；`platform/app/composables/useConditionDsl.ts` 的 `ConditionType` 同樣只宣告這 6 種。缺的是 unit_in_polygon、contact_established、manual、after_ticks_of、held_for、not。

**操作員因此**：統裁寫劇本時做不出幾種最常用的狀況：「等紅軍**發現**藍軍才發這個狀況」（contact_established）、「A 事件之後 30 分鐘」（after_ticks_of）、「陣地**連續守住** 20 分鐘才算」（held_for）、「不規則地形區」（unit_in_polygon）、「**由我教官看現場決定要不要發**」（manual）。最痛的是 manual：白軍控制台上那兩顆「扣發／跳過」按鈕已經做好了，但編輯器產不出只認人工扣板機的狀況，所以那個「動態取捨」的設計等於沒有腳本可用，只能手寫 msel.yaml。

**怎麼補**：`useConditionDsl.ts` 的 `ConditionType`／`emptyCondition` 補 6 種；`ConditionBuilder.vue` 的 `TYPE_OPTIONS` 與各型別的欄位模板補齊——manual / not / held_for / after_ticks_of 是純表單（S 級），contact_established 是兩個陣營下拉，unit_in_polygon 需要沿用 `MapPointPicker` 做多點取點（這一項最重）。可拆成兩張卡先出前四種。

**優先度**：中高——manual 那一段是「白軍已經有按鈕、劇本產不出對應事件」的對接缺口，先補它的 CP 值最高。


### [M] 回滾到指定快照點（統裁把演習倒帶重來）

*統裁與治理*

**後端**：core/app/api/control.py:62 `list_checkpoints`；core/app/state/checkpoint.py:369 `list_points` **沒有 LIMIT 也沒有分頁**，一次撈完該局全部。實測 `curl .../sessions/20f185f5-.../checkpoints | len` → **3799 筆**，且回的欄位只有 tick / ledger_seq / state_hash / created_at。

**前端**：platform/app/pages/session/[id]/white-cell.vue:388-396 把整份清單塞進一個原生 `<select>`；標籤由 platform/app/composables/useWhiteCell.ts:142 `checkpointLabel` 產生，格式是 `T2223600 · 01:44（…前）· 校驗碼 721e5c8f`。同檔 :51 註解說「實測一局有上百個點」——實際是 3799 個。每 10 秒（CLOCK_TICK_MS）重算全部選項文字。

**操作員因此**：統裁想回到「剛剛那場交戰之前」，要在 3799 個長得幾乎一樣、只有 tick 編號與牆鐘時間的選項裡用眼睛找。沒有搜尋、沒有分頁、也沒有推演當下的日期時間可對照——實務上等於這個功能只能用來『回到最新那一個點』。

**怎麼補**：後端 `list_checkpoints` 加 `limit`/`before_tick` 查詢參數並在回應帶推演時間（由 tick × tick_rate_ms + start_time 換算）→ 契約 → white-cell.vue 把下拉換成「最近 N 個 + 依推演時間搜尋」的挑選器，標籤主體改成推演日期時間而非牆鐘。

**優先度**：中——能力在、只是選不到；但一旦真的要在演習中回溯，現在這個 UI 會直接卡住流程。


### [M] MSEL 待命注入的扣發／跳過（統裁看現場決定要不要發下一個狀況）

*統裁與治理*

**後端**：core/app/api/msel.py:37 `list_pending` 回 `dict[str, list[str]]`；core/app/state/live_msel.py:76 `publish_pending(client, session_id, pending: list[str])` —— runner 只發佈**條目編號字串**，內容（會注入什麼動作、原定觸發條件）留在 runner 行程裡沒有對外。

**前端**：platform/app/pages/session/[id]/white-cell.vue:496-503 `<li v-for="id in mselPending"><code>{{ id }}</code>` 加「扣發」「跳過」兩顆按鈕。同頁 hint 自承：「清單只給得出腳本條目編號：後端不供應內容（會注入什麼、觸發條件為何），須查該局想定。」

**操作員因此**：統裁畫面上出現 `E3`、`E7` 兩行，旁邊各兩顆按鈕，但不知道按下去會發生什麼、原本設定在什麼條件下會自己發。要判斷『現在該不該發這個狀況』得同時開著想定檔對編號——演習中沒人有這個餘裕，實務上會變成一律不敢按。

**怎麼補**：MselRuntime 發佈 pending 時改帶 `{id, description, action, trigger_summary, once}`（core/app/state/live_msel.py 的 publish_pending 與 api/msel.py 回應型別一起改）→ 契約 → white-cell.vue 每一列顯示狀況敘述與動作型別；順便把已扣發/已跳過的做成歷史區塊（現在扣完就從清單消失，看不出這場發過什麼）。

**優先度**：中高——WP-B2c 的引擎已經做完，卡在最後一哩的顯示；對統裁的可用性影響很直接。


---

## 只有間接路徑（8）


### [S] 陣營的顯示名稱（display_name，例如把 BLUE 顯示成「國軍第八軍團」）

*想定與編裝*

**後端**：`contracts/scenario.schema.json:79-81` factions[].display_name。載入器解析為 `loaded.faction_display_names`，`core/app/scenario/loader.py:539` 一帶「陣營顯示資訊落地」隨局持久化；匯出端 `core/app/scenario/dump.py:24-25` 也寫得出來。

**前端**：編輯器**模型層**有（`useScenarioEditor.ts:22` EditorFaction.displayName、`:242` export、`:383` import），但**畫面上沒有輸入框**：`scenario-editor.vue:672-676` 的陣營列只有 `InputText v-model="f.id"` 和一個 `type="color"`。要設 display_name 只能去「匯入 JSON」文字框手貼。

**操作員因此**：想定作者只能把陣營叫 BLUE / RED / GREEN。演習的席位、地圖、AAR 上通通是這些代號，看不到「第八軍團」「登陸部隊」這種參演人員真正在講的名字。

**怎麼補**：`scenario-editor.vue` 的陣營列在 id 與顏色之間插一個 `InputText v-model="f.displayName"`（placeholder「顯示名稱（選填）」）。模型與 export/import 都已就緒，不需動 composable。

**優先度**：低但幾乎零成本——一行模板，順手做掉。


### [S] 軍械庫的油料諸元（mobility.fuel_capacity / fuel_burn_per_km / fuel_burn_per_tick）

*想定與編裝*

**後端**：#84 油料模型已上線：種子資料帶值（`core/app/adjudication/seed_weapons.py:110-113` HOWITZER_155_SP 的 fuel_capacity 510 / fuel_burn_per_km 1.5；MBT 1900 / 4.5），契約 `contracts/weaponeering.schema.json` 的 `$defs.mobility` 有這三欄，執行期真的燒（前端已有 `MOVE_HALTED_FUEL: '燃料耗盡，就地停止'`，`platform/app/composables/useCopFeed.ts:57`；活油量走 STATE_DIFF，`useLiveState.ts:45-47`）。

**前端**：`platform/app/pages/armory.vue:342-347` 的 `readMobility` 只讀 can_self_move / mobility_class / max_road_speed_kmh / max_cross_country_speed_kmh 四欄，`:358-368` 的 `mobilityStats` 也只寫這四欄。同檔 `:349-357` 的註解自承：「表單只涵蓋四個欄位，但 `mobility` 底下還有 `fuel_capacity` / `fuel_burn_per_km`（#84 油料模型）」。它靠 `...prev` 淺合併保住舊值，所以不會被刪，但**表單改不到**——只能切「JSON 檢視」手改。

**操作員因此**：想調一輛車的續航（例如「這批戰車滿油只能跑 300 公里」）的人，在軍械庫表單上找不到欄位，得知道要按「切 JSON」再手改巢狀 mobility 物件。而續航正是決定「這場能不能一口氣打到目標」的關鍵數字之一，是想定校準時最常動的參數。

**怎麼補**：`armory.vue` 的機動性區塊（ARTILLERY/VEHICLE/LOGISTICS 共用）加三個數字欄：油箱容量、每公里油耗、每 tick 油耗；`readMobility` 與 `mobilityStats` 同步補。續航（capacity ÷ burn_per_km）順手顯示一行即時換算，這是使用者腦子裡真正在算的數。

**優先度**：中——單一檔案小改動，但它擋住的是「油料模型剛做完卻調不動」這件事，做完立刻有感。


### [M] 契約漂移：11 條實作端有、契約沒有的端點——前端拿不到型別

*端點*

**後端**：用跑起來的服務直接比對契約：`curl /openapi.json` vs contracts/core_api.yaml，路徑參數名正規化後差集為 11 條（扣掉 `/healthz` 這個刻意排除的探針）：`GET/PUT/DELETE /sessions/{}/autonomy`、`GET /sessions/{}/aar/export`、`GET /sessions/{}/aar/missions`、`GET /sessions/{}/aar/replay`、`GET /sessions/{}/aar/report`、`GET/PUT /sessions/{}/orbat-permissions`、`POST /sessions/{}/units/{}/reposition`、`POST /system/config/test-llm`。與 core/tests/unit/test_contract_conformance.py 的 `_IMPL_ONLY` 完全一致（該清單註明「只能變短」）。

**前端**：除了 aar/missions（完全沒接）之外，其餘 10 條前端**都有呼叫端，但全部是手刻型別**：platform/app/composables/useAar.ts:154-169（`apiFetch<AarReplay>`、`apiFetch<AarReport>` 這些 interface 是人手寫的）、platform/app/pages/session/[id]/autonomy.vue:94-133、platform/app/composables/useEquipment.ts:27-36（`apiFetch<{ factions: string[] }>`）、platform/app/composables/useMapStateEdit.ts:65-88、platform/app/pages/system-settings.vue:135（`apiFetch<{ ok: boolean; detail: string; latency_ms: number | null }>`）。

**操作員因此**：對操作員是隱形的——直到後端改了某個欄位名，畫面上那一格默默變成空白或 `undefined`，而 typecheck 全綠、測試全綠。這正是這個 repo 的招牌病「存得進去、讀得回來、測試全綠、實際沒效果」的前端變體：自主主控台、AAR 重播、ORBAT 權限、地圖狀態編輯這四塊功能目前都靠人工同步維持。

**怎麼補**：把 11 條補進 contracts/core_api.yaml（含 response schema），重生 platform/app/types/api.ts，把各 composable 的手刻 interface 換成 `components['schemas'][...]`，再把 `_IMPL_ONLY` 清成空集合。純契約工作、無行為變更，可以一次做完也可以拆成 AAR / autonomy / 其他三批。

**優先度**：跟著上面的 aar/missions 一起做最省——那條反正要進契約


### [M] 戰況事件的定位與檢索（序號、tick、受眾陣營、關鍵字篩選）

*事件回饋*

**後端**：每則 EVENT envelope 都帶 `seq`（core/app/state/redis_stream.publish_to_stream 指派）與 `payload.tick`（broadcaster.py:83）與 `factions` 受眾標籤（broadcaster.py:120-122）——三樣資訊後端都送到前端手上了。

**前端**：**只有白軍控制台用得上**：platform/app/pages/session/[id]/white-cell.vue:306-325 的 `feedRows` 做出 seq／tick／受眾三欄 + 關鍵字篩選 + `FEED_LIMIT = 200`（:297）。一般席位的 platform/app/components/cop/EventsPanel.vue:23,29-31 只有 `formatEvent(...)` 一行純文字，沒有 tick、沒有 seq、沒有篩選；cop.vue:586-588 只留 20 則。

**操作員因此**：指揮官與參謀看到的戰況欄是一串沒有時間的句子——「第1連 交戰命中 → 敵戰車排 −70」發生在第幾分鐘？不知道。想回頭找「剛剛那則觸雷」也沒得搜。AAR 敘事報告引用的是 seq，COP 上根本看不到 seq，兩邊對不起來。同樣的能力白軍那邊做完了，一般席位沒有。

**怎麼補**：把 white-cell.vue:306-325 的列結構抽成共用（或直接讓 `EventsPanel.vue` 收 `seq`/`tick`/`audience` 並加一個篩選輸入框），cop.vue:586-588 的 `slice(-20)` 放寬到 `FEED_LIMIT`（視窗高度由 useCopWidgets.ts:57 的 `h:148` 控制，可捲動即可）。

**優先度**：中等——白軍那份現成程式碼可以直接搬，但要先確認 seq 曝露給一般席位不違反迷霧（seq 本身是全域計數器，會洩漏「別人那邊發生了幾件事」，需評估）。


### [M] AI（LLM）指揮官的推理鏈——護欄要求每個 AI 決策附至少 3 步編號推理

*事件回饋*

**後端**：core/app/guardrails/gateway.py:174-176 G2 護欄強制 `reasoning_chain` 非空；core/app/models/tables.py:253 落 DB；core/app/aar/export.py:47 匯出時帶出。

**前端**：`rg -n 'reasoning' platform/app --glob '!types/api.ts'` → 零命中。broadcaster.py:83-118 的 envelope 也不帶（`reasoning_chain` 是 LedgerEvent 的頂層欄位，不在 `ai_decision` 裡）。唯一取得途徑是 aar.vue:277 的「匯出 JSON」下載檔案。

**操作員因此**：AI 扮演的敵軍指揮官下了一道令，人類指揮官／統裁想知道「它為什麼這樣打」——這是 AI 輔助兵推最該讓人看到的東西，而畫面上零曝露。要看只能下載 JSON 用文字編輯器搜。

**怎麼補**：（a）AAR 頁在書籤／事件列點下去時顯示該事件的 `reasoning_chain`（需 `/aar/replay` 或新端點回這個欄位，契約要動）。（b）活推演中則不宜——那是敵方的思路，會穿透迷霧；限 AAR 與全知席位。

**優先度**：中等——產品差異化的東西，但要先想清楚「活推演中誰能看」的迷霧規則，不能順手曝露。


### [M] 破障（ENGINEER/BREACH）指定要破的障礙

*下令*

**後端**：`core/app/orders/schemas.py:129-145`（BREACH 需 `feature_id`）；`core/app/engine/obstacle_wiring.py:236-255` 依 feature_id 破障、工時看標的型別（:211-213）；預檢 `engineer_target` / `engineer_proximity`（標籤見 `platform/app/composables/useLabels.ts:103-105`）。

**前端**：`UnitsOrderPanel.vue:436-444` 是一個純文字輸入框 `placeholder="障礙標註 id"` 綁 `ordering.engineerFeatureId`。地圖點選路徑（`cop.vue:445-449`）只填 `engineerPoint`（EMPLACE 用），**沒有任何地方會把選中的障礙標註 id 填進去**；`MapContextMenu.vue` 也沒有破障項（`rg -n "BREACH" platform/app` 只命中 UnitsOrderPanel 與 useCopFeed 的事件標籤）。

**操作員因此**：要下破障令，工兵排長得先想辦法弄到那片雷區的內部 id（開發者工具或問白軍），再手打進一個文字框。實務上這道令等於下不了——而破障是裝甲突穿科目的關鍵動作。

**怎麼補**：讓地圖上點選障礙標註時把 id 帶進來：`cop.vue` 的 feature 點選已有 `selectedFeatureId`，在 ENGINEER/BREACH 模式下把它寫入 `engineerFeatureId`，面板改成顯示「已選標的：鐵絲網（xxx）」＋一個「從地圖選標的」按鈕；或在 `MapContextMenu.vue` 對障礙類標註加一個「派工兵破障」項。

**優先度**：早——這一項不修，C2 障礙工兵那張卡在人類席位上等於只做了一半。


### [M] 白軍專用面板的型別安全（自主推演設定、各軍自編權限、LLM 連線測試）

*統裁與治理*

**後端**：core/tests/unit/test_contract_conformance.py:67-73 的已知漂移清單 `_IMPL_ONLY` 明列：`GET/PUT/DELETE /api/v1/sessions/{}/autonomy`、`GET/PUT /api/v1/sessions/{}/orbat-permissions`、`POST /api/v1/system/config/test-llm` —— 六條實作有、契約沒有。

**前端**：因為契約沒有，前端拿不到產生的型別，只能手抄：platform/app/pages/session/[id]/autonomy.vue:20-24 自己寫 `interface AutonomyView`；platform/app/composables/useEquipment.ts:28 與 white-cell.vue:235 各自寫一份 `{ factions: string[] }`；system-settings.vue:135 自己寫 test-llm 的回傳型別。（對照組：`ai-status` 有進契約，useAiStatus.ts:7 就吃得到 `components['schemas']['AiFactionStatus']`。）

**操作員因此**：這幾條都是白軍/統裁專用的設定面板。後端加或改欄位時前端不會有任何紅燈，只會在演習當天發現『設定按了儲存卻沒生效』——core/app/api/autonomy.py:58-61 的註解就記著 `ai_ground_truth` 出過這個問題（欄位沒宣告，白軍設了直接被丟掉）。

**怎麼補**：把這六條補進 contracts/core_api.yaml（含 AutonomyConfig / FactionAI / OrbatPermissions / TestLlmRequest / TestLlmResult 五個 schema）→ 重新產生 platform/app/types/api.ts → 把 autonomy.vue、useEquipment.ts、white-cell.vue、system-settings.vue 的手抄型別換成產生的型別 → 從 `_IMPL_ONLY` 刪掉這六條（該檔 test_the_known_drift_list_only_shrinks 會強制你刪）。

**優先度**：中——不修不會馬上壞，但它是這一塊未來所有『存得進去、實際沒效果』的溫床。


### [L] 演習中的臨機裁決：生增援、直接調某單位戰力/位置、改天氣、發信文給指定席位

*統裁與治理*

**後端**：core/app/scenario/msel_actions.py:8-15 定義五種**會真的改變世界**的注入動作（SPAWN_UNITS / MODIFY_UNIT / MESSAGE / PAUSE / WEATHER_OVERRIDE），由 core/app/sim_runtime.py:590 `make_applier(...)` 於 MSEL 觸發時套用。但 core/app/api/inject.py:51 的即時注入只做 `publish_event(...)`，**不經過套用層、也不寫 Ledger**。

**前端**：platform/app/components/InjectActionForm.vue:14-18 自承兩種型態的語義差別；white-cell.vue:423 用 `variant="live"`，因此白軍控制台的注入表單**刻意不給任何動作選項**。五種動作的結構化表單（:324 MODIFY_UNIT、:384 MESSAGE、:421 SPAWN_UNITS、:471 WEATHER_OVERRIDE）只在想定編輯器（開局前）用得到。

**操作員因此**：演習跑到一半，統裁判定「這一波應該有一個連的增援上來」「這個營被友軍誤擊應該再掉三成戰力」「起霧了」——這些都只能在開局前寫進 MSEL 腳本。臨場能做的只有發一則純文字說明，玩家看得到訊息、模型裡什麼都沒變。這正是白軍軟裁決最常用的動作。

**怎麼補**：新增 `POST /api/v1/sessions/{id}/msel/adhoc`：接與 MSEL entry 同構的 action payload，推進 runner 的命令佇列由 `make_applier` 於下一 tick 套用並寫 Ledger（沿用 core/app/state/live_msel.py 的 push 機制）→ 契約 → white-cell.vue 的注入區改用 `variant="msel"` 並標明「下一 tick 生效、會記入帳本」。

**優先度**：中高——白軍控制台最核心的裁決能力目前只能靠事前腳本，但這條要動 runner 命令通道，範圍不小。


---

## 看得到改不了（2）


### [S] 設障作業的障礙尺寸（ENGINEER/EMPLACE 的 radius_m）

*下令*

**後端**：`core/app/orders/schemas.py:136`（`radius_m`，預設 200，可 0<x≤5000）；`core/app/engine/obstacle_wiring.py:264` `influence_radius_m=float(payload.get("radius_m") or 200.0)` 直接成為地圖標註的影響半徑。

**前端**：`useCopOrdering.ts:360` `const engineerRadiusM = ref(200)`、:404 送出時帶——但 `rg -n "engineerRadiusM" platform/app` 只有 composable 內部三處（:360/:404/:535），`UnitsOrderPanel.vue` 的 ENGINEER 區塊（:407-452）**沒有任何綁到它的輸入框**。等於一個永遠是 200 的常數。

**操作員因此**：工兵設的雷區/戰車壕一律 200 m 寬，不管是要封一條 50 m 的隘路還是一段 2 km 的正面。障礙計畫在圖上畫得出各種大小（地圖編輯器可以），但用工兵令真的施工出來的一律同一個尺寸——圖上規劃與場上結果對不起來。

**怎麼補**：ENGINEER/EMPLACE 區塊加「作業半徑（公尺）」數字輸入（min 50 / max 5000，預設 200）綁 `ordering.engineerRadiusM`，並在地圖上以圓圈預覽範圍（`useMapFeatures.ts:455-468` 已有 genCircle 可用）。

**優先度**：中——典型的「欄位存在、沒人編得動」，改動極小。


### [M] 想定的禁射區／限制射擊區（no_strike_zones）

*想定與編裝*

**後端**：`contracts/scenario.schema.json:162-230`（NO_STRIKE 硬阻擋、RESTRICTED_FIRE 升白軍確認，polygon/circle 兩種幾何）。隨局落地 `core/app/scenario/loader.py:523` `no_strike_zones=loaded.no_strike_zones or None`；判定 `core/app/orders/no_strike.py`（`zones_to_cells` 純函數 + `_feature_zones` 併入 COP 圈的區）。四份出貨想定都有宣告（`rg -n 'no_strike_zones' scenarios/examples/*/scenario.yaml`）。

**前端**：`useScenarioEditor.ts:110` `noStrikeZones?: Array<Record<string, unknown>>` ——**只有原樣帶著**，`:107-109` 的註解自己寫明「編輯器不編輯禁射區（那是 COP 地圖編輯器的事）」。`scenario-editor.vue` 沒有任何禁射區欄位，只有匯出 JSON 文字框看得到內容。COP 那條路（`useMapEditor.ts:90-103` 繪製時可選 zone_class）改的是**該局的 MapFeature**，不回寫想定。

**操作員因此**：「這份想定永遠保護這幾間醫院」這件事，寫劇本的人設不了也改不了——只能在匯出的 JSON 裡看到它存在。要新增保護區只有兩條路：手寫 scenario.yaml，或每一局開打後由白軍在 COP 上重畫一次（而且下一局又要重畫）。

**怎麼補**：編輯器加「禁射區」section，重用 `MapPointPicker`／地圖繪製元件產出 polygon ring 或 circle(center+radius_m)，每筆要 name + zone_class。模型欄位 `noStrikeZones` 已經在了，只缺 UI 與型別化。

**優先度**：中——現況至少不會遺失（passthrough 有守住），而且 COP 有可用的替代路徑；但「隨想定走的保護區」與「這一局白軍臨時圈的」在語義上不同，長期要分開。


---

## 完整可用（25）


### [S] 敵情接觸（/intel）與陣營關係矩陣（/relations）

*端點*

**後端**：`GET /api/v1/sessions/{id}/intel`（core/app/api/intel.py:59）與 `GET /api/v1/sessions/{id}/relations`（core/app/api/relations.py:41）都在，且被 core/app/api/state.py:88-103 直接呼叫函式聚合進 `/state` 快照（該檔 11 行明講「一致性由構造保證」）。

**前端**：兩支獨立端點的呼叫函式 `fetchIntel` / `fetchRelations` 定義在 platform/app/composables/useIntel.ts:16-38，但 `rg 'fetchIntel|fetchRelations' platform/app platform/tests platform/e2e` 除定義處外零命中——**是死碼**。不過能力本身完整可見：COP 從 `/state` 一次拿到（platform/app/pages/session/[id]/cop.vue:323 `intelContacts.value = snap.contacts`、325 行取關係矩陣），fog 過濾與友我判準都在後端做（platform/app/composables/useCopUnits.ts:74-118）。


### [S] 演習專案全生命週期（建立/階段/檢查表/掛局/稽核/封存包/簽證/銷毀）

*端點*

**後端**：core/app/api/exercises.py 共 13 條端點（GET/POST `/exercises`、`/{id}` GET/DELETE、`/phase`、`/checklist/{key}`、`/sessions` POST、`/sessions/{sid}` DELETE、`/audit`、`/bundle`、`/destroy`、`/seal` POST/GET/DELETE）。

**前端**：platform/app/composables/useExercises.ts:54-166 逐一對應包了 12 支，全部被 platform/app/components/ExercisePanel.vue:41 匯入使用；bundle 下載走 apiFetch+Blob（該檔 102-108 有註記，避免 `<a href>` 打到 Nuxt 自己）。唯一沒接的是 `GET /exercises/{id}`（單筆），因為列表已含全欄位——屬合理省略。


### [S] 白軍時間控制（暫停/續行/回滾）與 ad-hoc 事件注入

*端點*

**後端**：core/app/api/control.py:62 `GET /checkpoints`、105 `POST /control`（`_ACTIONS = {PAUSE, RESUME, ROLLBACK}` + `target_tick`）；core/app/api/inject.py:39 `POST /inject`（event_type / payload / faction）。皆限白軍。

**前端**：platform/app/composables/useWhiteCell.ts:23（checkpoints）、160-170（三種 action 都送得出，含 target_tick）、172-182（inject 三個欄位都帶），由 platform/app/pages/session/[id]/white-cell.vue 使用；PAUSE/RESUME 另外被地圖狀態編輯重用（platform/app/composables/useMapStateEdit.ts:31、54）。回滾點選單有人話格式化（useWhiteCell.ts:150-152 顯示 tick / 時間 / 校驗碼前 8 碼）。


### [S] 下令（7 種令型）+ 逐武器彈藥 + 移動路徑預覽 + 火力計畫

*端點*

**後端**：`OrderType` 9 值（contracts/core_api.yaml:1272）；`GET/POST /orders`、`DELETE /orders/{id}`（core/app/api/orders.py:53-79）；`GET /units/{id}/weapons`（core/app/api/units.py:267）；`POST /movement/preview`（core/app/api/movement.py:109）；fire-plans 四支（core/app/api/fire_plans.py:143-212）。

**前端**：下令面板 platform/app/components/cop/UnitsOrderPanel.vue:154-162 有 7 個令型，payload 組裝在 platform/app/composables/useCopOrdering.ts:368-430；9 減 7 的差額是 RESUPPLY（後勤，本次排除）與 RECON——**RECON 的缺席是對的**：core/app/seats/__init__.py:23 `UNIMPLEMENTED_ORDER_TYPES = frozenset({OrderType.RECON})`，明文「宣告了但沒有任何執行端…誰都不該下得了」。火力計畫面板 platform/app/components/cop/FirePlanPanel.vue 接滿四支。


### [S] 事件型別的中文化覆蓋率（後端每一種 event_type 都要有中文敘述）

*事件回饋*

**後端**：`rg -o --no-filename 'event_type="[A-Z_]+"' core/app | sort -u` → **47 種**（注意：`rg -h` 是 help 不是 no-filename，用 `-h` 會得到一份 460 行的 ripgrep 說明書當成事件清單——本任務簡報裡「後端 124 種 event_type」這個數字就是這樣來的，**不成立**）。更權威的是 core/tests/unit/test_frontend_event_labels.py:backend_event_types()，它用 AST 掃 `LedgerEvent(event_type=)`、轉發函式的位置參數、以及先賦值再傳的區域變數三種寫法。

**前端**：platform/app/composables/useCopFeed.ts:42-110 `EVENT_LABELS` 共 **52 條**（涵蓋 47 種實發型別 + 排除清單裡的兩種）。`uv run pytest core/tests/unit/test_frontend_event_labels.py -q` → **6 passed**，四條斷言雙向鎖死：每個後端型別都要有中文、不得有沒人發的死條目、前端 `EVENT_TYPES_NOT_IN_FEED` 必須等於 broadcaster 的 `_FEED_EXCLUDE`、cop.vue 必須有暫停橫幅。


### [S] 下令被拒的逐項理由（PrecheckResult.checks 的每一條檢查名 + 中文說明）

*事件回饋*

**後端**：core/app/orders/precheck.py 實際產生的 `PrecheckCheck.name` 共 **20 個**：ammo、combined_fires、engineer_proximity、engineer_qualified、engineer_target、fire_approval、fratricide_warning、indirect_weapon、line_of_sight、mission_params、no_strike、physics、position、range、reachability、roe、roe_weapon、target_exists、trajectory、weapon（`rg -o 'name="[a-z_]+"' core/app/orders/precheck.py | sort -u`）。每一條還附後端組好的中文 `detail`（如 precheck.py:737「間瞄彈道越過地形，免視線（12.3 km）」、:783「拋物線被障礙阻隔：…」）。

**前端**：platform/app/composables/useLabels.ts:85-105 `PRECHECK_LABELS` 恰好 **20 條、完全對齊**（逐一比對無缺無多）。渲染在 platform/app/components/cop/UnitsOrderPanel.vue:556-557：`{{ c.passed ? '✓' : '✗' }} {{ precheckLabel(c.name) }} — {{ c.detail }}`，通過與未通過的項目都列出來。`useLabels.ts:19-22` 的 `lookup()` 遵守「查無原樣回傳」紀律，將來後端加新檢查項會漏出英文代號當提示，不會被抹成「未知」。


### [S] 白軍時間控制與勝負底定的即時回饋（暫停／恢復／回滾／收場）

*事件回饋*

**後端**：core/app/api/control.py:127 走 `publish_event(client, session_id, "SESSION_CONTROL", payload)` ——這條路（core/app/stream/publish.py:30-32）是 `{event_type, **payload}` **整包下發**，不受 broadcaster 白名單限制，所以 `action` 與 `target_tick` 都到得了前端。勝負則由 broadcaster.py:306-317 的 `publish_events_now()` 同步推送（該函式的 docstring 明說它存在的理由就是「否則前端的勝負橫幅永遠不會出現」）。

**前端**：platform/app/pages/session/[id]/cop.vue:589-615 讀 `payload.event_type === 'SESSION_CONTROL'` 取最後一則、依 action 決定是否顯示暫停橫幅；:617- 讀 SESSION_CONCLUDED 顯示勝負橫幅。useCopFeed.ts:279-289 另給 feed 一行帶圖示的敘述（▶／⏸／↩ 依動作變化，:281 註解特別說明「一排都是 ⏸ 的話恢復推演會被讀成又暫停一次」）。core/tests/unit/test_frontend_event_labels.py:test_white_cell_pause_is_visible_on_the_cop 守著這條。


### [S] 火力迷霧相關的事件呈現（面射擊無觀測、BDA 估計值、友軍誤傷）

*事件回饋*

**後端**：broadcaster.py:60-76 的 `feed_damage()` 對 AREA_FIRE_RESOLVED 擋掉傷亡數字（回 None 而非 0，docstring 說明 0 會被讀成「沒打中」），並要求 WS 與 AI briefing 兩個投影邊界都呼叫它；:104-115 白名單特意帶上 `observation`/`rounds`（面射擊）、`estimated_losses`/`is_estimate`/`error_band`（BDA）、`cause`/`shooter_faction`（誤傷，註解寫「沒列進來的話 FRATRICIDE 到了 COP 只剩一個空殼」）。

**前端**：useCopFeed.ts:238-245 面射擊只說「落彈 N 發（無觀測，散布加倍）」不說戰果；:252-258 BDA 永遠標「約 −X（估計 ±Y%）」；:248-251 誤傷用「⚠ 友軍誤傷（面射擊）」最直白的字。三處都有註解說明為什麼不能多寫。


### [S] 單位戰備狀態（OK / DEGRADED / DOWN）

*欄位*

**後端**：core/app/api/units.py:85 `UnitView.readiness`，由 `health_state(strength/authorized_strength)` 導出（core/app/api/units.py:148-150）。欄位註解說明它存在的理由：`health` 在戰力比 ≤0.30 就歸零，光看 health 會把還活著的連隊讀成「已殲滅」。

**前端**：完整渲染。`platform/app/components/cop/UnitDetailCard.vue:115-118` 以 `READINESS_LABELS[unit.readiness]` 顯示，帶 `rdy-*` 顏色類別與 `data-testid="unit-readiness"`；型別在 `platform/app/composables/useUnits.ts:66`。


### [S] 下令當下的逐項預檢結果

*欄位*

**後端**：core/app/orders/schemas.py:148-159 `PrecheckCheck{name,passed,detail}` / `PrecheckResult{feasible,checks,reason}`。

**前端**：`platform/app/components/cop/UnitsOrderPanel.vue:549-557` 逐項列出 checks（可行/不可行 + 每項 name 與 detail）；`platform/app/composables/useCopOrdering.ts:483-484` 另把失敗項組成 `✗ 名稱 — 細節` 的訊息。


### [S] AAR 統計指標全套（下令次數 vs 實射次數、命中率、逐陣營戰損、事件型別分布、統計口徑版本）

*欄位*

**後端**：core/app/api/aar.py:206-221 回 `attempts` / `engagements_fired` / `hits` / `hit_rate` / `total_damage` / `guardrail_blocks` / `damage_by_faction` / `event_counts` / `stats_version`。

**前端**：九欄全數上畫面：`platform/app/pages/session/[id]/aar.vue:120-143`（含 `event_counts` 的型別分布清單、`damage_by_faction` 的逐陣營列），`platform/app/composables/useAar.ts:85,96` 另處理 hit_rate 格式與 `stats_version` 口徑不符的警示。


### [S] C2 信文已讀狀態與申請單核覆留痕

*欄位*

**後端**：core/app/api/c2.py:71 `MessageView.read_at`（欄位註解記錄了它曾經是「DB 有、契約有、只有這個 view 漏掉」）；core/app/api/c2.py:107-110 `RequestView.decided_by/decided_at_tick/decision_note`。

**前端**：`platform/app/components/cop/C2Panel.vue:86-91,258-276` 渲染已讀/未讀、未讀計數與標示已讀按鈕；`:323-326` 渲染「核覆：某某 · T120」與核覆說明。


### [S] 本局權限與範圍旗標（允許誤傷、限指揮之單位子集、我坐哪一席、可否編裝）

*欄位*

**後端**：core/app/lobby/schemas.py:29-50 `SessionSummary` 的 `allow_fratricide` / `my_unit_scope` / `my_seat_role` / `orbat_edit`。`allow_fratricide` 的欄位註解點名它是「前端唯一的來源」。實測 `GET /api/v1/sessions` 回應四欄齊全。

**前端**：`platform/app/pages/session/[id]/cop.vue:357-369` 四欄全讀進 reactive state，並實際改變行為：`UnitsOrderPanel.vue:29,87` 依 `allow_fratricide` 決定友軍是否可選、且強制勾誤傷確認；`useCopOrdering.ts:102` 有對應說明。


### [S] 偵蒐令（RECON）

*下令*

**後端**：`core/app/seats/__init__.py:23` `UNIMPLEMENTED_ORDER_TYPES = frozenset({OrderType.RECON})`——後端明確宣告未實作，任何席位都下不了（:28 指揮官也被扣掉）；`core/app/orders/validator.py:39-60` 的 `_PAYLOAD_MODELS` 也沒登錄它；`ai_loop/orders_bridge.py:158` AI 也不產生。

**前端**：`UnitsOrderPanel.vue:154-162` 的令型下拉**沒有 RECON 選項**——選不下去，不會出現「送得出去卻沒反應」。（唯一殘留是 `useOrders.ts:21 RECON: '偵察'` 的顯示標籤，只在真有這種令要渲染時才用得到。）


### [S] 行軍節奏（MOVE 的 tempo：一般／強行軍）

*下令*

**後端**：`core/app/orders/schemas.py:60`（`MovePayload.tempo`）；`core/app/api/movement.py` 的預覽端點吃 tempo；速度 ×1.5 與行軍耗損 ×2.5 在 `core/app/engine/movement.py`。

**前端**：`useCopOrdering.ts:45-49` `TEMPO_OPTS`、:139 `tempo` ref、:238-243 預覽**帶同一個 tempo**、:371-379 送出無條件帶；`UnitsOrderPanel.vue:173-181` 有下拉＋強行軍警語，:222-235 預覽把「行軍耗損」的代價當場顯示出來。


### [S] 姿態令（POSTURE）與隊形/乘駐車（FORMATION 的 formation/mounted）

*下令*

**後端**：`core/app/orders/schemas.py:98` POSTURE pattern `MOVING|HASTY|DEFENSE|DUG_IN` 四值；:108-109 formation 五值 `COLUMN|LINE|WEDGE|VEE|HERRINGBONE`、mounted 三態（None＝不動該欄）。

**前端**：`UnitsOrderPanel.vue:68` `POSTURE_OPTS` 四值全在、:455-462 逐項附中文說明與「轉換要時間」提示；:382-401 隊形五值全在、乘駐車三態（含「（不變更）」＝送出時省略該欄，見 `useCopOrdering.ts:388-394`）。


### [S] 交戰令（ENGAGE）的目標/武器/彈種/火力政策/誤傷確認

*下令*

**後端**：`core/app/orders/schemas.py:63-70`（target_unit_id / weapon_id / ammo_type）；火力政策三值見契約 `core_api.yaml:1348-1355`，消費點 `core/app/adjudication/adjudicator.py:126,438`（`payload.get("fire_policy")` → `resolve_combined_engagement`）。

**前端**：`useCopOrdering.ts:428-434` 四欄都送得出（fire_policy 僅在聯合火力模式帶，與後端「指定 weapon_id 即單武器」的語義一致）；`UnitsOrderPanel.vue:469-542` 有目標下拉、武器下拉（含最小射程與活彈數）、彈種下拉、火力政策下拉、武器組合清單、誤傷二次確認（:479-486）。


### [S] 五項想定頂層設定：申請配額 / 晝夜 / 允許友軍誤傷 / 曲射須經火協核准 / 陣地變換

*想定與編裝*

**後端**：`contracts/scenario.schema.json:284-349` 五段皆宣告；隨局落地 `core/app/scenario/loader.py:530-541`（request_quotas / indirect_fire_requires_approval / allow_fratricide / day_night）；陣地變換 `core/app/fires/survivability.py`。

**前端**：全部有表單：`scenario-editor.vue:526-668` 的「想定設定」section——申請配額三格（`:529-551`）、晝夜勾選＋時分（`:553-600`）、誤傷勾選（`:602-611`）、火協勾選（`:613-629`）、陣地變換勾選＋三參數（`:631-667`）。`useScenarioEditor.ts:132-140` 的 `MODELLED_SCENARIO_KEYS` 也把五者移出 passthrough（避免 UI 關掉後舊值復活）。每一項下面都有一段「開了會怎樣」的說明。


### [S] 戰場範圍 bbox / tick 速率 / 六角解析度 / 彙整裁決層級

*想定與編裝*

**後端**：`contracts/scenario.schema.json:27-63`；hex_resolution 是 `const: 8`（宣告別的值 loader 直接拒載）；aggregate_adjudication_level 隨局落地於 `core/app/scenario/loader.py:544` 一帶（註解記錄它過去「整個沒有持久化」已修）。

**前端**：`scenario-editor.vue:457-523` 的「戰場範圍與節奏」section：bbox 四格 InputNumber + 西界<東界的即時警告（`:473-475`）、tick 速率 + 人話換算 + 過小警示（`:477-497`）、六角解析度**刻意鎖成唯讀 8**（`:499-505`，理由寫在註解裡：給下拉等於給一個必定失敗的選項）、彙整裁決層級下拉含「（未宣告＝營級）」（`:507-518`）。


### [S] MSEL 注入動作五種（增援生成 / 調整單位 / 發狀況信文 / 暫停推演 / 天氣覆蓋）與白軍待命注入扣發

*想定與編裝*

**後端**：`core/app/scenario/msel_actions.py:52-99` 的 `make_applier` 恰為五種 action；白軍動態取捨端點 `core/app/api/msel.py`（GET 待命清單 / POST fire / POST skip，限白軍）。

**前端**：`platform/app/composables/useConditionDsl.ts` 的 `InjectActionKind` 五種齊全，`INJECT_ACTION_KEYS` 逐一列出各動作專屬鍵，`injectActionIssues` 還逐條對應後端的失敗或靜默忽略（例如 MODIFY_UNIT 的 lat/lng 只填一個會被整組忽略）；`InjectActionForm.vue` 有 `variant: 'msel' | 'live'`，live 模式**刻意不給動作選項**（因為即時注入端點不經過 make_applier，給了就是騙人）。白軍側 `white-cell.vue:489-505` 有待命注入清單與扣發／跳過。


### [S] 演習專案全流程：建立／階段推進／整備勾稽／掛卸推演局／參數簽證與解除／稽核紀錄／封存包下載／銷毀模式

*統裁與治理*

**後端**：core/app/api/exercises.py 共 13 條端點（:37 list、:46 create、:56 get、:65 delete、:76 phase、:87 checklist、:98 attach、:109 detach、:120 audit、:129 bundle、:139 destroy、:153/:163/:173 seal 三態）。

**前端**：platform/app/components/ExercisePanel.vue 逐項都有入口：create-exercise(:189)、advance-phase(:325)、checklist-{key}(:268)、attach-session(:253)/detach-session(:234)、seal-params(:314)/unseal-params(:293)、**exercise-audit(:412) 有清單且把 actor_id 換成帳號名**、**download-bundle(:334) 有下載鈕**、destroy-open/destroy-submit(:359/:380，限 ADMIN + 名稱逐字確認)、delete-exercise(:341)。composables/useExercises.ts 對應 13 個函式。只有 `GET /exercises/{id}`（單筆）沒用到——列表已含全部欄位，不算缺口。


### [S] 推演參數（SimParams）調整

*統裁與治理*

**後端**：core/app/sim_params.py:57-112 的 SimParams dataclass 共 24 個欄位；:201 `to_config` 全部序列化；core/app/api/system.py:162-170 PUT /config 接受 `sim` 並在參數簽證期間擋改。

**前端**：platform/app/pages/system-settings.vue:276-397「推演參數」區塊逐欄有輸入框：移動三速+補給距離+偵測兩項(:284-304)、節奏與自主推演六項含 tick_rate_ms/pace_compression/comms_interval_ticks/ai_heartbeat_s/ai_max_orders/checkpoint_interval_ticks(:307-331)、環境與後勤(:333-342)、保真係數八項含壓制三項/乘駐車兩項/雷區三項(:363-389)、行軍耗損逐 profile(:391-396)。逐項比對後 24 欄全數可調（後勤 supply_daily_rates / repair_per_day 依指示排除不列）。


### [S] 白軍控制台核心操作：時間控制、視角切換、各軍自編權限、單位屬性/編裝編輯、事件流（含帳本序/tick/受眾）

*統裁與治理*

**後端**：core/app/api/control.py:105 PAUSE/RESUME/ROLLBACK；core/app/api/units.py:226 與 state.py:78、map_features.py:188、intel.py:62 的 `as_faction` 視角參數；core/app/api/orbat.py:113 單位編輯（含 :164 `_push_live` 推進活模擬熱狀態）、:197/:209 自編權限讀寫。

**前端**：white-cell.vue：pause/resume(:383-384)、viewpoint 下拉(:375)、perm-{faction} 勾選(:465-471 區塊)、UnitAttributeEditor + UnitOrbatEditor(:455-478 區塊)、wc-event-list 帶 seq/tick/受眾三欄(:520-536)。視角切換在 COP 也完整接線（cop.vue:373、stores/sessionStream.ts:59 連 WS 都帶 as_faction）。


### [S] 帳號與名冊：建帳號/改角色/重設密碼/刪帳號，以及指派陣營×角色×席位×指揮單位範圍

*統裁與治理*

**後端**：core/app/api/users.py 四條（list/create/patch/delete）；core/app/auth/schemas.py:41-58 UserView/CreateUserRequest/UpdateUserRequest 全部欄位。core/app/api/participants.py:118/:138/:197 名冊三條，AssignParticipantRequest 含 faction / role / seat_role / unit_scope 四個維度，:152 允許指派 WHITE_CELL（跨陣營）。

**前端**：platform/app/pages/accounts.vue:54/:67/:86/:102/:118 四條端點全用上（建立、改角色、重設密碼、刪除）。platform/app/pages/lobby.vue:447-512 名冊面板：陣營下拉(:455)、角色下拉(:464)、席位下拉(:470-479，ASSIGNABLE_SEATS 六種)、unit_scope 勾選(:254)。實測 `curl .../participants` 回的 factions 含 WHITE_CELL，前端下拉直接吃這份清單，故跨陣營/白軍指派做得到。lobby.vue:250-254 還特別處理了改 unit_scope 時要把 seat_role 帶回去，避免靜默清掉席位。


### [XL] 軍械庫 DRONE 類別的完整表單（drone_kind / 續航 / 巡航速度 / 升限 / 資料鏈距離 / 酬載 / 感測酬載 / 消耗性 / 天候上限）

*想定與編裝*

**後端**：契約 `contracts/weaponeering.schema.json` 的 `$defs.drone` 九個欄位齊備。⚠ 但 `rg -n 'DRONE' core/app/ -g '*.py'` → **零命中**；`endurance_ticks` / `data_link_range_m` / `service_ceiling_m` / `payload_kg` / `weather_limits` 在 core 全部零消費端。

**前端**：完整可編：`armory.vue:126-137` 九個 ref、`:329-336` populateForm 全讀、`:482-489` 全寫；`FORM_CATEGORIES`（:143）含 DRONE。


---

## 各視角整體觀察


### 端點 — 端點：後端有、前端沒呼叫

【整體判斷：端點層面幾乎是完整的，破的是「契約層」與「選項層」】

我用可重現的方式做了完整差集，不是抽樣：
1. 後端端點以**跑起來的服務**為準（`curl localhost:8000/openapi.json`），得 67 條路徑；不是靠數 decorator（`rg '@router\.(get|post|put|patch|delete)' core/app/api/` 只有 91 個 decorator，任務說明裡的 135 這個基準數字已經過期）。
2. 前端呼叫以**字面量抽取**為準：掃 platform/app 下所有 .ts/.vue（排除自動產生的 types/api.ts），把樣板字串的 `${...}` 正規化成 `{}`，得 68 條路徑。
3. 兩邊對差集，再逐條人工判斷是「真的沒 UI」還是「被聚合端點取代 / 刻意不做」。

**結論一：純粹的端點缺口只有一條。**
67 條後端端點裡，前端從未觸碰的業務端點**只有 `GET /sessions/{id}/aar/missions`**（`/healthz` 是探針不算）。這一條是真的有能力、curl 有資料、畫面零蹤影——本次盤點裡最乾淨的一個 MISSING。
另有兩條端點（`/intel`、`/relations`）雖然沒有執行中的呼叫端，但能力經 `/state` 聚合完整送到畫面，屬合理設計（core/app/api/state.py 直接呼叫這兩支的 handler 函式聚合，紅線 3 的 fog 過濾也留在後端）——留下的 `fetchIntel`/`fetchRelations` 是死碼，不是缺口。

**結論二：真正在漏的不是端點，是「契約」與「選項」兩層。**
- **契約層**：11 條實作端有、契約沒有（`_IMPL_ONLY`），涵蓋自主主控台、AAR 重播/報告/匯出、ORBAT 權限、地圖狀態編輯、LLM 連線測試——這五塊功能的前端型別**全部是人手寫的**。它們今天畫面上看得到，但後端一改欄位名就會靜默變空白，而所有閘門都是綠的。這正是這個 repo 招牌病的前端變體。反方向還有 7 條契約有、實作沒有（`_CONTRACT_ONLY`），前端拿得到型別按下去吃 404，其中 `/sessions/{id}/ledger` 對應的是 DB 裡 21 萬筆真實事件。
- **選項層**（我認為對操作員痛感最強）：端點都通、但前端沒把後端的能力**全部曝露成可選項**。三個具體案例：席位可下令型別（後端有權威表，前端下拉不過濾 → 送出才被 `ORDER_SEAT_DENIED` 擋）、C2 信文文別（後端收四種，前端只送得出 FREE_TEXT）、MSEL 待命清單（後端存整份腳本，前端只印得出 id 字串）。這三個都不是「功能沒做」，是「做了但操作員碰不到」。

**建議的修正順序**（以投入產出比排）：
1. `aar/missions` + 順手把 11 條契約漂移一起補（同一批契約工作）— S+M
2. 席位→令型過濾（S，零後端新端點，把權威表塞進 participants 的 me 區段即可）+ C2 文別下拉（S，純前端）— 兩條都是每場演習每個參謀都會撞到的日常摩擦
3. MSEL 完整腳本檢視（M）— 白軍控制台目前可用性最弱的一塊
4. Ledger 查詢端點（M）— 事後爭議裁決的最終依據
5. AI 稽核紀錄（M，**但先驗證 `AIInvocationLog` 目前 0 筆是不是代表寫入路徑在活執行期根本沒觸發**——若是，那是另一張後端卡，做前端等於做空表）
6. 插件狀態面板（M）— 價值真實但不常出事

**已排除**：後勤（supply / SUPPLY_POINT / 補給水位 / 整補）依指示未列入；`OrderType.RESUPPLY` 在下令面板的缺席同屬該範圍，未計為缺口。

**唯讀承諾**：本次未修改、未新增、未刪除任何檔案，未 commit，未 docker build。所有查證只用 rg / git / curl / 唯讀 SQL（`select count(*)`、`group by`）。

### 欄位 — 欄位：回應裡有、畫面上沒有

整體判斷：**這一塊是「兩極化」，不是均勻地破。**

完整的那一極很完整——AAR 統計（九欄全上）、C2 已讀/核覆、SessionSummary 的權限旗標、UnitView 的 readiness/is_fixed/stale_since_tick、ContactView 的 fidelity/echelon/branch/designation、MapFeatureView 的 attributes 深度使用（顏色/區類/寬度/sidc/viewshed_ring）。這些不只是「有讀」，而且讀進去之後真的改變畫面行為。凡是有一張任務卡「以前端可見為驗收條件」收尾的，欄位就接得很乾淨。

破的那一極集中在三個可預測的位置：

1. **令與申請單的「內容」欄位**——`OrderResponse.payload`、`OrderResponse.precheck`（既有指令）、`RequestView.params`、`MessageView.ref_id`。共同形狀是：後端把一包 `dict[str, Any]` 誠實地回出來，而前端沒有 renderer 去拆它。這一組加起來讓火協鏈（申請 → 核准 → 掛單射擊 → 檢討）在畫面上是斷的：核准者看不到申請內容、指令列看不到打哪裡、事後看不到為什麼被駁回。**若只排一件事，排這一組**——資料全在回應體裡，純前端工，成本 S–M。

2. **STATE_DIFF 的「進行中」欄位**——`posture_target` / `posture_since_tick` / `mounted` / `formation` / `column_spacing_km` / `footprint_m`。`broadcaster.py:151-166` 是 denylist，所以引擎每寫一個新熱狀態鍵就自動外送一個；而前端 `useLiveState.ts` 是白名單，只讀九個。**兩邊的成長方式相反，缺口只會愈開愈大**：C1、C3 這兩張卡的狀態欄位一寫進熱狀態就上了線，但沒人去 `useLiveState` 加對應的讀取器。建議把「新增熱狀態鍵時同步決定它的畫面歸屬（含『刻意不畫』）」寫進 HOW_TO，否則下一張卡會再犯一次。

3. **無人呼叫的整支端點**——`/aar/missions`。它同時也不在 `contracts/core_api.yaml` 裡，正好印證了任務描述裡那條規律：**契約沒有的端點，前端連型別都拿不到，於是永遠不會有人去接**。這條值得當成一個訊號去查 `_IMPL_ONLY` 漂移清單裡的其他端點。

一條**反向**的發現（不屬本視角但順手撞到，記在這裡）：`OrdersPanel.vue:57-66` 的 `phaseLabel()` 讀 `o.mission_phase`——契約 `contracts/core_api.yaml:1408` 宣告了這個欄位，但 `core/app/orders/schemas.py` 的 `OrderResponse` 根本沒有它。所以這是「前端畫好了、後端不填」，與本視角其餘各條剛好相反。前端註解自己寫著「後端一填就會自己亮起來」——那句話從寫下來到現在都還是真的。

關於校準例（`OrderResponse.issuer_id` / `payload`）：後端補欄那一半確實已完成，實測回應體都有。但我仍列了一條 item，因為**前端那一半一個消費端都沒有**（`rg issuer_id platform/app` 除自動生成的型別檔外零命中），原始的使用者症狀「AAR 上答不出這道令是誰下的、FIRE_MISSION 打哪裡也看不到」在畫面上原封不動。若上游已把這條算結案，請把它併掉；我列出來是因為排程表上還缺這張前端工的卡。

盤點範圍（供判斷覆蓋率）：`UnitView`(18 欄，扣除 C7 的 supply/starved_days)、`WeaponView`(9)、`OrderResponse`(12)、`PrecheckResult`/`PrecheckCheck`(6)、`ContactView`(9)、`MapFeatureView`(9)、`StateSnapshotView`(7)、`SessionSummary`(15)、`MessageView`(10)、`RequestView`(10)、`QuotaView`(3)、AAR 五支端點的全部回應欄位、以及 `broadcaster.public_diff` 實際會外送的熱狀態鍵集合（由各 `*_wiring.py` 的 `*_KEY` 常數反推）。C7 補給相關（`supply` / `starved_days` / `refit_tick` / SUPPLY_POINT）依指示全數排除，未查。

### 下令 — 下令：令型與參數的可操作性

整體判斷：**這一塊的骨架是完整的、破的是末梢**。九種令型裡真正該由人下的七種（MOVE/ENGAGE/FIRE_MISSION/POSTURE/MISSION/FORMATION/ENGINEER）COP 面板全都做得出來，令型層級沒有缺口；RECON 後端明確標為未實作、前端也確實選不到，沒有「選得下去卻沒反應」的狀況（`core/app/seats/__init__.py:23` vs `UnitsOrderPanel.vue:154-162`）。破的一律是**同一個 payload 裡的次要欄位**——後端 pydantic 收得下、引擎讀得到、測試綠、契約多半也寫了，就是面板沒有那個輸入框。

逐欄比對的結果（不含 C7 後勤）：後端 payload 欄位共 30 個，前端送得出 21 個，**送不出 9 個**——`FIRE_MISSION.ammo_type`(SMOKE)、`FIRE_MISSION.ttl_ticks`、`FIRE_MISSION.weapon_id`、`ENGAGE.fire_request_id`、`FORMATION.column_spacing_km`、`MISSION/DEFEND.orientation_deg`、`MISSION/MOVE_MARCH.spacing_km`、`ENGINEER.radius_m`（有 ref 但沒有 UI，永遠 200）、以及 `acknowledge_restricted` 在 FIRE_MISSION 路徑上沒有渲染點。九項裡有七項是 S 級，多半是「加一個下拉/數字輸入 + payload 條件帶一欄」。

三個值得注意的模式：
1. **契約缺欄＝前端連型別都拿不到**：`ttl_ticks` 在 `contracts/core_api.yaml` 零命中、`ENGAGE.fire_request_id` 也沒宣告（`PROGRESS.md:298` 已記過）。這兩項不是「忘了做 UI」，是漂移到前端根本不知道有這個能力。
2. **AI 比人類能下的令更完整**：tempo 過去只有 `ai_loop/orders_bridge.py` 送得出（已修，可當範本）；現在輪到 `orientation_deg`/`spacing_km`——`orders/decomposer.py` 自動展開時用得到，人手動下 FORMATION/MISSION 令卻編不了。同一局裡 AI 陣營的可用手段比人類多，而畫面上看不出為什麼。
3. **確認/管制類 UI 只綁在單一分支**：限制射擊區確認寫死在 ENGAGE 的 `v-else` 裡（`UnitsOrderPanel.vue:489`），FIRE_MISSION 因此變成絕對禁射。這類「共用 computed、單分支渲染」的寫法值得在 review 時特別看。

另外兩點超出逐欄比對但同屬「下令可操作性」：**破障令實際上下不了**（要手打障礙標註 id，地圖點選不會填），這讓 C2 障礙工兵在人類席位上只完成一半；以及**面板完全不看席位**（`cop.vue:148` 的 `mySeatRole` 只餵給 C2Panel），情報官/觀察員看得到七種令型、送出才被 `ORDER_SEAT_DENIED` 打回。

排程建議：一張「下令參數補完」卡（S 級九項的其中七項：SMOKE、限制射擊確認搬家、column_spacing_km、orientation_deg、spacing_km、engineer radius_m、FIRE_MISSION weapon_id）可以一起做完；`ttl_ticks` 與 `ENGAGE.fire_request_id` 因為要動契約單獨一張；破障選標的與席位篩選各一張 M。

依規定，`RESUPPLY` 令型（後端 `seats/__init__.py:47` 已指派給 S4_LOG、前端下拉沒有這個選項）屬於 C7 後勤範圍，未列入 items。

### 事件回饋 — 事件與回饋：發生了但畫面不說

【整體判斷：這一塊是「地基好、最後一哩斷了」——不是壞的，是差一層轉發】

首先要更正任務簡報的前提。「後端發出 124 種 event_type、前端 EVENT_LABELS 只有 78 條」這個數字**不成立**，它來自一個壞掉的指令：`rg -oh` 的 `-h` 在 ripgrep 裡是 `--help`（no-filename 是 `--no-filename`），所以那 124 行其實是 ripgrep 的說明書。實測後端 **47 種**、前端 **52 條**（含兩條刻意排除的），`core/tests/unit/test_frontend_event_labels.py` 用 AST 雙向鎖死（6 passed）。**事件型別的中文化沒有缺口，這個方向不必排修正。** 同理，`PRECHECK_LABELS` 的 20 條與 `precheck.py` 的 20 個 check name 完全對齊，UnitsOrderPanel.vue:556-557 連後端組好的中文 detail 都渲染出來了——下令被拒的逐項理由也是完整的（只是沒有測試守著，將來會漂）。

真正的病在**另一個維度**：不是「這個事件叫什麼」沒翻譯，而是「這個事件講了什麼」沒送到。系統裡有兩條事件下行路徑，命運完全不同：
- 走 `core/app/stream/publish.py:publish_event` 的（白軍控制、即時注入、C2 信文）——`{event_type, **payload}` 整包下發，前端要什麼有什麼。這些功能的回饋都是好的。
- 走 `core/app/state/broadcaster.py:build_event_envelope` 的（引擎裁決出來的所有戰場事件）——被一份 15 鍵白名單過濾，且 **`LedgerEvent.detail` 一個字都不轉發**。

回報的那條線索**查證屬實，而且比回報的更嚴重**：`detail` 不只在 WS 上被丟掉，`core/app/aar/export.py:36-48` 的匯出也沒有它（活體 curl 驗證：匯出 185 筆事件的鍵集合裡沒有 detail）。也就是說油料殘量、卡住的地形格、觸雷的障礙名稱、行進耗損是行軍磨的還是硬穿付的代價——**沒有任何操作員能取得的路徑**，即時看不到、AAR 看不到、匯出檔也沒有。最能說明問題的證據是 useCopFeed.ts:127-154 的 `REASON_LABELS` 裡有五條翻譯（OUT_OF_FUEL、IMPASSABLE_TERRAIN、MARCH、FORCED_CROSSING、TARGET_GONE）**在 feed 上永遠不可能被觸發**，因為那些 reason 全住在 detail 裡。有人認真寫了翻譯，只是線沒接上。

第二類病是**落帳與廣播被當成同一件事**。`ORDER_REJECTED`、`ORDER_RESTRICTED_FIRE_OVERRIDE`、`REQUEST_SUBMITTED`、`REQUEST_DECIDED` 四種事件的 sink 是 `LedgerWriter`（core/app/api/deps.py:144、core/app/api/c2.py:542），而 `core/app/state/ledger.py` 全檔沒有一行 redis——它們只寫 DB，永遠不進串流。前端四條中文標籤備好了，一次都不會被渲染。反過來，白軍的即時注入（core/app/api/inject.py:48-56）是另一半：只推串流不落帳，於是統裁手動打進戰場的狀況在 AAR 裡完全不存在（該檔 docstring 自己寫「Ledger + WS stream」，實作只做了後者）。這兩者合起來就是這個 repo 的招牌病在事件層的變體。

第三類是**回饋的載體本身不堪用**。戰況事件小工具沒有 tick、沒有序號、沒有篩選、只留 20 則（cop.vue:586-588），而白軍控制台的同一份 feed 三樣都有（white-cell.vue:297,306-325）——能力做出來了，一般席位沒有。更致命的是 `SENSOR_CONTACT` 不在 `_FEED_EXCLUDE` 裡，活資料顯示一場推演可以有 35347 則（同場交戰類不到 10 則），20 則的視窗會被同一件事洗光；再加上新客戶端不補送 ring（core/app/stream/backfill.py:25-27），按一次 F5 整個戰況欄就歸零。**這三條不先修，前面所有「把事件說得更清楚」的改動都看不到效果。**

排程建議（三波）：
1. **先修載體與最便宜的線**（各 S–M，一週內可清）：SENSOR_CONTACT 洗版、AAR 頁的裸英文代號（aar.vue:143,180，半天）、`aar/export.py` 補 detail（一行）、MISSION_ENDED 的 phase、重播書籤的死型別、C2 核覆推錯事件。這一波的共同點是改動小、驗證容易、每一條都直接對到操作員當天會遇到的畫面。
2. **再修兩條轉發線**（M）：`detail` 白名單轉發 + 前端專屬敘述（注意 `lat`/`lng` 不可轉發，會穿透 fog of war）；`ORDER_REJECTED`／`REQUEST_*` 同時落帳與推串流；新客戶端補送 ring。
3. **最後做可解釋性**（L）：交戰係數（p_hit/地形遮蔽/天候/壓制/姿態）與 AI 推理鏈的呈現。這兩項工作量最大、且需要先想清楚迷霧規則（誰能看敵方的命中率計算、誰能看 AI 的思路），不適合現在動。

最後一個觀察，給下一個接手的人：這個子系統**已經有一道很好的門**（test_frontend_event_labels.py 的 AST 掃描，同時看得到前後端兩邊），但它只守「型別有沒有中文」這一條不變式。本次查到的每一個缺口都在它的守備範圍之外——白名單漏鍵、detail 不轉發、AAR 頁另一條渲染路徑、事件只落帳不廣播。修正這些的同時，值得把那道門的守備範圍一起擴大，否則同樣的洞會在下一個子系統重新長出來。

### 想定與編裝 — 想定與編裝：schema 有、編輯器編不了

整體結論：**這一塊是「頂層設定已補齊，資產層仍是破的」**。

過去一輪明顯有人系統性地補過想定的**頂層**設定——五項想定設定（E6）、戰場範圍/tick/彙整層級（E7）、mode 未實作的誠實化標示、passthrough 結構性保真——都做完了，而且做得很有紀律（每個控制項下面必有一行「開了會怎樣」，`MODELLED_SCENARIO_KEYS` 還防了「UI 關掉、passthrough 舊值復活」）。所以 `PROGRESS.md` Backlog 裡的 [E6/E7] 條目已經過期，排程時要先把它劃掉，否則會重複開卡。

破的是**資產層**，而且破法很一致：凡是「不在 `bundle.scenario` 這個 dict 裡的東西」，編輯器一律不認——
- bundle 的兄弟鍵：`roe`、`overrides.mobility_matrix` → 完全沒 UI；
- unit 的非建模鍵：`equipment` → 完全沒 UI。

這三者不只是缺 UI，而是**會被靜默刪掉**：`importScenario` 的參數型別只有 `{scenario, orbat, msel}`（`useScenarioEditor.ts:335-339`），`exportScenario` 的回傳也只有這三段（`:217-221`），而 `GET /scenarios/{id}` 回的是完整 bundle。所以任何人用編輯器打開一份出貨想定（四份 example **全部**有 roe.yaml + overrides/mobility_matrix.json + 大量 equipment）改個名字存回去，交戰規則、地形覆寫、全部單位的武器彈藥就沒了，畫面顯示「已存到伺服器」，零警告。這正是 repo 招牌病的前端變體，而且是最惡性的那一種——不是「做不到」，是「把已經做好的東西刪掉」。

**排程建議**：先做 roundtrip 保真（S，純 composable，一天內），把三條資料先止血；再做 ROE section（M）與 ORBAT 編裝（L，這是「劇本編輯器能不能獨立產出一份能打的想定」的分水嶺）；機動覆寫（M）可與 ROE 併卡，因為兩者共用同一段 bundle export/import 改動。條件 DSL 補 manual/after_ticks_of/held_for/contact_established（M，可先出四種簡單的）CP 值高，因為白軍控制台的「扣發／跳過」按鈕已經做好了，只差劇本產不出對應的 manual 事件。display_name 輸入框與軍械庫油料三欄各是一天內的小改，順手做掉。

另外記一筆**反向的病**（不是前端缺口，是後端缺口）：軍械庫的 DRONE 整組九欄在 `core/app/` 零消費端（`rg 'DRONE' core/app/ -g '*.py'` 零命中），`rate_of_fire_rpm` / `penetration_type` / `guided` / `reload_ticks` / `countermeasure_resistance` 同樣沒有任何解析器讀。前端表單長得很完整，使用者填了、存了、推演時毫無影響。這是 PROGRESS.md [H2] 的範圍，屬保真卡；短期最划算的做法是比照編輯器對 WEGO/IGO_UGO 的處理（`scenario-editor.vue:35` 的 `UNIMPLEMENTED_MODE_SUFFIX`），在這些欄位加「（尚未接入推演）」標示——先誠實，再補功能。

（依指示，`supply` / `supply_points` / 補給水位 / 整補 一律未列入。）

### 統裁與治理 — 統裁與治理：後端有權限、前端沒入口

整體判斷：**這一塊是本專案最完整的一塊，但完整的是「動作」，破的是「回饋」。**

三個具體觀察：

1. **寫入路徑幾乎沒有缺口，讀取路徑到處是洞。** 演習專案 13 條端點前端露出 13 條（含使用者特別點名的 audit 與 bundle，兩者都有入口）；SimParams 24 欄全部可調；白軍的暫停/續行/回滾/注入/視角/自編權限/單位編輯全部接得到。但只要是「後端算出來、要給人看」的東西就會斷：`/metrics` 零消費端、`AIInvocationLog` 連端點都沒有、`total_submitted` 只存在於自動產生的型別檔裡、暫停狀態沒有任何權威來源。這正是專案招牌病的前端變體——**存得進去、算得出來、測試全綠、畫面上看不到**。

2. **最痛的三件事都不是「沒做」，是「差最後一哩」。** MSEL 待命注入（後端只發編號、不發內容）、回滾點挑選（後端一次回 3799 筆、前端塞進一個原生 select）、暫停狀態（Redis 有鍵、沒有 GET）——三者的引擎都已完成並通過測試，卡在最後一層的資料形狀上。這類修正 size 都在 S–M，投報率遠高於新功能。

3. **白軍專用面板是契約漂移的重災區。** `_IMPL_ONLY` 六條裡有六條全在本視角範圍內（autonomy ×3、orbat-permissions ×2、test-llm ×1），對應的前端型別全是手抄的。`ai_ground_truth` 已經因此出過「白軍設了存不進去」的事故（autonomy.py:58-61 的註解就是那次的疤）。統裁面板是最少人天天用、最晚被發現壞掉的地方，卻剛好是型別保護最弱的地方——這個組合遲早還會再咬人一次。

排程建議（僅供參考，不含後勤 C7）：先做 S 級的暫停狀態顯示與 total_submitted（各半天到一天，直接解決「以為系統掛了」這類誤判），接著 M 級的系統健康出口與 MSEL 清單帶內容，然後補契約漂移六條，最後才是 L 級的 AI 稽核檢視與臨機裁決注入。
