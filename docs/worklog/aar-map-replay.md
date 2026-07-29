---
task: WP-D6.1        # SPEC_V2 §6 WP-D6 第 1 項（AAR 地圖重播，量綱修正先行）
status: IN_PROGRESS
started: 2026-07-30T18:40+08:00
updated: 2026-07-30T18:40+08:00
agent: Opus 5
---

# WP-D6.1 AAR 地圖重播（量綱修正先行）

## 目標摘要

AAR 的 `scrubTick` 滑桿要能驅動地圖重繪：拉到任一 tick，地圖顯示該時刻各單位的位置與戰力，
並可用事件書籤跳轉（[JCATS-F p.6]「定位到任一時間點回放檢討」）。
前置是修掉 `reconstruct_states` 的量綱混用（health% vs 戰力點），
否則重播出來的血量是錯的刻度。

## 開工盤點（實際讀完程式碼後的發現）

規格說「`replay.frames` 已有資料未接視覺」。**實際情況比這句嚴重**：

### 1. 量綱混用是真的，而且分岔在兩個分支

`app/aar/replay.py` 的 `UnitState.health` 是**百分比**（預設 100.0），但：

| 來源 | 寫入 | 實際量綱 | 對不對 |
|------|------|---------|-------|
| 個體交戰 `target_health_after` | `health = dec[...]` | 效能% | ✅ |
| 聚合交戰 `initiator_strength_after` / `target_strength_after` | `health = dec[...]` | **戰力點** | ❌ |
| 無後態時 `damage_calc` 遞減 | `health -= damage_calc` | 見下 | ❌ |

活模擬那條路徑**早就分清楚了**（`adjudicator.py:324`）：

```python
health = effectiveness_pct(strength_after / authorized) if authorized > 0 else 0.0
self._hot.update_unit(unit.id, {"strength": strength_after, "health": health})
```

戰力點與效能% 是兩個欄位，後者由前者除以滿編戰力再過效能曲線導出。
AAR 重播是**唯一**把兩者塞進同一個欄位的地方。後果：一個滿編 500 人的營打完剩 420，
AAR 會顯示「health 420」；一個 8 人班會顯示「health 8」。

### 2. `damage_calc` 在聚合事件裡是**雙方損失相加**

`aggregate.py:79` 寫 `damage_calc=a_loss + b_loss`。而 `reconstruct_states:89-91` 的
fallback 是「拿 damage_calc 從**目標**血量扣掉」。兩者相遇＝把攻擊方的損失也算到守方頭上。
這正是 §4 差距總表第 23 列說的「聚合戰損歸帳單側」。
（目前實務上 `*_strength_after` 分支會先命中，所以 fallback 很少走到——但那是巧合不是設計。）

### 3. **`reconstruct_states` 根本沒有被 API 用到**

`GET /sessions/{id}/aar/replay` 回的是 `replay_summary(...)`，內容只有
`frames`（每 tick 有哪些 event type）、`bookmarks`、`total_events`、`max_tick`。
**沒有任何單位狀態。** frames 裡連 unit id 都沒有。

所以「把滑桿接上 MapCanvas」不是純前端工作——**目前後端沒有任何端點能回答
「tick N 時各單位在哪、剩多少戰力」**。`reconstruct_states` 是寫好但沒接線的純函數。

## 計畫

- [ ] 1. 量綱修正：`UnitState` 分出 `strength`（點）與 `health`（%），
      聚合分支比照活模擬用 `effectiveness_pct(strength / authorized)` 導出；
      `authorized` 由呼叫端注入（事件流裡沒有滿編戰力，那是 DB 靜態資料）。
- [ ] 2. `damage_calc` fallback 修正：聚合事件不得把雙方損失和算到單側。
- [ ] 3. 契約先行：`contracts/core_api.yaml` 新增重播狀態的回傳結構 → 驗證 → 再實作端點。
- [ ] 4. 後端端點：回傳指定 tick 的單位狀態（faction-scoped，比照現有 AAR 存取控制）。
- [ ] 5. 前端：`scrubTick` → 地圖重繪；播放/暫停/倍速；書籤跳轉。
- [ ] 6. 測試 + 四道驗證 + e2e。

## 執行紀錄

- `18:40` 讀規格與程式碼，得出「開工盤點」三項。
- `19:0x` **量綱修正**（`77bccbf`）：`UnitState` 分出 strength/health，聚合分支比照活模擬
  用 `effectiveness_pct(strength/authorized)` 導出；順帶修掉聚合 `damage_calc`（雙方損失和）
  被從守方單側扣的錯。5 個測試。
- `19:1x` **位置重建**（`6735677`）：發現移動事件的 lat/lng 全在 `detail` 而非 `ai_decision`，
  該分支自建立以來從未生效。`AarEvent` 補 `detail`，detail 優先。2 個測試。
- `19:3x` **契約先行 + 端點**（`a0ed6f9`）：`/sessions/{id}/aar/replay/states`，
  形狀為「靜態底本 + 逐 tick 差異」；`state_frames` 與 `reconstruct_states` 共用 `_apply_event`。
- `19:5x` **真實資料打臉兩個假設**（尚未 commit，程式碼已在工作區）：
  1. **帳本不是照 tick 排的**。`read_events` 依 seq 取，而實測這場既有推演的**第一筆事件就是
     tick 3700**（WP-E1 之前 SimClock 每次 runner 重建回 0，同一本帳混了兩段時間軸）。
     原本 `reconstruct_states` 一遇 `tick > up_to_tick` 就 break → **立刻回空狀態**。
     加 `sorted_by_tick`（依 (tick, seq)）。
  2. **權威 health 被導出值覆寫**。個體交戰同時記 `target_health_after` 與
     `target_strength_after`，我的 `_set_strength` 會用後者重算覆蓋前者。
     目前兩者同公式所以數值一致，但**記錄值才是權威**，已改為不覆寫並加測試釘住優先順序。
- `20:xx` 前端：`useAarReplay`（差異累加 + 播放/暫停/倍速）、`aar.vue` 重播區、
  `MapCanvas` 加 `fitBounds` prop（AAR 單位常擠在數百公尺內，台灣全景會什麼都看不到）。

## ⛔ 未解：AAR 重播地圖畫面全空（前端最後一哩）

**現象**：AAR 頁的重播地圖一片空白。但同一時刻用 devtools 手動呼叫 `map.resize()`，
單位符號**立刻正確畫出來**（三個陣營、顏色正確、位置正確）。

**已確認正常的**（都在畫面全空的當下量到）：

| 檢查 | 值 |
|------|-----|
| `querySourceFeatures('units')` | 117（資料進來了） |
| 已註冊 icon | 3 個陣營色 SIDC 圖 + 血條 |
| `transform.width/height` | 866 × 350（與容器一致） |
| `getBounds()` | 涵蓋所有單位座標 |
| 各 source `isSourceLoaded` | 全部 true；`areTilesLoaded()` true |
| `queryRenderedFeatures({layers:['units']})` | **0** |
| `map.loaded()` | **false** |

**已排除**（試過但無效）：
- `map.triggerRepaint()` — 無效
- `map.jumpTo(同座標)` — 無效
- 容器尺寸問題 — transform 與容器尺寸一致，不是 0
- 在 `map.on('load')` 內註冊 ResizeObserver — 註冊太晚（load 晚於尺寸變化）
- 改在 map 建立當下註冊 ResizeObserver（**已保留在程式碼裡**，做法本身正確）— 單獨仍無效
- 首次拿到單位資料後 rAF 內 `resize()` 一次 — 無效（已移除，不留無效的 workaround）

**目前推測**（未證實）：只有 `resize()` 有效而 `triggerRepaint`/`jumpTo` 無效，指向
**painter / GL viewport 在零尺寸下初始化後未再重新設定**；`resize()` 會呼叫
`painter.resize()` 重配 framebuffer，另兩者不會。但 transform 已是正確尺寸，
與這個推測不完全相符，**所以沒有據此下手改**。

**注意**：`MapCanvas.vue` 本來就有一行 `setTimeout(() => map?.resize(), 300)`
（在 `map.on('load')` 內、`loaded.value = true` 之前），可見前人遇過同類問題。
那行在本案例中沒有解決問題。

**下一步建議**（給接手的人，別重跑上面已排除的）：
1. 在 `syncUnits` 的 `setData` 前後印出 `map.loaded()` / `map._styleDirty` / `map._sourcesDirty`，
   確認 render loop 到底有沒有被排程。
2. 若 render loop 沒跑，查 MapLibre v5 的 `_triggerFrame` 是否因為某個條件被抑制。
3. 對照 COP 頁（可正常顯示）逐項比對兩者的初始化順序差異——最大差別是
   **COP 的地圖佔滿視窗且在頁面掛載時就有尺寸，AAR 的地圖在 `v-else` 子樹裡、
   且資料載入完成後才出現**。
4. 也可考慮繞過：AAR 頁先渲染地圖容器（不等 loading），資料到了再餵 props。

## 中斷續作指引

- **後端全部完成並已推送**（`77bccbf` / `6735677` / `a0ed6f9`），三個既有錯已修並有測試。
  最後一次的兩項修正（tick 排序、權威 health）在工作區，測試已綠待 commit。
- **前端寫完但卡在上述渲染問題**：`useAarReplay` / `aar.vue` 重播區 / `MapCanvas.fitBounds`
  三者 lint/typecheck/build 皆綠，資料管線經 devtools 驗證正確（117 筆特徵、座標與圖示都對）。
- 動手前先讀上面的「已排除」表，那些路都走過了。
