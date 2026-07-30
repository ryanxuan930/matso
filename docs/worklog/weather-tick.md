---
task: V2.1 WP-C4b
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-C4b 天氣逐 tick 刷新（C4 收尾）

## 動手前查證：規格的三個前提有兩個已經成立

SPEC_V2 寫「天氣是 session 啟動單一快照」，並把整張 C4 標成 **golden：重錄**。查證後：

**1. 插件的 RPC 早就是 tick-aware 的。** `GetWeatherRequest.sim_tick` 存在，
`WeatherClient.fetch_state(sim_tick)` 也收 tick——**是 core 只呼叫了一次且永遠傳 0**
（`_weather_snapshot()` 的註解自己寫著「v0：整局用啟動快照」）。缺的不是能力，
是「每隔 N tick 再問一次」這件事。

**2. 風場早就在契約裡。** `WeatherCell.wind_ms` / `wind_dir_deg`（proto 欄位 3/4）與
`RawWeather` 都有——**是 core 的 `_cell_from_proto` 只讀了 `cell.effects`，把風丟掉了**。
⚠ 我在 C4c 的 worklog 寫「要先擴 weather 契約」是**錯的**，已更正：不需要改契約，
改的是 core 的讀法。

**3. golden 不必重錄。** 刷新間隔預設 `0` ＝永不刷新 ＝既有的單一快照行為，一個位元都不差。
與 C1/C3/C4a/C4c 同一招。實測 8 個 golden 未動。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/engine/weather_wiring.py | 新增 | `WeatherCache`：每 N tick 重問；**取不到沿用上一份**；失敗也更新時間戳 |
| core/app/weather.py | 修改 | `CellEffects` 加 `wind_ms`/`wind_dir_deg`（0＝無風＝中性） |
| core/app/plugins/weather_client.py | 修改 | `_cell_from_proto` 改收整個 `cell`，把風讀進來 |
| core/app/engine/{engage,sensor}_wiring.py | 修改 | `weather_for` 回呼（有就現讀，無則沿用建構時快照＝既有行為） |
| core/app/sim_params.py | 修改 | `weather_refresh_ticks`（預設 0） |
| core/app/sim_runtime.py | 修改 | `WeatherCache` 注入兩條路徑 + `_weather_snapshot_at(tick)` |
| core/app/adjudication/obscurants.py | 修改 | `drift()`：煙隨風漂移（確定性） |
| core/tests/unit/test_weather_refresh.py | 新增 | 6 條 |
| core/tests/unit/test_obscurants.py | 修改 | +4 條漂移 |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1850 passed, 8 skipped, 4 deselected**
- `core/tests/replay` → **8 passed（golden 未重錄）**
- ruff / mypy(259) / schema-sync / buf / 前端兩閘門 → clean（**無 DB migration、無契約變更**）
- 突變測試 5 個全數被抓：預設就刷新、失敗退回晴天、失敗不更新時間戳、風向不轉 180、漂移不累積

## 決策與陷阱

**`weather_for` 是回呼不是值**（同 C4a `light_for`、C4c `smoke_for`）：`env_for` 的閉包在
Kernel 建構時就固定了，傳一份 `WeatherState` 進去整局就永遠停在那一份——**那正是本卡要修的病**。

**刷新失敗沿用上一份，不退回晴天。** 天氣服務抖一下就讓全場忽然放晴，比慢一拍嚴重得多
（同 `has_los` 服務中斷不致盲的退化紀律）。

**失敗也要更新時間戳**，否則每個 tick 都會重試一次失敗的 RPC，把 tick 預算耗在一個已知
不可用的服務上。

**⚠ 風向是「來向」不是「去向」。** 氣象慣例：北風＝0 度＝從北方吹來，煙要往**南**走，
所以漂移方位角是 `wind_dir_deg + 180`。直接拿它當移動方位角的錯誤**在畫面上完全合理**
（煙有在動），只有對著風標看才會發現方向反了——有專門一條測試釘住。

**漂移是確定性的**：位置由 (初始位置, 風, 經過 tick) 完全決定，不抽任何隨機，
所以不必存每 tick 的位置，也不會擾動 RNG 串流。

**core 不做內插或平滑**：`WeatherCache` 只快取不加工。加工會讓 core 變成第二個氣象模型，
而 core 的原則是「不解讀氣象學」（`weather.py` 模組說明）。

## 中斷續作指引

- **下一步第一件事**：C4 三卡全數完成，往 C7 後勤體系。
- **未竟項**：
  1. **`WeatherMode.REPLAY` 未接**。規格要「Ledger 記每次快照內容，重播時照放」。
     現況：刷新是決定性的（同 tick 問插件得同一份答案，SYNTHETIC 由 seed 派生），
     所以**同一個插件版本**下重播是一致的；但換了插件或用 LIVE 模式就會漂。
     要做得記快照進 Ledger 並在重播時攔截 `fetch`。
  2. **煙幕的 `drift()` 寫好了但沒有掛進查詢路徑**——`load_active_smoke` 仍回原始位置。
     要掛需要決定：漂移後的位置是否寫回 MapFeature（會產生每 tick 的寫入）
     或每次查詢時即時算（要知道生成 tick 與當時的風）。後者較乾淨但要多存兩個欄位。
  3. 想定/UI 都不能設 `weather_refresh_ticks`（目前只能改 SimParams）。
