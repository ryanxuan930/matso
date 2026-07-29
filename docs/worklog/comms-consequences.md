---
task: "WP-C5 通聯後果閉環：位置凍結與敵情粗化"
status: DONE
started: 2026-07-29T23:55+08:00
updated: 2026-07-30T01:20+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-C5（V2.0 路線第六張）；SPEC_FULL §6.2「MUST enforce」；contracts/ws_protocol.md
---

# WP-C5 通聯後果閉環：位置凍結與敵情粗化

## 目標摘要
SPEC_FULL §6.2 的三種戰術後果只做了一種：`order_admissible`（OFFLINE 收不到新令、DEGRADED 延遲）
已於 #33b 接進 movement/adjudicator；`position_report_*`（斷聯單位在**己方** COP 位置凍結）與
`intel_granularity`（DEGRADED → 敵情粗化）**定義了但沒有任何消費者**。本卡把兩者接到投影層：
資料照常演進，只是「指揮所看不到」。

## 開工掃描（規格四項之外發現的東西）

1. **STATE_DIFF 完全沒有陣營受眾**（最嚴重）。`build_state_diff_envelope` 產出的信封沒有
   `faction`/`factions` 標籤 → `stream/faction_filter.is_visible` 對所有人回 True →
   **每個連線的 client 都收到全部單位（含敵軍）的即時 lat/lng/health/fuel/ammo**。
   前端只是「沒把不認識的 unit 畫出來」而已（單位清單來自 `GET /units`），開 devtools 就看得到。
   這是**紅線 3**（fog of war 過濾只能在後端）的違反。
   與本卡的關係：規格要求「STATE_DIFF 的**己方視角**投影中 OFFLINE 單位凍結」——
   一個廣播給所有人的信封**根本沒有「己方視角」可言**，不先做 per-faction 投影就做不了凍結。
2. **`TacticalUnit.comms_status` 播種後從未被寫過**。`CommsSystem` 只寫熱狀態的 `comms_state`，
   而 `GET /units` 回的是 DB 欄位 → 重新整理/重連後通聯狀態一律顯示播種值（ONLINE）。
3. **`cop.vue` 的 `currentTick` 是寫死的 `ref(100)`**，從未被更新。地圖上的「OFFLINE +Nt」
   與敵情老化淡出（`stalenessOpacity(currentTick - lastSeenTick)`）都是拿假 tick 算的。
   前端的虛影渲染（opacity 0.4 + `OFFLINE +Nt` 標籤）其實**早就寫好了**，只是餵給它的
   `comms` 是 DB 陳值、`lastReportedTick` 是常數 100——功能在，資料是假的。
4. `IntelContact` **沒有觀測者單位欄位**（只有 `faction`）。故敵情粗化只能做規格寫的
   「本陣營整體 comms 姿態」，做不到更真實的「該筆情報的回報者斷聯 → 該筆凍結」。記 backlog。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| `contracts/core_api.yaml` | 修改 | `UnitView.stale_since_tick`、`StateSnapshotView.comms_posture`、ContactView 粗化說明 |
| `contracts/ws_protocol.md` | 修改 | 新增「受眾標籤」表（含 `exclusive`）與「STATE_DIFF 的每陣營投影」節 |
| `core/app/comms/consequences.py` | 修改 | `REPORT_*_KEY`、`position_report_due`、`last_position_report`、`project_position`、`faction_link_state` |
| `core/app/engine/comms.py` | 修改 | **產出端**：依 `position_report_interval` 落 `report_lat/lng/tick` |
| `core/app/state/comms_view.py` | **新增** | 投影層用的唯讀熱狀態切片（單次 MGET；Redis 掛→空視圖） |
| `core/app/state/hot_state.py` | 修改 | `unit_key()` helper（Kernel 外的唯讀取用者共用，勿另寫字面值） |
| `core/app/api/units.py` | 修改 | 陣營視角套位置凍結；`comms` 改讀熱狀態；`_visible_factions` 改用共用規則 |
| `core/app/api/intel.py` | 修改 | `faction_posture` / `faction_granularity` + 粗化傳入 service |
| `core/app/api/state.py` | 修改 | 快照補 `comms_posture`（沿用 `/intel` 那條路算出來的姿態） |
| `core/app/intel/service.py` | 修改 | `visible_contacts(..., granularity)`；h3 res-6 量化 + fidelity 上限 DETECTED |
| `core/app/state/broadcaster.py` | 修改 | `public_diff` / `project_diff`；`_envelopes` 發 N+1 份信封 |
| `core/app/stream/faction_filter.py` | 修改 | `exclusive` 受眾語義（關掉全知旁通） |
| `core/app/factions/visibility.py` | **新增** | 「自己＋盟軍」規則的**唯一**純函數（REST 與 STATE_DIFF 共用） |
| `core/app/sim_runtime.py` | 修改 | broadcaster 接上 observers / visible_for / state_for |
| `core/app/engine/sensor_wiring.py` | 修改 | `SensorResolver.factions()` |
| `core/app/ai_loop/world_view.py` | 修改 | `projected_snapshot`、`faction_granularity`、`allied_units(..., snapshot)` |
| `core/app/ai_loop/worker.py` | 修改 | 取快照後**立刻**投影；`EnemyVisibility` 協定加 `granularity` |
| `core/app/ai_loop/context.py` | 修改 | briefing 標示「通聯 X：新令無法/延遲送達，位置為 tick N 的最後回報」 |
| `platform/app/pages/session/[id]/cop.vue` | 修改 | `liveStaleTick`、`currentTick` 改接串流、單位卡座標標註、敵情粗化告示 |
| `core/tests/unit/test_comms_projection.py` | **新增** | 26 條（純函數 + 廣播器 + 受眾標籤 + CommsSystem 產出端） |
| `core/tests/unit/test_comms_api_projection.py` | **新增** | 11 條（REST 三端點 + 視角語義 + 降級） |
| `core/tests/unit/test_ai_comms_consistency.py` | **新增** | 8 條（AI 與人看同一張圖） |

## 設計決定

### 1. 凍結的是**視野**，不是單位（本卡最重要的一條）
`report_lat/lng/tick` 是**新增**的熱狀態欄位；真實 `lat`/`lng` 照常由 movement 每 tick 演進。
若圖省事直接把熱狀態的座標凍住，射程/LOS/移動裁決會一起被騙——那不是迷霧，是改物理。
測試檔開頭把這條寫成不變量：任何測試若能靠「改熱狀態的 lat/lng」通過，就是實作錯了。

### 2. 沒有回報 ≠ 退回真實位置
`project_position` 在「非 ONLINE 且無任何回報」時回 `(None, None, None)`＝位置不明。
REST 回 null，STATE_DIFF **移除** `lat`/`lng`（不是送 null——送 null 會把 client 上最後已知的
位置清掉，單位憑空消失；移除才是「保留最後已知」的凍結語義）。
為了讓這個分支近乎不可達，`CommsSystem` 對**還沒有任何回報**的單位一律先落一筆——
部署位置本來就是指揮所知道的，開局即失聯的部隊不該一開始就「位置不明」。

### 3. 白軍的兩種視角語義不同
god view（全知且未指定 `as_faction`）→ 真實位置、不粗化。
指定 `as_faction=X` → **要**凍結、**要**粗化：那是在問「X 看得到什麼」（與 O7.4 視角語義、
WP-E3 的快照視角一致）。驗收條文「白軍視角照動」指的是前者。

### 4. STATE_DIFF：N+1 份信封 + `exclusive` 受眾
每個有單位的陣營各一份已投影的副本（`factions:[F]` + `exclusive:true`），外加一份真實副本
（`factions:[]`，只有全知旁通收得到）。`exclusive` 是必要的：全知若照舊旁通，會同時收到
N 份互相矛盾的副本（有的凍結有的沒有），先到先贏。
代價：ring buffer（5000 條）的回補視窗被 N+1 除。三陣營局約剩 1250 tick，仍遠大於實際重連需求，
且 WP-E3 已經把 RESYNC 全量重同步做出來了。

替代方案（單一信封 + 在 WS fan-out 時逐 client 過濾）被否決：WS handler 認證後**立刻關掉 DB
連線**（每條 WS 長握一個 pool 連線會在數條併發時耗盡 pool），拿不到陣營/關係/熱狀態；
而 broadcaster 在 Kernel 行程內，這三樣都是現成的。

### 5. 「自己＋盟軍」只留一份實作
`GET /units` 與 STATE_DIFF 投影都要判可見集。抽成 `factions/visibility.visible_factions`
（純函數，資料來源由呼叫端給：REST 給 DB 查詢結果、sim_runtime 給已載入的 resolver/relations）。
兩份實作就是兩份會漂移的 fog of war。

### 6. 陣營姿態的門檻：全斷才 OFFLINE
`faction_link_state`：**全部** OFFLINE → OFFLINE（沒有新觀測進得來，圖凍結）；能即時回報的
不到半數 → DEGRADED；否則 ONLINE。全員 DEGRADED 落在 DEGRADED——那些單位仍在回報，只是慢，
稱不上凍結。0.5 是 v0 門檻，與模組既有的 `DEFAULT_DEGRADED_DELAY_TICKS = 3` 同紀律。

### 7. 粗化必須連身分一起收回
只把座標量化到 h3 res-6、卻留著 `designation`/`faction`，等於 `fidelity` 欄位與內容不符。
故降級的 fidelity 在**揭露判定之前**就算好（`IntelService._project`），且 `error_radius_m`
放大到該解析度的六邊形邊長——只換座標不放大誤差圈，是謊稱「我對這個格心有公尺級把握」。

### 8. AI 走同一份投影，且**告訴它後果**
`worker` 取快照後立刻 `projected_snapshot`，之後 own_units / allied_units 全吃投影後的世界；
敵情粒度由同一份 `faction_link_state` 導出。另外在 briefing 明寫「通聯 OFFLINE：新令無法送達，
位置為 tick N 的最後回報」——不講的話，LLM 會對一支聽不見命令的部隊反覆下令，還以為位置是即時的。

## 測試證據
- 新增 **45 條**；`uv run pytest` → **1289 passed / 8 skipped**（原 1244）；golden 6 未破
  （投影只在 RedisBroadcaster，golden 走 NoOp/Collecting broadcaster）。
- `ruff check` / `ruff format` / `mypy`（213 檔）/ OpenAPI 驗證 / schema-sync（16 表 144 欄）全綠。
- 前端 `npm run lint` + `vue-tsc --noEmit` 綠。
- **容器實測**（`docker compose up -d --build core frontend`）：
  - `/openapi.json` 確認 `UnitView.stale_since_tick`、`StateSnapshotView.comms_posture`（8 欄全 required）。
  - 真實 3 陣營局（36 單位）跑 `_envelopes`（唯讀腳本，不寫 Redis/DB）：
    輸出 4 份信封＝1 份真實（6 單位、lat 99.0）+ BLUE/RED/YELLOW 各 1 份
    （各 2 單位、只含己方、斷聯者座標為回報值 23.0、`exclusive=True`）。
  - **產出端在正式環境已生效**：`e2e-orders` 局的熱狀態出現
    `report_lat/report_lng/report_tick`，且 `report_tick` 等於當前 tick（28100）
    ——ONLINE 每 interval 回報的節奏與規格一致。
  - COP 頁面載入無 console error；牆鐘顯示 T1185（`currentTick` 改由串流供給後仍正確）。
- **未做端到端目視**：現有活局沒有失聯單位，要造一個就得推進使用者的推演局，故未做。
  凍結的呈現由單元測試（REST 三端點 + STATE_DIFF 投影）與上述容器實測覆蓋。

## 未做 / 已知限制
- **敵情粗化是陣營層近似**。更真實的是「該筆情報的回報單位斷聯 → 該筆凍結」，但
  `IntelContact` 沒有觀測者欄位（記 PROGRESS backlog）。因此 `IntelGranularity.FROZEN`
  目前與 COARSE 的投影效果相同（都是量化 + 降級），差別只在 `comms_posture` 顯示的字。
- 凍結單位的陣營副本**每 tick 重送同一組座標**（投影無狀態，不記「上次送過什麼」）。
  覆寫式語義下無害，但確實有冗餘流量。
- 白軍**切了視角之後**收到的 live STATE_DIFF 仍是真實副本（全知身分），只有快照是該陣營視角。
  白軍本就有權看全部，但這代表「用視角切換驗證某軍看到什麼」在**即時**畫面上不完全準確。
- `/units` 與 `/intel` 各自建一個 Redis client（沿用 WP-E3 `/state` 的做法），每次請求一次
  TCP 連線。以目前輪詢頻率無虞，但連線池共用值得另開一卡。
- `GET /units` 的座標仍取自 DB（movement 每 tick 落盤），不是熱狀態；god view 因此有最多
  一 tick 的落後。本卡未動（前端的即時位置本來就走 STATE_DIFF）。

## 中斷續作指引
- **本卡已全部完成並實測**。無未竟項。
- 後續相關：V2.0 剩 G1（cop.vue 拆分）、D6.1（AAR 地圖重播）。
