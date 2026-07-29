---
task: WP-D6.1        # SPEC_V2 §6 WP-D6 第 1 項（AAR 地圖重播，量綱修正先行）
status: DONE
started: 2026-07-30T18:40+08:00
updated: 2026-07-30T21:10+08:00
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

- [x] 1. 量綱修正：`UnitState` 分出 `strength`（點）與 `health`（%），
      聚合分支比照活模擬用 `effectiveness_pct(strength / authorized)` 導出；
      `authorized` 由呼叫端注入（事件流裡沒有滿編戰力，那是 DB 靜態資料）。
- [x] 2. `damage_calc` fallback 修正：聚合事件不得把雙方損失和算到單側。
- [x] 3. 契約先行：`contracts/core_api.yaml` 新增重播狀態的回傳結構 → 驗證 → 再實作端點。
- [x] 4. 後端端點：回傳指定 tick 的單位狀態（faction-scoped，比照現有 AAR 存取控制）。
- [x] 5. 前端：`scrubTick` → 地圖重繪；播放/暫停/倍速；書籤跳轉。
- [x] 6. 測試 + 四道驗證 + e2e。

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

## ✅ 已解：地圖「畫面全空」是 harness 假象，不是程式錯誤

**結論先講**：AAR 重播地圖**本來就是好的**。所謂「畫面全空」完全是 in-app 瀏覽器
harness 造成的假象，程式碼一行都不用為它改。

### 根因

| 量測（在「畫面全空」的當下） | 值 |
|---|---|
| `document.visibilityState` | **`hidden`** |
| `requestAnimationFrame` 觸發次數（閒置 2 秒） | **0** |
| 同上，**截一張圖之後** | **4** |
| map `render` 事件次數，截圖後 | 0 → 3 |

in-app 瀏覽器把頁面回報為 `hidden`；瀏覽器會暫停隱藏頁面的 `requestAnimationFrame`，
而 **MapLibre 的 render loop 完全靠 rAF**。所以地圖永遠不繪，除非有東西強制合成畫面。
**截圖就是那個東西。**

### 這解釋了先前每一個矛盾的觀察

- 「`map.resize()` 有效」——**其實是我緊接著截的那張圖有效**。resize 只是把狀態標髒，
  真正讓它畫出來的是截圖強制觸發的那幾個 frame。
- 「`triggerRepaint` / `jumpTo` 無效」——因為我用 JS 讀結果、中間沒有截圖，
  render loop 從頭到尾沒跑過。**我一直在測量一個被凍結的 render loop。**
- 「COP 的地圖看起來正常」——因為在 COP 我每一步都在截圖。

### 教訓（與右鍵選單那次同一類）

**in-app 瀏覽器 harness 不能用來驗證 MapLibre 的任何東西。**
合成事件打不進它的事件層（`ctxmenu`/`coords` 那兩支 e2e 的由來），
現在再加一條：**畫面有沒有真的繪出來也測不了**，因為 rAF 是停的。
唯一可信的是真 Playwright。

另一個給自己的提醒：連續五次「改一下→看看好了沒」而沒有先量到根因，就是在猜。
真正解開它的是**直接量 rAF 有沒有在跑**——一個早該問的問題。

### `.env` 的坑比之前記的更大（順帶更正）

G1a 的 worklog 記「`platform/.env` 會讓『離線：無 tile server』那條測試在本機必紅」。
**實際範圍大得多**：該檔還設了 `NUXT_PUBLIC_API_BASE`，會蓋掉 Playwright 傳給 Nuxt 的
`http://localhost:8100`，於是 e2e 前端連到 **docker 後端（:8000）**——那裡沒有 `e2e-orders`
這局，`單位列表載入真單位` 直接拿到 0 個單位。本卡一度以為是自己弄壞的。

**主樹跑出來的 e2e 數字整份不可信**，不是只有底圖那條。權威比對一律在 `.env`-free worktree。

### 證明

新增 `platform/e2e/aar-replay.spec.ts`（真 Playwright，2 條皆綠）：

1. **單位符號實際畫在畫布上**——斷言 `queryRenderedFeatures({layers:['units']}).length > 0`。
   刻意不用「source 有幾筆特徵」，因為 symbol 要經過 placement 才會真的出現，
   而 placement 正是 harness 裡恆為 0 的那一步。
2. 拖時間軸改變畫面、書籤可跳轉。

### 保留的兩處 MapCanvas 改動（各有各的正當理由，與上述假象無關）

- `fitBounds` prop：AAR 的單位常擠在數百公尺內，用預設的台灣全景會什麼都看不到。
  實測生效後比例尺由 50 km 變 300 m。
- `ResizeObserver`：MapLibre 自己只聽 window resize，容器在視窗沒變的情況下改變尺寸
  它不會知道。**註解已改寫**——原本那段把它說成是「畫面全空」的解方，那是錯的診斷，
  不能留在程式碼裡誤導下一個人。

## 中斷續作指引

- **後端完成並已推送**：量綱（`77bccbf`）、位置來源（`6735677`）、契約+端點（`a0ed6f9`）、
  tick 排序與權威 health（`8303559`）。共修掉 5 個既有錯，全部有測試釘住。
- **前端完成並經真 Playwright 驗證**：`useAarReplay`、`aar.vue` 重播區、
  `MapCanvas.fitBounds` / ResizeObserver。e2e 共 9 支（本卡 +2）。
- **剩餘（本卡未做，屬 WP-D6 其餘兩項）**：D6.2 統計對帳（命中率分母語意）、
  D6.3 匯出管線（串流分頁 CSV/SQL + bundle）。
- **驗證紀律新增兩條**：
  1. MapLibre 的「畫面有沒有出現」一律用 Playwright，不要用 in-app 瀏覽器——
     它的頁面是 hidden、rAF 停擺，看到的空白不代表壞掉。
  2. e2e 一律在 `.env`-free worktree 跑。`platform/.env` 的 `NUXT_PUBLIC_API_BASE`
     會把前端指到 docker 後端，主樹的 e2e 結果沒有參考價值。
- **e2e 對照（`.env`-free worktree）**：

  | | passed | failed |
  |---|---|---|
  | G1b 收卡時 `fe17afe` | 16 | map:71 / orders:24 / orders:47 / smoke:11 |
  | 本卡 `HEAD` | **18** | **完全相同的四條** |
