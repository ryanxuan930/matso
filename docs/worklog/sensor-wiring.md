---
task: "#97 感測器接線（sim_runtime 取代 NoOpSensorSystem）"
status: DONE
started: 2026-07-28T13:20+08:00
updated: 2026-07-28T14:10+08:00
agent: Opus 5
spec: SPEC_FULL §7.2（偵測模型）、§12.1（關係矩陣）、§13.3（fog of war）
---

# #97 感測器接線：讓 fog of war 真的有東西可看

## 為什麼這張卡插在 #90 前面
使用者要「白軍可切各陣營視角並套用戰場迷霧」（#90）與「友軍/敵軍用對應 2525 符號」（#91）。
動工前盤點發現 **fog of war 目前是壞的，而且是壞在最底層**：

- `sim_runtime.py:225` 用 `NoOpSensorSystem()` → 從來沒有任何偵測發生。
- 實測 `IntelContact` 全表 **0 筆**。
- 一般陣營指揮官登入 COP：`/units` 只回己方 → `realAsContacts` 過濾後為空 → **完全看不到敵人**。
- 白軍：`myFaction` 為空 → cop.vue:376 「全部以友軍呈現」→ 三個陣營都畫成同一種友軍符號
  （使用者截圖裡 BLUE/RED/YELLOW 都是黃色菱形，就是這個原因）。

所以若照原訂順序先做 #90，切到 BLUE 視角會是「看得到自己、敵人一片空白」；#91 的敵軍符號
更是無從呈現。使用者選擇先補這段。

## 關鍵發現：偵測程式碼其實全都寫好了
只是沒接上。既有且已有單元測試（`test_sensor.py`/`test_sweep.py`）：

| 檔案 | 內容 |
|---|---|
| `intel/sensor.py` | `SensorProfile`（sensor_kind/max_range_m/detect_curve）、`detect_probability`（距離衰減×LOS×天氣×訊號×隱蔽）、`fidelity_for`（機率→DETECTED/CLASSIFIED/IDENTIFIED） |
| `intel/sweep.py` | H3 k-ring 空間預過濾的掃描；**關係矩陣過濾**（己方/ALLIED 不成 contact）；迭代順序固定→確定性 |
| `intel/store.py` | per-faction upsert（位置取最新、fidelity 取歷來最佳，不降級） |
| `intel/sensor_system.py` | Kernel 接線層 `SensorSweepSystem`（讀熱狀態→sweep→落庫→發 SENSOR_CONTACT 事件） |

缺的只有「把三個 lookup 餵進去」+ sim_runtime 換掉 NoOp。做法比照 #33 CommsSystem 取代 NoOp。

## 計畫
- [x] `engine/sensor_wiring.py`：`SensorResolver`（裝備→SensorProfile；**內建基本目視**）
- [x] `make_detect_env`（地形 LOS + 天氣，比照 `make_engage_env`）
- [x] `sim_runtime` 換上 `SensorSweepSystem` + `DeterministicRNG(seed, "sensors")`
- [x] 掃描節流 + 同陣營重複觀測收斂（實測發現的效能問題，見下）
- [x] 測試 9 條 + gates + 容器實測

## 執行紀錄
- `13:20` 盤點完成，確認 golden replay **不受影響**——`core/tests/replay/scenarios.py:72,169` 用的是
  自己的 `NoOpSensorSystem()`，不經 sim_runtime，故本卡不需重錄 golden。
- `13:35` 發現 `SEED_SENSORS["EO_DAY"]` 已存在（OPTICAL/4km，且已對 schema 驗證），
  遂**改用它**當內建目視，刪掉自己手寫的平行常數——同一件事不要有兩個真相。
- `13:50` 接線完成，準備實測前先算單位間距：**4km 內跨陣營配對達 429 組**（該局單位相距僅數十公尺）。
  tick = 1 sim 分鐘、實跑約 0.5s/tick → 每 tick 429 次 SELECT+寫。加上節流與收斂（見決策）。
- `14:00` 實測擋在「原局 tick 凍結」：該 session 的 Redis `matso:sim:...:concluded=1`
  （O11.5 勝負引擎已收場、runner 不重啟）。改**複製一局**來測，不動使用者原局資料。
- `14:05` 實測通過（見下），測試副本已封存+刪除、41 個 Redis 殘鍵清空、確認原局零殘留。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| `core/app/engine/sensor_wiring.py` | 新增 | `SensorResolver`（裝備→感測器，取射程最遠者；無裝備→內建目視）、`make_detect_env`（地形 LOS + 依感測器種類的天氣修正） |
| `core/app/intel/sensor_system.py` | 修改 | +`relations` 注入點、+`interval_ticks` 掃描節流、+`_best_per_target` 收斂 |
| `core/app/sim_runtime.py` | 修改 | `NoOpSensorSystem()` → `SensorSweepSystem(...)` |
| `core/tests/unit/test_sensor_wiring.py` | 新增 | 9 條：內建目視、裝備覆蓋、壞資料不致盲、非感測裝備不誤認、LOS 退化、收斂 |

## 測試證據
- `uv run pytest` → **1067 passed / 8 skipped**（含 **golden 6 未破**）；ruff/mypy(202) 全綠。
- **容器實測（複製局，36 單位／三陣營）**：

  | 觀測方 | 看到 | 說明 |
  |---|---|---|
  | BLUE | RED 13、YELLOW 10 | |
  | RED | BLUE **12**、YELLOW 10 | 有 1 個 BLUE 單位尚未被偵獲 |
  | YELLOW | RED 13、BLUE **12** | 同上 |

  **自看自己的違規筆數 = 0**（fog 不變式）。`GET /intel?as_faction=` 三陣營分別回 23/22/25 筆、
  全知 god view 70 筆（＝23+22+25）；contact 欄位**不含 `target_unit_id`**（去識別化未破）。
  BLUE 那 12 vs 13 的落差正是機率偵測在作用——不是把 ground truth 換個標籤。

## 決策與陷阱
- **內建基本目視**：既有 session 的單位身上沒有任何 SENSOR 類裝備，若只認裝備導出的感測器，
  接線後仍是 0 contact。故每個單位都有 organic observation 基準（沿用 `SEED_SENSORS["EO_DAY"]`），
  裝備有更好的感測器才覆蓋——同 #84 油料「惰性滿油」的精神：既有資料免遷移即可運作。
- **掃描節流（interval 5 tick）**：比照 CommsSystem。密集戰場射程內配對可達數百組，每 tick 全掃
  會把 DB 打爆。5 tick＝5 sim 分鐘，在 sim 時間尺度上仍屬即時。
- **同陣營重複觀測收斂 `_best_per_target`**：一個陣營十幾個單位同時看到同一敵人，會對**同一列**
  發十幾次 SELECT+寫。收斂成一筆（取最佳 fidelity）。位置對同一目標各觀測者一致（皆取自熱狀態
  ground truth），故語義不變；且「取最佳」比「看誰最後寫」更具決定性。
- **地形服務掛掉 → LOS 退回可見**，不可退成不可見：否則地形服務一抖，全場忽然集體變瞎。
  與交戰 LOS 同一退化紀律，並有測試釘住。
- **關係矩陣目前取不到**：`sweep` 支援 `relations`（ALLIED 不互相成 contact），但**該局的關係矩陣
  在執行期根本無從取得**——scenario loader 建完就沒持久化，`WargameSession` 也沒有欄位存，
  AI orchestrator 同樣只能用全 HOSTILE 預設（`orchestrator.py:159` 自己註明）。故本卡先留注入點、
  傳 None（＝僅同陣營互不偵測）。**這是 #91 的前置**：友軍要顯示成 Friendly，得先讓執行期拿得到關係。

## 中斷續作指引
- **下一步第一件事**：#90 COP 視角切換。但**先解關係矩陣持久化**（見上），否則 #91 無法做。
- **尚未驗證的假設**：內建目視 4km 對「單位相距數十公尺」的想定顯得過寬（幾乎全場互看得到）；
  若想定改為正常疏開距離再回頭校準 `interval_ticks` 與曲線。
