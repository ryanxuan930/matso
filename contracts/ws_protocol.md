# WebSocket 協定 v1（SPEC_FULL §16.2）

連線：`WS /api/v1/sessions/{id}/stream?token=<jwt>`
伺服器依 token 的 faction scope 過濾一切訊息（fog of war 為後端責任）。

## Envelope

```json
{ "v": 1, "seq": 10231, "tick": 4211, "type": "STATE_DIFF", "payload": {} }
```

- `seq`：per-session 單調遞增（與 Ledger seq 同源）。
- 重連補償：client 於 `HELLO` 帶 `last_seq`；server 從 Redis ring buffer（最近 5000 條）補送；
  超出範圍 → 回 `RESYNC_REQUIRED`，client 走 `GET /sessions/{id}/state` 全量重同步。

## 訊息型別

| type | 方向 | payload 摘要 |
|------|------|--------------|
| `HELLO` | C→S | `{ last_seq: int \| null }` |
| `WELCOME` | S→C | `{ session, faction, resumed_from_seq }` |
| `RESYNC_REQUIRED` | S→C | `{ reason }` |
| `STATE_DIFF` | S→C | `{ units: [{id, changed_fields...}] }`（僅變動欄位） |
| `EVENT` | S→C | Ledger 事件的 faction-safe 投影 |
| `INTEL_UPDATE` | S→C | `{ contacts: [IntelContact 投影] }` |
| `WEATHER_UPDATE` | S→C | 受影響 cells 的 effects |
| `CLOCK` | S→C | `{ tick, compression, session_state }`（心跳，每秒） |
| `AI_TASK_UPDATE` | S→C | `{ task_id, status, result? }` |
| `ERROR` | S→C | `{ code, message }` |

## 背壓規則（HOW_TO §8）

- per-client send queue 上限 1000 則；溢出 → 斷線並要求全量重同步。禁止無限緩衝。
