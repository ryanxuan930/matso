-- AlterTable
-- 該局的 tick 長度（ms 模擬時間）。想定 schema 早就把 `tick_rate_ms` 列為**必填**、
-- loader 也讀得進 `LoadedScenario`，但它只被 `dump.py` 用來做 roundtrip 匯出——
-- **沒有任何一條路把它帶進執行期**，於是每一局都跑系統設定的那個值，
-- 想定寫什麼都沒差（roundtrip 測試仍綠，因為 loader→dump 對得上）。
--
-- NULL＝沿用系統設定的 SimParams.tick_rate_ms（既有局零影響）。
ALTER TABLE `WargameSession`
  ADD COLUMN `tickRateMs` INT NULL;
