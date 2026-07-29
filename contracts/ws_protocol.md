# WebSocket 協定 v1（SPEC_FULL §16.2）

連線：`WS /api/v1/sessions/{id}/stream?token=<jwt>`
伺服器依 token 的 faction scope 過濾一切訊息（fog of war 為後端責任）。

## 受眾標籤（fog of war 的傳輸層閘門）

信封可帶下列頂層欄位；`stream/faction_filter.is_visible` 依此決定是否送給某 client。

| 欄位 | 意義 |
|------|------|
| （無） | 全域訊息，所有人可見（`SESSION_CONCLUDED`、`CLOCK`…） |
| `faction: F` | 單一受眾（API 端 `publish_event`）；全知角色亦收得到 |
| `factions: [F…]` | 受眾清單（一次交戰同時關乎射手與目標兩方）；全知角色亦收得到 |
| `exclusive: true` | **關掉全知旁通**：只有 `factions` 內的陣營收得到，全知角色**不**收 |

`exclusive` 是給**每陣營投影**用的（WP-C5 的 STATE_DIFF）：同一 tick 會發出「每個陣營各一份
已投影的副本」＋「一份真實副本」。若全知角色照舊旁通，就會同時收到 N 份互相矛盾的副本。
真實副本以 `factions: []` 標記——沒有任何作戰陣營在清單內，只有全知旁通收得到。

## Envelope

```json
{ "v": 1, "seq": 10231, "tick": 4211, "type": "STATE_DIFF", "payload": {} }
```

- `seq`：per-session 單調遞增的**傳輸層計數器**（Redis INCR；與 Ledger seq 為不同計數器）。
  ⚠ 不耐 Redis 遺失：Redis 清空時 ring buffer 同滅，崩潰復原流程會呼叫
  `RedisBroadcaster.reset_stream()` 讓新串流自 seq=1 重新起算。
- 重連補償：client 於 `HELLO` 帶 `last_seq`；server 從 Redis ring buffer（最近 5000 條）補送。
  `last_seq` **不在 ring buffer 現存 seq 範圍內**（缺口過大「或 seq 倒退」，即崩潰復原後的新串流）
  → 一律回 `RESYNC_REQUIRED`，client 走 `GET /sessions/{id}/state` 全量重同步。
  O4.3 實作 WS server 時 MUST 以「範圍檢查」而非「差值檢查」判斷（O1.7/R7）。
- **RESYNC 閉環（WP-E3）**：`GET /sessions/{id}/state` 回**單一原子快照**
  （`StateSnapshotView`：units＋contacts＋map features＋relations＋`tick`＋`last_seq`）。
  client MUST 以它**一次性**重建全部狀態，並在套用後**丟棄 `seq ≤ last_seq` 的 STATE_DIFF**
  ——server 送出 RESYNC 後**不會停止推播**（pub-sub 早在送 RESYNC 前就已訂閱），
  快照回來時可能已比某些 diff 舊；不去重的話舊快照會蓋掉新位置。
  快照的迷霧過濾與 `/units`、`/intel`、`/map-features`、`/relations` **逐項相同**
  （後端複用同一份 handler，非另寫一套）。

## 訊息型別

| type | 方向 | payload 摘要 |
|------|------|--------------|
| `HELLO` | C→S | `{ last_seq: int \| null }` |
| `WELCOME` | S→C | `{ session, faction, resumed_from_seq }` |
| `RESYNC_REQUIRED` | S→C | `{ reason }` |
| `STATE_DIFF` | S→C | `{ units: [{id, changed_fields...}] }`（僅變動欄位；**每陣營投影**，見下） |
| `EVENT` | S→C | Ledger 事件的 faction-safe 投影 |
| `INTEL_UPDATE` | S→C | `{ contacts: [IntelContact 投影] }` |
| `WEATHER_UPDATE` | S→C | 受影響 cells 的 effects |
| `CLOCK` | S→C | `{ tick, compression, session_state }`（心跳，每秒） |
| `AI_TASK_UPDATE` | S→C | `{ task_id, status, result? }` |
| `ERROR` | S→C | `{ code, message }` |

## STATE_DIFF 的每陣營投影（WP-C5）

伺服器對每個 tick 的變動發出 **N+1 份**信封：每個有單位的陣營各一份（`factions:[F]` +
`exclusive:true`），外加一份真實副本（`factions:[]`，只有全知角色收得到）。
陣營副本已在後端套用兩層 fog of war：

1. **可見集**：只含該陣營自己與其**盟軍**的單位（與 `GET /units` 同一份規則）。
   WP-C5 之前 STATE_DIFF 沒有任何受眾標籤，等於把敵軍即時座標廣播給所有連線的 client。
2. **位置凍結**（SPEC_FULL §6.2）：通聯非 ONLINE 的單位，`lat`/`lng` 換成其**最後一次位置回報**
   並附 `stale_since_tick`；若尚無任何回報則**移除** `lat`/`lng`（client 保留最後已知值，
   不得以 null 清空）。OFFLINE ＝不再回報（凍結）；DEGRADED ＝降頻回報（落後）。

client 對凍結單位會**每 tick 收到同一組座標**（投影是無狀態的，不記「上次送過什麼」）——
重複套用無害，因為 STATE_DIFF 語意是覆寫。

## 背壓規則（HOW_TO §8）

- per-client send queue 上限 1000 則；溢出 → 斷線並要求全量重同步。禁止無限緩衝。
