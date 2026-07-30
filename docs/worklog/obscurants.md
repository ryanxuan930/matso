---
task: V2.1 WP-C4c
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C4c 煙幕

## 目標摘要

[JCATS-A p.19]：煙幕是化學兵的標準配屬，作用是阻視線。發煙任務（`FIRE_MISSION` +
`ammo_type=SMOKE`）在落點生成煙幕，交戰與偵測的 LOS 判定疊上去。

## 三個決定了整體形狀的裁決

**1. 煙幕是雙面的，所以 `blocks_los()` 不知道誰是誰。** 規格明寫這一點。放煙的一方同樣
看不穿自己的煙——那正是煙幕在戰術上要付的代價（掩護退卻的煙也擋住你自己的觀測）。
任何帶 `faction` 參數的版本都會把這件事弄丟，所以有一條測試直接檢查**簽名裡沒有陣營**。

**2. 煙是 LOS 的布林覆寫，不是一個係數。** 煙不是「讓你看得比較模糊」，它是遮蔽。
做成 0.3 之類的係數會讓「隔著煙幕狙擊」變成機率低但可行的事，而那不是煙幕存在的理由。
地形 LOS 已經是布林，煙疊在它後面用同一個語義，兩者也就不會互相打架。

**3. 煙存成 `MapFeature(kind="SMOKE")`，不進熱狀態。** 熱狀態是 **unit 鍵值**的；
硬塞成 pseudo-unit 的代價是每一個 `hot.get_all()` 的消費端（sensor sweep、AI context、
broadcaster、`compute_state_hash`）都得學會忽略它——四處要改、漏一處就出事。
存成 MapFeature 免費得到持久化（重啟/checkpoint 自動涵蓋）、既有的標註載入機制、
前端本來就會畫 MapFeature。**消散不需要每 tick 寫回**（到期資訊在 attributes，判定是純比較），
所以煙也不會在 STATE_DIFF 上製造每 tick 的雜訊。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/adjudication/obscurants.py | 新增 | 純幾何：`SmokeCloud`、`blocks_los`（不看陣營）、`duration_ticks` |
| core/app/movement/attrition.py | 修改 | `_dist_point_to_segment_m` → **公開** `dist_point_to_segment_m`（單一份幾何） |
| core/app/engine/smoke_wiring.py | 新增 | MapFeature 存取 + `SmokeCache`（逐 tick 一次 query） |
| core/app/engine/engage_wiring.py | 修改 | 地形 LOS **之後**疊煙；間瞄不受影響 |
| core/app/engine/sensor_wiring.py | 修改 | 同上，且**只對 `needs_los` 的感測器**生效（雷達穿得過煙） |
| core/app/engine/fire_wiring.py | 修改 | `ammo_type=SMOKE` → `_emplace_smoke()`（扣彈但不走殺傷裁決）+ `SMOKE_EMPLACED` 事件 |
| core/app/orders/schemas.py、contracts/core_api.yaml | 修改 | `FireMissionPayload.ammo_type` |
| core/app/sim_runtime.py | 修改 | `SmokeCache` 注入兩條 LOS 路徑（回呼而非值） |
| core/tests/unit/test_obscurants.py | 新增 | 13 條 |

## 測試證據

- `uv run pytest -q` → **1844 passed, 8 skipped**；`core/tests/replay` → **8 passed（golden 未重錄）**
- ruff / mypy(258) / schema-sync / buf / 前端 lint+typecheck → clean（**無 DB migration**——煙重用 MapFeature）
- 突變測試：M1 點到直線而非線段、M2 過期照樣擋、M3 發數不影響時長、M4 弄丟雙面性、
  M5b 座標非數值不略過 → **全部轉紅**。

## 決策與陷阱

**幾何重用既有那一份。** 判定就是「煙心到視線線段的最短距離 <= 半徑」，而
`movement/attrition` 早就有含 cos-lat 修正的實作。把它公開而不是另寫一份——
兩份幾何必然漂移（WP-C2 的 `obstacles_at` 也是同一個理由）。
順帶釘住「**線段不是無限直線**」：射手背後的煙擋不到他往前看，用點到直線的距離會誤判。

**`smoke_for` 是回呼不是值**（同 WP-C4a 的 `light_for`）：`env_for` 閉包在 Kernel 建構時
就固定了，傳一份清單進去會讓整局停在建立時的那一刻。

**發煙不抽落點散布。** 煙幕的戰術意義是「這一片看不見」，一團 150 m 的煙本來就涵蓋 CEP
等級的誤差；為它抽 Rayleigh 只會多動一次 RNG（**擾動後續所有隨機序列**）卻不改變任何
可觀測結果。

**雷達/聲學/SIGINT 穿得過煙**（只對 `needs_los` 的感測器生效）——把它們也擋掉等於把煙幕
當成電磁屏障。

**⚠ 我的髒資料測試第一版是假的。** 只放了一列同時「幾何壞掉 + 缺到期 tick」，於是拿掉
任一個 guard 都照樣綠（另一個接住了）。改成每種壞法各一列後才分得出來。
另誠實記一筆：把「缺 `expires_at_tick` → None」改成「→ 0」**殺不掉，而且那是對的**
——0 代表「已於 tick 0 到期」，結果完全等價。那是等價突變不是測試漏洞。

## 中斷續作指引

- **下一步第一件事**：C4b（天氣 tick 化 + REPLAY 模式）是 C4 最後一塊。
- **未竟項**：
  1. **風向漂移未做**。規格寫「隨 weather wind 向量漂移」，但 `CellEffects` **沒有風場**
     ——要做得先擴 weather 契約（C4b 一起處理較合理）。目前煙原地不動直到消散。
  2. **前端沒有畫煙**：MapFeature 有進圖層機制，但 `kind="SMOKE"` 沒有半透明圓的樣式，
     也沒有隨剩餘 tick 淡出。
  3. **COP 下不了發煙任務**：`ammo_type` 在契約與 payload 都通了，但火力任務面板沒有彈種選擇。
  4. 過期煙的 `purge_expired_smoke()` 寫好了但**沒有掛進 pre_tick**——過期煙不影響正確性
     （`active_at` 判掉了），只是 row 會累積。掛的時候要注意它要 commit。
