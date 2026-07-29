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

- `18:40` 讀 SPEC_V2 §6 WP-D6、`app/aar/replay.py`、`app/api/aar.py`、`aar.vue`（153 行）、
  `adjudication/aggregate.py`、`adjudication/adjudicator.py`、`effectiveness.py`。
  三項發現如上。判斷：規格把這張卡描述成「接視覺」，實際上**後端缺一個端點**，
  範圍比字面大。仍屬同一張卡（沒有端點就沒有重播），不另開卡。

## 中斷續作指引

- 本卡剛開工，尚未動任何程式碼。上面「開工盤點」三項是讀完程式碼的結論，可直接採信續做。
- 動手順序有依賴：**量綱修正必須在端點之前**（否則端點會把錯的刻度固化進契約）。
- `effectiveness_pct` 在 `app/adjudication/effectiveness.py`；重播要與活模擬用**同一個函數**，
  不要在 AAR 另寫一份曲線。
