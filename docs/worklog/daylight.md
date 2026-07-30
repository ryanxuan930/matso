---
task: V2.1 WP-C4a
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C4a 晝夜與照明

## 目標摘要

[JCATS-A p.7]：晝夜影響運動與偵測。`SimClock` 早就有模擬時刻，缺的只是「把時刻翻成光照
等級」與「光照對誰有什麼影響」兩層。C4 的另外兩塊（天氣 tick 化、煙幕）另開卡。

## 與規格不同的裁決

**1. golden 不必重錄。** SPEC_V2 把整張 C4 標成「golden：重錄（天氣快照語意變更）」。
那句話是針對 C4b 的天氣；晝夜這一段只要**中性預設**守住就不必重錄——未宣告日出日落 →
光照恆為 DAY → DAY 的三個係數全 1.0。與 WP-C1/C3 用過的是同一招。已實測 8 個 golden 未動。

**2. 日出日落是想定參數，不是天文計算。** 真的算太陽仰角要緯度、日期、時區與大氣折射，
而這裡不需要那個精度：兵推要的是「白天打還是晚上打」。想定作者給兩個時刻，比一個他無法
覆寫的天文公式有用得多（夜訓本來就會挑時間）。

**3. `night_capable` 掛裝備不掛單位。** 掛單位的話一個連配一支夜視鏡就整連免罰，
而那正是夜戰最關鍵的差別（有沒有配發到人）。感測器與行軍是**兩個獨立旗標**：
同一個連可以有夜視鏡卻沒有駕駛用夜視。

**4. 「我看多遠」與「我多好被看到」是兩個軸。** `optical_range_modifier` 吃夜視、
`concealment_modifier` 不吃（那是環境，對雙方成立）。合成一個數字會讓「我方有夜視」
同時變成「敵人比較容易看見我」。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/adjudication/daylight.py | 新增 | 純函數：`light_at`（含跨午夜）、三個係數、`DayNight` |
| core/app/engine/daylight_wiring.py | 新增 | `LightClock`（SimTime→光照）+ `read_day_night`（壞資料→未宣告） |
| core/app/intel/sensor.py | 修改 | `SensorProfile.night_capable`（缺鍵→False）+ `DetectionEnv.light_modifier` |
| core/app/engine/sensor_wiring.py | 修改 | `make_detect_env(light_for=…)`：**每次呼叫現讀**當前光照 |
| core/app/engine/movement.py | 修改 | 夜間行軍倍率（未宣告→`self._light is None`→整段跳過） |
| core/app/sim_runtime.py | 修改 | 開局快照 `LightClock`，注入偵測與移動 |
| contracts/scenario.schema.json、scenario/{loader,dump}.py、models/tables.py、db/prisma | 修改 | `day_night` 六層（migration `20260730140000_c4a_day_night`） |
| core/tests/unit/test_daylight.py | 新增 | 16 條 |

## 測試證據

- `uv run pytest -q` → **1831 passed, 8 skipped**；`core/tests/replay` → **8 passed（golden 未重錄）**
- ruff / mypy(256) / schema-sync(23 tables / 225 columns) / 前端 lint+typecheck → clean
- 活 DB：先 `mariadb-dump` 備份（73.8 MB）→ `migrate:deploy`
- 突變測試 6 個全數被抓（**其中一個是靠突變測試才發現我的測試不夠**，見下）

## 決策與陷阱

**⚠ 我的跨午夜測試第一版是假的，突變測試才抓出來。**
把 `_within` 換成單純的 `start <= m < end`（跨午夜的經典錯法）之後，測試**照樣全綠**。
原因：我選的案例是「日落 22:00、日出 05:00」，看起來像跨午夜，但被判定的**白天區間**
（05:30–21:30）根本沒有繞回去——繞的是夜晚，而夜晚是 else 分支。
真正會分出對錯的是**曙暮光帶橫跨午夜**：日出 00:15 → 曙光帶 23:45→00:45。
補上那個案例後突變立刻轉紅。

**光照每 tick 由時刻導出，不存熱狀態。** 光照是時刻的函數，不是可獨立變動的狀態；
存進熱狀態就會出現「熱狀態說 NIGHT、時鐘說中午」的可能，checkpoint 還會把它一起存下來。
照明彈那種**局部且短暫**的覆寫才需要狀態（本卡未做）。

**`light_for` 是回呼不是值。** sweep 跨 tick 重用同一個 `env_for`，傳一個等級進去會讓
整局停在建立時的那一刻。

**未宣告時消費端整段跳過**（`LightClock.declared` / `movement._light is None`），
而不是「算出來剛好是 1.0」——一次都不算更省，也更不會在改係數時意外動到既有局。

## 中斷續作指引

- **下一步第一件事**：C4b（天氣 tick 化 + REPLAY 模式）或 C4c（煙幕）。
- **未竟項**：
  1. **照明彈（ILLUM）未做**——需要局部且短暫的光照覆寫（熱狀態實體），
     且要接上 WP-C10 的火力任務彈種。`ILLUM_DURATION_TICKS` 常數已備好。
  2. **前端沒有任何晝夜呈現**：COP 不顯示當前模擬時刻/光照，地圖也沒有夜間配色。
     `LightClock.minute_at()` 已備好供顯示。
  3. **想定編輯器不能設 `day_night`**（同 `allow_fratricide`，第二份手寫白名單問題）。
  4. C4b/C4c 未做（天氣仍是整局單一快照；無煙幕）。
