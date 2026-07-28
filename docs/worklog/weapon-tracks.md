---
task: "#95 攻擊時繪製武器軌跡"
status: DONE
started: 2026-07-28T18:30+08:00
updated: 2026-07-28T19:10+08:00
agent: Opus 5
spec: SPEC_FULL §13.3（COP 顯示）、§7.1（交戰裁決——本卡只讀其結果）
---

# #95 武器軌跡

## 紅線：純顯示，絕不回頭影響裁決
軌跡由**後端已裁決完**的 `ENGAGEMENT_RESOLVED` 事件驅動；前端不做任何命中/射程/視線判定，
只把結果畫出來。`adjudication/` 仍是唯一權威。

## 關鍵設計：座標不從事件帶，改用「client 本來就看得到的東西」
最直覺的做法是讓後端在事件裡夾帶射手/目標座標。**查過之後否決了**：

`state/broadcaster.py` 的 `build_event_envelope` **不設 faction 受眾標籤**，而
`stream/faction_filter.is_visible` 只在有標籤時才過濾 → 目前 Kernel 發出的交戰事件是**廣播給
所有陣營**的。若在其中夾帶座標，等於把全場每一次交戰的精確位置送給每一個陣營，是嚴重的迷霧洩漏。

故端點座標一律由前端從**已經合法可見的圖徵**解析：我方/友軍單位（id）+ 已偵獲的 contact。
- 解析不到任一端 → **不畫**（迷霧下的正確行為，而非畫一條指向未知處的線）。
- 代價：陣營視角下若射手或目標未被該陣營看見，該次交戰不會有軌跡。全局視角（白軍觀戰，
  也是目前主要的使用方式）則完整可見。

**副作用是好的**：本卡完全不動後端 → 零 golden 風險、零迷霧風險。

## 交付
| 檔案 | 動作 | 說明 |
|---|---|---|
| `platform/app/pages/session/[id]/cop.vue` | 修改 | `weaponTracks` + 事件 watcher（只吃新事件，游標式）+ `weaponTrackFc`（含淡出 opacity）+ unmount 清計時器 |
| `platform/app/components/map/MapCanvas.vue` | 修改 | `weapon-track` 圖層：HIT 亮橘實線、MISS 灰藍虛線，透明度由 feature 帶 |

- REJECTED **不畫**（根本沒射出去）。
- 計時器只在有軌跡時跑，清空即停（不閒置空轉）。
- 事件緩衝被裁切（`MAX_EVENTS`）時游標重置，避免漏處理。

## 同批一併修掉的問題
**全局視角單位重複渲染**（#90 我引入的）：god view 既以 ground truth 畫出全部單位（own），
又疊上各陣營偵測到的 contacts（實測 36 own + 70 contact），同一個單位會出現兩次。
全局視角本就看得到全部，不需要再疊偵測結果 → 無觀測者時不畫 contacts。

## 測試證據
- gates：`pytest` **1088 passed / 8 skipped**（本卡未動後端）、前端 lint + typecheck 綠。
- **瀏覽器實測**（複製局，注入四種已裁決事件）：

  | 注入 | 結果 |
  |---|---|
  | HIT（雙方皆可見） | ✅ 畫出，opacity 0.95 |
  | MISS（雙方皆可見） | ✅ 畫出，opacity 0.5 |
  | HIT（目標解析不到） | ✅ **不畫**（迷霧） |
  | REJECTED | ✅ **不畫**（未射出） |

- 淡出實測：0.3s→**0.95**、1.8s→**0.58**、4.4s→**0.11** 後消失。
- 截圖同時可見 #94 的血條與本卡的橘色 HIT 軌跡。

## 排查紀錄（避免下次誤判為 bug）
初測在 z7 時「HIT 沒畫出來」，一度以為是邏輯錯。實際是那條軌跡兩端相距僅 **34m**，
在該縮放級別下**小於一個像素**，被 MapLibre 的向量切圖丟棄；同批 350m 的 MISS 則正常。
放大到 z15 後兩條都正確渲染。**不是 bug**，但值得記著：低縮放下近距離交戰的軌跡看不到。

## 未做
- 彈道飛彈的拋物線軌跡：目前一律畫直線。後端 `adjudication/trajectory.py` 有拋物線淨空判定，
  但未輸出弧線幾何；要畫真弧線需後端提供取樣點（且同樣受上述迷霧洩漏限制）。
- ~~交戰事件本身缺 faction 受眾標籤~~ → **已於同批解決**（見下）。

## 追加：事件受眾標籤（本檔提出的問題，隨後即修）
Kernel 發出的事件原本**完全沒有受眾標籤**，`is_visible` 因而一律放行 → 每個陣營都收得到
他方的交戰與偵測事件。修法：

- `faction_filter.is_visible` 支援 `factions` **清單**受眾（一次交戰同時關乎射手與目標兩方；
  原本的單一 `faction` 欄位保留給 API 端 `publish_event`，向後相容）。
- `broadcaster.event_audience(event, faction_for)` 導出受眾：
  **`observer_faction` 優先且為唯一受眾**（SENSOR_CONTACT）→ 否則取所涉單位的陣營 → 都沒有則
  為全域事件（不標）。
- `RedisBroadcaster` 接受 `faction_for`；sim_runtime 注入 `SensorResolver.faction_for`（已現成）。

**最關鍵的一條**：SENSOR_CONTACT 的 `target_id` 是「被偵測到的單位」。若照 unit 推導受眾，
等於通知對方「你被發現了」——**比原本的全廣播更糟**。故 `observer_faction` 必須優先。有測試釘住。

**未注入 `faction_for`（測試/合成想定）→ 不標受眾 ＝ 行為與加此功能前完全相同**，
故 golden 與既有測試零影響。8 條新測試；pytest 1096 passed / 8 skipped。
