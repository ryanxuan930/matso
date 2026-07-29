# ADR 007 — Rollback 的 Ledger 語義：世代標記，不做實體截斷

- 狀態：**Accepted**（2026-07-29）
- 決策者：Opus 5（WP-E1 實作）
- 影響範圍：`state/checkpoint.rollback`、`state/ledger`、`aar/events`、`api/control`

## 背景

SPEC_V2 §6 WP-E1 的驗收條文寫著：

> rollback 後 Ledger 尾段正確**截斷**（hash chain 重錨定，設計須記 ADR）

需求本身是對的——回滾之後，被棄世代的事件不該再被當成「發生過的事」：AAR 的戰損統計會
把同一場交戰算兩次、敘事會出現「打完又沒打」的段落、重播會走一條已不存在的時間軸。

但「實體截斷」（DELETE 那些列）與系統既有的三道設計正面衝突，而且那三道設計都是刻意的：

1. `LedgerWriter` 的**唯一對外能力是 `append`**——刻意不提供 update/delete（`state/ledger.py`）。
2. DB 權限層 `ops/tools/grant_ledger_readonly.sql` 對應用帳號 `matso_app`
   `GRANT SELECT, INSERT` 並 **`REVOKE UPDATE, DELETE`**。截斷需要提權。
3. `verify_chain()` 的**第一條檢查**就是「seq 自 0 起連續遞增，缺號＝被刪」。
   實體截斷後鏈必然驗證失敗，除非連帶重寫整條鏈的 hash——而那正是竄改者會做的事。

第 3 點是關鍵：這條 hash chain 的存在理由是**防竄改**（SPEC_FULL §15.3）。
若系統自己提供一條「刪掉尾段再重新錨定」的合法路徑，那麼「鏈驗證通過」就不再能證明
「沒有東西被刪過」——防竄改性歸零。兵推的事後檢討與究責正是建立在這個性質上。

## 決策

**不刪任何一列。改以「世代標記」達成同一個效果。**

- `rollback()` 追加的 `ROLLBACK` 事件在其 `detail` 記下被棄世代的 seq 區間：
  `superseded_from_seq = 目標快照的 ledgerSeq + 1`、`superseded_to_seq = 回滾當下的鏈尾 seq`。
  （`detail` 是非證據性診斷欄，刻意不入 hash chain——O1.7/R8。）
- `state/ledger.superseded_seqs(rows)` 掃出所有 ROLLBACK 事件的區間聯集，
  即「已不屬於現行時間軸」的 seq 集合。多次回滾直接聯集；後一次把前一次的 ROLLBACK
  事件也蓋掉是正確的（它本來就在被棄的那一段裡）。
- **需要現行時間軸的消費端過濾**：`aar/events.read_events` 已接上。
- **需要完整歷史的消費端不過濾**：稽核直接查 `TacticalEventLog`；`verify_chain` 仍驗完整鏈。

「hash chain 重錨定」在本設計中由 ROLLBACK 事件本身擔任錨點——它是新舊世代的分界，
其 `prevHash` 仍鏈回被棄世代的最後一筆（物理鏈連續），但語義上宣告了那一段作廢。

同時 rollback 一併回捲 **Order 狀態**（快照的 orders 區段）：不然回到 tick T 之後，
T 之後被打完的交戰令仍是 COMPLETED，那場交戰再也不會發生。回滾點之後才下的令標
`CANCELLED` 而非刪除——「誰在什麼時候下了什麼令」同樣是稽核紀錄。

## 後果

**得到**
- 防竄改性完整保留：鏈永遠連續，`verify_chain` 通過即可證明無刪除。
- 稽核看得到「白軍在什麼時候把什麼段落回滾掉了」——這比看不到更有價值（回滾本身就是
  推演過程的一部分，統裁的介入應該留痕）。
- DB 權限層不必為應用帳號開 DELETE。
- 不需要 migration（區間存在既有的 `detail` JSON 欄）。

**代價**
- 帳本會累積被棄世代的事件，長局多次回滾後體積偏大。以目前規模（單局事件數量級 10⁴–10⁵）
  不成問題；真要清理應走「封存時歸檔」而非執行期刪除。
- **每個消費端都必須自己決定要不要過濾**。忘記過濾 = 統計重複計算，且不會有任何錯誤訊息。
  緩解：`read_events` 是 AAR 的單一入口且已接上，並有測試釘住
  （`test_aar_excludes_superseded_events`）。新增消費端時必須做同樣的決定。
- `superseded_seqs` 以 `range()` 展開區間，極端情況（回滾跨越數百萬事件）會吃記憶體。
  若真的遇到，改為區間比對即可（介面不必變）。

## 替代方案（未採用）

- **實體 DELETE + 重算 hash chain**：規格字面要求。否決理由見背景第 3 點——它會摧毀
  這條鏈唯一的價值。
- **`TacticalEventLog` 加 `generation` 欄位**：語義更明確、查詢可下推到 SQL。但需要
  migration、要改 `verify_chain` 的錨定邏輯、且每筆事件都要帶世代號（寫入端也得改）。
  目前一局的回滾次數是個位數，用 ROLLBACK 事件的區間表達成本低得多。真的需要
  SQL 下推過濾時再升級，屆時 `superseded_seqs` 的介面不變。
