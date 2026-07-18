# ADR 002：SimCheckpoint 快照儲存策略（inline LONGBLOB + 大小護欄）

日期：2026-07-18　狀態：Accepted

## Context
O1.5 需要把「完整模擬狀態」每 N ticks 序列化存入 `SimCheckpoint.stateBlob`。
原 backlog 疑慮為「>16MB 的快照要分片或改物件儲存」。實測確認：

- `stateBlob` 的實際欄位型別是 **LONGBLOB**（最大 4GB）——欄位本身不是瓶頸。
- 真正限制是 MariaDB **`max_allowed_packet`（本機 = 16MB）**：單一 INSERT 封包超過此值會失敗。
- Phase 1 規模：NFR 上限 500 單位，每單位熱狀態 JSON ~數百 bytes → 未壓縮 ~250KB，
  zstd 壓縮後僅數十 KB。**遠低於 16MB。**

## Decision
Phase 1 採 **inline 儲存**：`serialize_state = canonical_json → zstd`，壓縮後 bytes 直接寫入
`SimCheckpoint.stateBlob`（LONGBLOB）。

加一道**大小護欄**：壓縮後若超過 `MAX_CHECKPOINT_BYTES`（預設 8MB，安全低於 16MB packet 上限），
`save` 拋出明確錯誤，指引改用物件儲存 / 分片（Phase 2）。

不在 Phase 1 實作分片或外部物件儲存——待真實想定的快照接近上限時再啟動。

## Consequences
- 實作簡單、單一交易、無外部依賴；Phase 1 規模綽綽有餘。
- 若未來單位數/單位狀態暴增使壓縮快照逼近 8MB，護欄會先擋下並發出明確訊號，
  此時再依本 ADR 的 Phase 2 路徑（物件儲存 + stateBlob 存參照）處理。
- **相關限制（記入 O1.6/後續）**：checkpoint 目前只含單位熱狀態；要做「checkpoint 後
  快速前滾復原」需一併保存 RNG 狀態（見 O1.1 backlog：DeterministicRNG get/set_state）
  或改以「從 checkpoint 重跑模擬」（O1.6 golden replay）。O1.5 只保證「復原到 checkpoint 當下」。
